"""WebSocket endpoint of an interview room.

Protocol (JSON text frames, ``t`` is the message type)

client -> server
    hello   {sid, name?, resume?: {epoch, revs: {doc: rev}}}   first message
    op      {doc, rev, id, op}        an edit made against revision ``rev``
    cursor  {doc, a, h}               selection (anchor/head index) or a=null
    set     {key: 'language', value}
    timer   {action: start|pause|reset}
    name    {name}
    run     {}                        run the room's code with the room's stdin
    check   {doc, rev, hash}          "do we still agree on the text?"
    ping    {}

server -> client
    init    full state, or (resumed) the operations missed while disconnected
    ack     {doc, rev, id}            your operation is now revision ``rev``
    op      {doc, rev, op, by}        somebody else's (transformed) operation
    resync  {doc, rev, text, reason}  throw away local state for this document
    cursor / join / leave / peer / state / run / error / pong
"""
import asyncio
import json
import logging
import secrets

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from . import execution, ratelimit, sandbox
from .ot import OTError, TextOperation
from .rooms import (
    DOCUMENT_KEYS,
    ROOM_NAME_RE,
    DocumentTooLong,
    Peer,
    StaleRevision,
    clean_name,
    manager,
    now_ms,
)

logger = logging.getLogger(__name__)

MAX_FRAME_CHARS = 600 * 1024
MAX_OUTBOX = 2000
MAX_MESSAGES_PER_SECOND = 400
RUN_OUTPUT_CHARS = 64 * 1024


def _clip_output(text):
    if len(text) <= RUN_OUTPUT_CHARS:
        return text, False
    return text[:RUN_OUTPUT_CHARS], True


class RoomConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room = None
        self.peer = None
        self.outbox = asyncio.Queue()
        self.writer = None
        self.closing = False
        self._window_start = 0.0
        self._window_count = 0
        if not ROOM_NAME_RE.match(self.room_name):
            await self.close(code=4404)
            return
        await self.accept()
        self.writer = asyncio.ensure_future(self._write_loop())

    async def disconnect(self, close_code):
        self.closing = True
        room, peer = self.room, self.peer
        self.room = None
        self.peer = None
        if room is not None and peer is not None and room.peers.get(peer.id) is peer:
            del room.peers[peer.id]
            room.broadcast({'t': 'leave', 'id': peer.id})
            await manager.left(room)
        if self.writer is not None:
            self.writer.cancel()

    # ---- outgoing ----

    def push(self, message):
        self.push_text(json.dumps(message))

    def push_text(self, text):
        """Queue a frame. Never blocks, so broadcasts stay atomic."""
        if self.closing:
            return
        if self.outbox.qsize() >= MAX_OUTBOX:
            # The client is not reading; drop it. It resyncs when it reconnects.
            self.closing = True
            asyncio.ensure_future(self._force_close(4408))
            return
        self.outbox.put_nowait(text)

    async def _write_loop(self):
        try:
            while True:
                text = await self.outbox.get()
                await self.send(text_data=text)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug('websocket writer stopped', exc_info=True)

    async def _force_close(self, code):
        try:
            await self.close(code=code)
        except Exception:
            pass

    def _error(self, code, message):
        self.push({'t': 'error', 'code': code, 'message': message})

    # ---- incoming ----

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None or len(text_data) > MAX_FRAME_CHARS:
            await self.close(code=1009)
            return
        loop_time = asyncio.get_event_loop().time()
        if loop_time - self._window_start >= 1.0:
            self._window_start = loop_time
            self._window_count = 0
        self._window_count += 1
        if self._window_count > MAX_MESSAGES_PER_SECOND:
            await self.close(code=1008)
            return
        try:
            message = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get('t')
        if self.peer is None:
            if kind == 'hello':
                await self._hello(message)
            return
        handler = self._HANDLERS.get(kind)
        if handler is not None:
            handler(self, message)

    async def _hello(self, message):
        room = await manager.get(self.room_name)
        if self.closing:
            return
        if len(room.peers) >= settings.ROOM_MAX_PEERS:
            self.closing = True
            await self.send(text_data=json.dumps(
                {'t': 'error', 'code': 'room_full', 'message': '房间人数已满'}))
            await self.close(code=4409)
            return

        sid = str(message.get('sid') or '')[:64] or secrets.token_hex(8)
        user = self.scope.get('user')
        authenticated = bool(user is not None and getattr(user, 'is_authenticated', False))
        name = clean_name(message.get('name'))
        if not name and authenticated:
            name = clean_name(user.get_username())
        if not name:
            name = '访客%02d' % secrets.randbelow(100)
        headers = dict(
            (key.decode('latin-1').lower(), value.decode('latin-1'))
            for key, value in self.scope.get('headers', [])
        )
        client = self.scope.get('client') or ('', 0)
        peer = Peer(
            consumer=self,
            peer_id=secrets.token_hex(4),
            sid=sid,
            name=room.unique_name(name),
            color=room.pick_color(),
            authenticated=authenticated,
            ip=ratelimit.client_ip_from_headers(headers, client[0] if client else ''),
        )

        payload = {
            't': 'init',
            'room': room.name,
            'epoch': room.epoch,
            'you': peer.describe(),
            'peers': [other.describe() for other in room.peers.values()],
            'state': room.describe(),
            'last_run': room.last_run,
            'now': now_ms(),
            'max_chars': settings.ROOM_MAX_DOC_CHARS,
        }
        catchup = self._catchup(room, message.get('resume'))
        if catchup is not None:
            payload['resumed'] = True
            payload['catchup'] = catchup
        else:
            payload['resumed'] = False
            payload['docs'] = dict(
                (key, room.documents[key].snapshot()) for key in DOCUMENT_KEYS)

        room.peers[peer.id] = peer
        self.room = room
        self.peer = peer
        self.push(payload)
        room.broadcast({'t': 'join', 'peer': peer.describe()}, exclude=peer)

    @staticmethod
    def _catchup(room, resume):
        """Operations the client missed, if it can pick up where it left off."""
        if not isinstance(resume, dict) or resume.get('epoch') != room.epoch:
            return None
        revisions = resume.get('revs')
        if not isinstance(revisions, dict):
            return None
        catchup = {}
        for key in DOCUMENT_KEYS:
            entries = room.documents[key].entries_since(revisions.get(key))
            if entries is None:
                return None
            catchup[key] = entries
        return catchup

    # ---- handlers (synchronous on purpose: no await between changing the
    #      room and queueing the messages that describe the change) ----

    def _on_op(self, message):
        room, peer = self.room, self.peer
        key = message.get('doc')
        document = room.documents.get(key) if isinstance(key, str) else None
        if document is None:
            return
        op_id = message.get('id')
        if not isinstance(op_id, int) or isinstance(op_id, bool):
            op_id = None
        try:
            operation = TextOperation.from_json(message.get('op'))
            applied, duplicate = document.receive(message.get('rev'), operation, peer.sid, op_id)
        except DocumentTooLong:
            self._resync(key, document, '内容超过长度上限（%d 个字符）' % settings.ROOM_MAX_DOC_CHARS)
            return
        except (StaleRevision, OTError):
            self._resync(key, document, '')
            return
        if duplicate:
            # The client resent something we already have. It should have
            # learnt that from the catch-up; give it a clean copy instead.
            self._resync(key, document, '')
            return
        self.push({'t': 'ack', 'doc': key, 'rev': document.revision, 'id': op_id})
        room.broadcast(
            {'t': 'op', 'doc': key, 'rev': document.revision, 'op': applied.to_json(), 'by': peer.id},
            exclude=peer)
        manager.mark_dirty(room)

    def _resync(self, key, document, reason):
        snapshot = document.snapshot()
        self.push({'t': 'resync', 'doc': key, 'rev': snapshot['rev'], 'text': snapshot['text'],
                   'reason': reason})

    def _on_check(self, message):
        key = message.get('doc')
        document = self.room.documents.get(key) if isinstance(key, str) else None
        if document is None:
            return
        if message.get('rev') == document.revision and message.get('hash') != document.checksum():
            logger.warning('room %s: document %s diverged for a client; resyncing', self.room.name, key)
            self._resync(key, document, '')

    def _on_cursor(self, message):
        key = message.get('doc')
        if key not in DOCUMENT_KEYS:
            return
        anchor, head = message.get('a'), message.get('h')
        if anchor is None:
            cursor = None
        else:
            if not all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10 ** 7
                       for value in (anchor, head)):
                return
            cursor = {'doc': key, 'a': anchor, 'h': head}
        self.peer.cursor = cursor
        self.room.broadcast({'t': 'cursor', 'by': self.peer.id, 'cursor': cursor}, exclude=self.peer)

    def _on_set(self, message):
        room = self.room
        if message.get('key') != 'language':
            return
        language = sandbox.get_language(str(message.get('value') or ''))
        if language is None or not sandbox.find_binary(language):
            self._error('language', '服务器不支持这种语言')
            return
        if room.language == language.key:
            return
        room.language = language.key
        room.broadcast({'t': 'state', 'key': 'language', 'value': language.key, 'by': self.peer.id},
                       exclude=self.peer)
        manager.mark_dirty(room)

    def _on_timer(self, message):
        room = self.room
        if room.timer_action(message.get('action')):
            room.broadcast({'t': 'state', 'key': 'timer', 'value': dict(room.timer),
                            'now': now_ms(), 'by': self.peer.id})
            manager.mark_dirty(room)

    def _on_name(self, message):
        name = clean_name(message.get('name'))
        if not name or name == self.peer.name:
            return
        self.peer.name = self.room.unique_name(name)
        self.room.broadcast({'t': 'peer', 'peer': self.peer.describe()})

    def _on_ping(self, message):
        self.push({'t': 'pong', 'now': now_ms()})

    def _on_run(self, message):
        room, peer = self.room, self.peer
        if room.running:
            self._error('busy', '程序正在运行，请稍候')
            return
        if not ratelimit.allow('run_per_ip', peer.ip) or not ratelimit.allow('run_per_room', room.name):
            self._error('rate', '运行得太频繁了，请稍后再试')
            return
        language = room.language
        code = room.documents['code'].plain_text()
        stdin_text = room.documents['stdin'].plain_text()
        problem = execution.validate_program(language, code, stdin_text)
        if problem:
            self._error('run', problem)
            return
        try:
            future = execution.submit(execution.run_program, language, code, stdin_text)
        except execution.QueueFull:
            self._error('busy', '服务器正忙，请稍后再试')
            return
        room.running = True
        room.broadcast({'t': 'run', 'state': 'started', 'by': peer.id, 'name': peer.name,
                        'language': language})
        asyncio.ensure_future(self._finish_run(room, peer.id, peer.name, future))

    @staticmethod
    async def _finish_run(room, peer_id, peer_name, future):
        try:
            result = await asyncio.wrap_future(future)
        except Exception:
            logger.exception('run failed in room %s', room.name)
            result = {'status': sandbox.STATUS_INTERNAL_ERROR, 'stdout': '', 'stderr': '',
                      'compile_output': '', 'message': '运行环境出错，请稍后再试',
                      'cpu_ms': 0, 'wall_ms': 0, 'memory_kb': 0, 'exit_code': None,
                      'signal': None, 'truncated': False}
        stdout, cut_out = _clip_output(result.get('stdout') or '')
        stderr, cut_err = _clip_output(result.get('stderr') or '')
        compile_output, _ = _clip_output(result.get('compile_output') or '')
        last_run = {
            'status': result.get('status'),
            'stdout': stdout,
            'stderr': stderr,
            'compile_output': compile_output,
            'message': result.get('message') or '',
            'cpu_ms': result.get('cpu_ms') or 0,
            'wall_ms': result.get('wall_ms') or 0,
            'memory_kb': result.get('memory_kb') or 0,
            'exit_code': result.get('exit_code'),
            'signal': result.get('signal'),
            'truncated': bool(result.get('truncated') or cut_out or cut_err),
            'language': result.get('language'),
            'by': peer_id,
            'name': peer_name,
            'at': now_ms(),
        }
        room.running = False
        room.last_run = last_run
        room.broadcast({'t': 'run', 'state': 'done', 'result': last_run})
        manager.mark_dirty(room)
        if not room.peers:
            await manager.left(room)

    _HANDLERS = {
        'op': _on_op,
        'cursor': _on_cursor,
        'check': _on_check,
        'set': _on_set,
        'timer': _on_timer,
        'name': _on_name,
        'run': _on_run,
        'ping': _on_ping,
    }


class LegacyEditorConsumer(AsyncWebsocketConsumer):
    """Old ``/ws/editor/<room>/`` endpoint.

    Pages cached by browsers before the upgrade still connect here with the
    old "send the whole state" protocol. Tell them to reload instead of
    letting them overwrite the room.
    """

    NOTICE = '网站已升级，请刷新页面（Ctrl+F5 / Cmd+Shift+R）后继续使用。'

    async def connect(self):
        await self.accept()
        await self.send(text_data=json.dumps({
            'session_id': 'server',
            'problem_text': self.NOTICE,
            'output_text': self.NOTICE,
        }))

    async def receive(self, text_data=None, bytes_data=None):
        return

    async def disconnect(self, close_code):
        return
