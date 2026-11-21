"""Live state of the interview rooms.

Everything a room needs while people are in it is kept in the memory of the
(single) ASGI process: the three shared documents with their revision
history, the participants, the timer and the last run. The content is written
back to ``RoomState`` shortly after every change and when the last person
leaves, and an empty room is dropped from memory after a grace period.

All mutation happens on the event loop and without awaiting in between, so no
locks are needed: a handler that changes a document and queues the resulting
messages for the other participants cannot be interleaved with another one.
"""
import asyncio
import json
import logging
import re
import secrets
import time

from channels.db import database_sync_to_async
from django.conf import settings

from . import sandbox
from .ot import OTError, TextOperation, from_units, text_hash, to_units

logger = logging.getLogger(__name__)

ROOM_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{3,64}$')

# document key -> RoomState field
DOCUMENTS = (('code', 'code'), ('problem', 'problem_text'), ('stdin', 'input_text'))
DOCUMENT_KEYS = tuple(key for key, _ in DOCUMENTS)

PEER_COLORS = [
    '#2f81f7', '#e5534b', '#3fb950', '#d29922', '#a371f7', '#db61a2',
    '#39c5cf', '#f0883e', '#8b949e', '#6cb6ff', '#f47067', '#57ab5a',
]
MAX_NAME_LENGTH = 20
MAX_OUTPUT_CHARS = 64 * 1024


class StaleRevision(Exception):
    """The operation is based on a revision the server no longer has."""


class DocumentTooLong(Exception):
    pass


def normalize_newlines(text):
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def clean_name(name):
    name = re.sub(r'[\x00-\x1f\x7f<>]', '', str(name or '')).strip()
    return name[:MAX_NAME_LENGTH]


def now_ms():
    return int(time.time() * 1000)


class Document(object):
    """A shared text with the operations that led to its current revision."""

    def __init__(self, text=''):
        self.text = to_units(normalize_newlines(text))
        self.revision = 0
        self.base_revision = 0   # revision the oldest history entry starts from
        self.history = []        # (TextOperation, sid, op_id)

    def receive(self, revision, operation, sid, op_id):
        """Apply a client operation made against ``revision``.

        Returns ``(operation as applied, duplicate)``. ``duplicate`` is True
        when this very operation was already applied (a client that resent it
        after a reconnect); nothing changes in that case.
        """
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise OTError('bad revision')
        if revision < self.base_revision or revision > self.revision:
            raise StaleRevision()
        for known, known_sid, known_id in self.history[revision - self.base_revision:]:
            if known_sid == sid and known_id == op_id and op_id is not None:
                return known, True
            operation = TextOperation.transform(operation, known)[0]
        if operation.base_length != len(self.text):
            raise OTError('operation does not fit the document')
        if operation.target_length > settings.ROOM_MAX_DOC_CHARS:
            raise DocumentTooLong()
        self.text = operation.apply(self.text)
        self.history.append((operation, sid, op_id))
        self.revision += 1
        overflow = len(self.history) - settings.ROOM_HISTORY_LENGTH
        if overflow > 0:
            del self.history[:overflow]
            self.base_revision += overflow
        return operation, False

    def entries_since(self, revision):
        """History entries after ``revision`` in wire form, or None if too old."""
        if not isinstance(revision, int) or isinstance(revision, bool):
            return None
        if revision < self.base_revision or revision > self.revision:
            return None
        entries = []
        current = revision
        for operation, sid, op_id in self.history[revision - self.base_revision:]:
            current += 1
            entries.append({'rev': current, 'op': operation.to_json(), 'sid': sid, 'id': op_id})
        return entries

    def snapshot(self):
        return {'rev': self.revision, 'text': self.text}

    def plain_text(self):
        return from_units(self.text)

    def checksum(self):
        return text_hash(self.text)


class Peer(object):
    def __init__(self, consumer, peer_id, sid, name, color, authenticated, ip):
        self.consumer = consumer
        self.id = peer_id
        self.sid = sid
        self.name = name
        self.color = color
        self.authenticated = authenticated
        self.ip = ip
        self.cursor = None

    def describe(self):
        data = {'id': self.id, 'name': self.name, 'color': self.color, 'auth': self.authenticated}
        if self.cursor:
            data['cursor'] = self.cursor
        return data


class Room(object):
    def __init__(self, name, row=None):
        self.name = name
        self.epoch = secrets.token_hex(8)
        self.peers = {}
        self.documents = {}
        for key, field in DOCUMENTS:
            self.documents[key] = Document(getattr(row, field, '') if row is not None else '')
        language = sandbox.normalize_language(getattr(row, 'language', '') or 'python')
        self.language = language if sandbox.get_language(language) else 'python'
        if row is None:
            # A brand new room starts with the example program of its language.
            # (Not marked dirty: a room nobody touched is never stored.)
            self.documents['code'] = Document(sandbox.get_language(self.language).template)
        meta = row.get_meta() if row is not None else {}
        self.timer = self._clean_timer(meta.get('timer'))
        self.is_vip = bool(getattr(row, 'is_vip', False))
        self.last_run = None
        output = getattr(row, 'output_text', '') if row is not None else ''
        if output:
            self.last_run = {'status': 'saved', 'stdout': output, 'stderr': '', 'saved': True}
        self.running = False
        self.dirty = False
        self.saving = False
        self.save_handle = None
        self.evict_handle = None
        self.exists_in_db = row is not None

    # ---- participants ----

    def pick_color(self):
        used = set(peer.color for peer in self.peers.values())
        for color in PEER_COLORS:
            if color not in used:
                return color
        return PEER_COLORS[len(self.peers) % len(PEER_COLORS)]

    def unique_name(self, wanted):
        taken = set(peer.name for peer in self.peers.values())
        if wanted not in taken:
            return wanted
        for suffix in range(2, 100):
            candidate = '%s %d' % (wanted[:MAX_NAME_LENGTH - 3], suffix)
            if candidate not in taken:
                return candidate
        return wanted

    def broadcast(self, message, exclude=None):
        text = json.dumps(message)
        for peer in list(self.peers.values()):
            if peer is not exclude:
                peer.consumer.push_text(text)

    # ---- shared state ----

    @staticmethod
    def _clean_timer(value):
        timer = {'running': False, 'started_at': None, 'elapsed': 0}
        if isinstance(value, dict):
            try:
                timer['elapsed'] = max(0, int(value.get('elapsed') or 0))
                if value.get('running') and value.get('started_at'):
                    timer['running'] = True
                    timer['started_at'] = int(value['started_at'])
            except (TypeError, ValueError):
                pass
        return timer

    def timer_action(self, action):
        now = now_ms()
        timer = self.timer
        if action == 'start' and not timer['running']:
            timer['running'] = True
            timer['started_at'] = now
        elif action == 'pause' and timer['running']:
            timer['elapsed'] += max(0, now - (timer['started_at'] or now))
            timer['running'] = False
            timer['started_at'] = None
        elif action == 'reset':
            timer['running'] = False
            timer['started_at'] = None
            timer['elapsed'] = 0
        else:
            return False
        return True

    def describe(self):
        return {
            'language': self.language,
            'timer': dict(self.timer),
            'is_vip': self.is_vip,
            'running': self.running,
        }

    def content_fields(self):
        """Values to store in the RoomState row."""
        fields = {}
        for key, field in DOCUMENTS:
            fields[field] = self.documents[key].plain_text()
        fields['language'] = self.language
        output = ''
        if self.last_run:
            output = (self.last_run.get('stdout') or '')
            if self.last_run.get('stderr'):
                output = (output + '\n' if output else '') + self.last_run['stderr']
        fields['output_text'] = output[:MAX_OUTPUT_CHARS]
        fields['meta'] = json.dumps({'timer': self.timer})
        return fields


class RoomManager(object):
    def __init__(self):
        self.rooms = {}
        self._loading = {}
        self.loop = None

    # ---- lookup ----

    async def get(self, name):
        """The live Room called ``name``, loading it from the database if needed."""
        self.loop = asyncio.get_event_loop()
        room = self.rooms.get(name)
        if room is None:
            pending = self._loading.get(name)
            if pending is None:
                pending = self._loading[name] = asyncio.ensure_future(self._load(name))
            room = await pending
        if room.evict_handle is not None:
            room.evict_handle.cancel()
            room.evict_handle = None
        return room

    async def _load(self, name):
        try:
            row = await database_sync_to_async(self._fetch_row)(name)
            room = Room(name, row)
            self.rooms[name] = room
            return room
        finally:
            self._loading.pop(name, None)

    @staticmethod
    def _fetch_row(name):
        from .models import RoomState
        return RoomState.objects.filter(room_name=name).first()

    # ---- persistence ----

    def mark_dirty(self, room):
        room.dirty = True
        if room.save_handle is None and not room.saving:
            room.save_handle = self.loop.call_later(
                settings.ROOM_SAVE_DELAY_SECONDS, self._start_save, room)

    def _start_save(self, room):
        room.save_handle = None
        asyncio.ensure_future(self.save(room))

    async def save(self, room):
        if room.saving or not room.dirty:
            return
        room.saving = True
        try:
            while room.dirty:
                room.dirty = False
                fields = room.content_fields()
                try:
                    await database_sync_to_async(self._write_row)(room.name, fields)
                    room.exists_in_db = True
                except Exception:
                    logger.exception('saving room %s failed', room.name)
                    room.dirty = True
                    break
        finally:
            room.saving = False
        if room.dirty and room.save_handle is None:
            # The save failed: try again a little later.
            room.save_handle = self.loop.call_later(5.0, self._start_save, room)

    @staticmethod
    def _write_row(name, fields):
        from .models import RoomState
        RoomState.objects.update_or_create(room_name=name, defaults=fields)

    # ---- leaving / eviction ----

    async def left(self, room):
        """Called after a participant left."""
        if room.peers:
            return
        if room.save_handle is not None:
            room.save_handle.cancel()
            room.save_handle = None
        await self.save(room)
        if not room.peers and room.evict_handle is None:
            room.evict_handle = self.loop.call_later(
                settings.ROOM_IDLE_EVICT_SECONDS, self._evict, room)

    def _evict(self, room):
        room.evict_handle = None
        if room.peers or room.running or room.saving:
            return
        if room.dirty:
            self.mark_dirty(room)
            room.evict_handle = self.loop.call_later(
                settings.ROOM_IDLE_EVICT_SECONDS, self._evict, room)
            return
        if self.rooms.get(room.name) is room:
            del self.rooms[room.name]

    # ---- calls from request threads ----

    def notify_threadsafe(self, name, message, apply=None):
        """Send ``message`` to everybody in room ``name`` from a non-loop thread.

        ``apply(room)`` (optional) runs on the loop first, to update the room.
        """
        loop = self.loop
        if loop is None:
            return

        def deliver():
            room = self.rooms.get(name)
            if room is None:
                return
            if apply is not None:
                apply(room)
            room.broadcast(message)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass  # loop already closed

    def stats(self):
        return {
            'rooms': len(self.rooms),
            'peers': sum(len(room.peers) for room in self.rooms.values()),
        }


manager = RoomManager()
