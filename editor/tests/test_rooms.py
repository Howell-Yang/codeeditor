"""The room WebSocket: joining, editing, presence, persistence, running code."""
import asyncio
import random

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from editor import ratelimit, sandbox
from editor.models import RoomState
from editor.ot import OTError, TextOperation, replace_all, text_hash, to_units
from editor.rooms import Document, StaleRevision, clean_name, manager
from editor.routing import websocket_urlpatterns

application = URLRouter(websocket_urlpatterns)
TEMPLATE = sandbox.get_language('python').template


class DocumentTests(SimpleTestCase):

    def test_concurrent_operations_are_transformed(self):
        document = Document('hello')
        first, _ = document.receive(0, TextOperation().retain(5).insert(' world'), 'a', 1)
        # based on revision 0 as well, but arrives second
        second, _ = document.receive(0, TextOperation().insert('>> ').retain(5), 'b', 1)
        self.assertEqual(document.text, '>> hello world')
        self.assertEqual(document.revision, 2)
        self.assertEqual(second.to_json(), ['>> ', 11])
        self.assertEqual(first.to_json(), [5, ' world'])

    def test_stale_and_invalid_operations(self):
        document = Document('abc')
        with self.assertRaises(StaleRevision):
            document.receive(5, TextOperation().retain(3), 'a', 1)
        with self.assertRaises(StaleRevision):
            document.receive(-1, TextOperation().retain(3), 'a', 1)
        with self.assertRaises(OTError):
            document.receive(0, TextOperation().retain(99), 'a', 1)
        with self.assertRaises(OTError):
            document.receive('0', TextOperation().retain(3), 'a', 1)
        self.assertEqual((document.text, document.revision), ('abc', 0))

    def test_resent_operation_is_recognised(self):
        document = Document('abc')
        operation = TextOperation().retain(3).insert('d')
        document.receive(0, operation, 'a', 7)
        _, duplicate = document.receive(0, TextOperation().retain(3).insert('d'), 'a', 7)
        self.assertTrue(duplicate)
        self.assertEqual(document.text, 'abcd')

    @override_settings(ROOM_HISTORY_LENGTH=3)
    def test_history_is_bounded(self):
        document = Document('')
        for index in range(10):
            document.receive(index, TextOperation().retain(index).insert('x'), 'a', index)
        self.assertEqual(len(document.history), 3)
        self.assertIsNone(document.entries_since(2))               # too old to resume from
        self.assertEqual([entry['rev'] for entry in document.entries_since(8)], [9, 10])
        self.assertEqual(document.entries_since(10), [])
        with self.assertRaises(StaleRevision):
            document.receive(2, TextOperation().retain(2).insert('y'), 'b', 1)

    def test_newlines_and_astral_characters(self):
        document = Document('a\r\nb\r😀')
        self.assertEqual(document.plain_text(), 'a\nb\n😀')
        self.assertEqual(len(document.text), 6)                    # the emoji counts as two units
        document.receive(0, TextOperation.from_json([6, '🎉']), 'a', 1)
        self.assertEqual(document.plain_text(), 'a\nb\n😀🎉')
        self.assertEqual(document.checksum(), text_hash(to_units('a\nb\n😀🎉')))

    def test_clean_name(self):
        self.assertEqual(clean_name('  张三 <b> \n'), '张三 b')
        self.assertEqual(len(clean_name('x' * 50)), 20)
        self.assertEqual(clean_name(None), '')


@override_settings(ROOM_SAVE_DELAY_SECONDS=0.05, ROOM_IDLE_EVICT_SECONDS=30)
class RoomConsumerTests(TransactionTestCase):

    def setUp(self):
        manager.rooms.clear()
        ratelimit.reset()

    async def join(self, room, name='', sid=None, resume=None):
        communicator = WebsocketCommunicator(application, '/ws/room/%s/' % room)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        hello = {'t': 'hello', 'sid': sid or 'sid-%d' % random.randrange(10 ** 9), 'name': name}
        if resume is not None:
            hello['resume'] = resume
        await communicator.send_json_to(hello)
        init = await communicator.receive_json_from(timeout=5)
        self.assertEqual(init['t'], 'init')
        return communicator, init

    async def expect(self, communicator, kind, timeout=5):
        """Next message of the given type (others in between are skipped)."""
        for _ in range(50):
            message = await communicator.receive_json_from(timeout=timeout)
            if message['t'] == kind:
                return message
        self.fail('no %s message' % kind)

    async def test_new_room_starts_with_the_template_and_is_not_stored(self):
        alice, init = await self.join('fresh-room', name='面试官')
        self.assertFalse(init['resumed'])
        self.assertEqual(init['docs']['code'], {'rev': 0, 'text': TEMPLATE})
        self.assertEqual(init['docs']['problem']['text'], '')
        self.assertEqual(init['you']['name'], '面试官')
        self.assertEqual(init['peers'], [])
        self.assertEqual(init['state']['language'], 'python')
        self.assertFalse(init['state']['timer']['running'])
        await alice.disconnect()
        self.assertFalse(await database_sync_to_async(RoomState.objects.filter(room_name='fresh-room').exists)())

    async def test_invalid_room_name_is_rejected(self):
        communicator = WebsocketCommunicator(application, '/ws/room/a b/')
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_edits_reach_the_other_participant(self):
        alice, _ = await self.join('pair', name='Alice')
        bob, bob_init = await self.join('pair', name='Bob')
        self.assertEqual([peer['name'] for peer in bob_init['peers']], ['Alice'])
        joined = await self.expect(alice, 'join')
        self.assertEqual(joined['peer']['name'], 'Bob')
        self.assertNotEqual(joined['peer']['color'], bob_init['peers'][0]['color'])

        length = len(TEMPLATE)
        await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1, 'op': [length, '# by alice\n']})
        ack = await self.expect(alice, 'ack')
        self.assertEqual((ack['doc'], ack['rev'], ack['id']), ('code', 1, 1))
        seen = await self.expect(bob, 'op')
        self.assertEqual((seen['doc'], seen['rev'], seen['op']), ('code', 1, [length, '# by alice\n']))

        # Bob edits the problem text; Alice edits the code against a stale revision at the same time.
        await bob.send_json_to({'t': 'op', 'doc': 'problem', 'rev': 0, 'id': 1, 'op': ['# 题目\n']})
        await bob.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 2, 'op': ['# by bob\n', length]})
        self.assertEqual((await self.expect(bob, 'ack'))['doc'], 'problem')
        code_ack = await self.expect(bob, 'ack')
        self.assertEqual(code_ack['rev'], 2)
        self.assertEqual((await self.expect(alice, 'op'))['doc'], 'problem')
        transformed = await self.expect(alice, 'op')
        self.assertEqual(transformed['op'], ['# by bob\n', length + len('# by alice\n')])

        room = manager.rooms['pair']
        self.assertEqual(room.documents['code'].plain_text(), '# by bob\n' + TEMPLATE + '# by alice\n')
        await alice.disconnect()
        await bob.disconnect()

    async def test_content_is_saved_and_loaded_again(self):
        alice, _ = await self.join('keep-me')
        await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1,
                                  'op': replace_all(to_units(TEMPLATE), 'print("保存 😀")\n').to_json()})
        await self.expect(alice, 'ack')
        await alice.send_json_to({'t': 'op', 'doc': 'stdin', 'rev': 0, 'id': 1, 'op': ['1 2 3']})
        await self.expect(alice, 'ack')
        await alice.send_json_to({'t': 'set', 'key': 'language', 'value': 'python'})
        await alice.disconnect()

        state = await database_sync_to_async(RoomState.objects.get)(room_name='keep-me')
        self.assertEqual(state.code, 'print("保存 😀")\n')
        self.assertEqual(state.input_text, '1 2 3')

        manager.rooms.clear()                                      # as after a restart
        bob, init = await self.join('keep-me')
        # (the JSON decoder of the test client joins the surrogate pair again)
        self.assertEqual(init['docs']['code']['text'], 'print("保存 😀")\n')
        self.assertEqual(init['docs']['stdin']['text'], '1 2 3')
        self.assertEqual(init['docs']['code']['rev'], 0)
        await bob.disconnect()

    async def test_autosave_while_people_are_still_in_the_room(self):
        alice, _ = await self.join('autosave')
        await alice.send_json_to({'t': 'op', 'doc': 'problem', 'rev': 0, 'id': 1, 'op': ['hello']})
        await self.expect(alice, 'ack')
        for _ in range(60):
            await asyncio.sleep(0.05)
            state = await database_sync_to_async(RoomState.objects.filter(room_name='autosave').first)()
            if state is not None and state.problem_text == 'hello':
                break
        else:
            self.fail('the room was not saved')
        await alice.disconnect()

    async def test_bad_operations_trigger_a_resync(self):
        alice, _ = await self.join('strict')
        for operation in ([999], 'nonsense', [{'x': 1}], [1.5]):
            await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1, 'op': operation})
            resync = await self.expect(alice, 'resync')
            self.assertEqual((resync['doc'], resync['rev'], resync['text']), ('code', 0, TEMPLATE))
        await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 7, 'id': 2, 'op': [len(TEMPLATE)]})
        await self.expect(alice, 'resync')
        await alice.send_json_to({'t': 'op', 'doc': 'nope', 'rev': 0, 'id': 3, 'op': []})
        await alice.send_json_to({'t': 'ping'})
        self.assertEqual((await self.expect(alice, 'pong'))['t'], 'pong')
        await alice.disconnect()

    @override_settings(ROOM_MAX_DOC_CHARS=50)
    async def test_documents_have_a_size_limit(self):
        alice, _ = await self.join('small')
        await alice.send_json_to({'t': 'op', 'doc': 'problem', 'rev': 0, 'id': 1, 'op': ['x' * 51]})
        resync = await self.expect(alice, 'resync')
        self.assertIn('50', resync['reason'])
        self.assertEqual(resync['text'], '')
        await alice.disconnect()

    async def test_checksum_mismatch_is_repaired(self):
        alice, _ = await self.join('checked')
        await alice.send_json_to({'t': 'check', 'doc': 'code', 'rev': 0, 'hash': text_hash(to_units(TEMPLATE))})
        await alice.send_json_to({'t': 'ping'})
        self.assertEqual((await alice.receive_json_from(timeout=5))['t'], 'pong')   # nothing else came first
        await alice.send_json_to({'t': 'check', 'doc': 'code', 'rev': 0, 'hash': 12345})
        self.assertEqual((await self.expect(alice, 'resync'))['text'], TEMPLATE)
        await alice.disconnect()

    async def test_resume_after_a_short_disconnect(self):
        alice, init = await self.join('resume', sid='alice-sid')
        bob, _ = await self.join('resume', sid='bob-sid')
        await alice.send_json_to({'t': 'op', 'doc': 'problem', 'rev': 0, 'id': 1, 'op': ['a']})
        await self.expect(alice, 'ack')
        await alice.disconnect()
        # While Alice is away Bob types, and Alice's own unacknowledged edit had arrived too.
        await bob.send_json_to({'t': 'op', 'doc': 'problem', 'rev': 1, 'id': 1, 'op': [1, 'b']})
        await self.expect(bob, 'ack')

        alice, resumed = await self.join('resume', sid='alice-sid', resume={
            'epoch': init['epoch'], 'revs': {'code': 0, 'problem': 0, 'stdin': 0}})
        self.assertTrue(resumed['resumed'])
        self.assertNotIn('docs', resumed)
        self.assertEqual(resumed['catchup']['code'], [])
        self.assertEqual(resumed['catchup']['problem'], [
            {'rev': 1, 'op': ['a'], 'sid': 'alice-sid', 'id': 1},
            {'rev': 2, 'op': [1, 'b'], 'sid': 'bob-sid', 'id': 1},
        ])

        # A different epoch (server restarted) or an impossible revision gets the full state.
        carol, full = await self.join('resume', resume={'epoch': 'other', 'revs': {'code': 0, 'problem': 0, 'stdin': 0}})
        self.assertFalse(full['resumed'])
        self.assertEqual(full['docs']['problem'], {'rev': 2, 'text': 'ab'})
        dave, full = await self.join('resume', resume={'epoch': init['epoch'], 'revs': {'code': 0, 'problem': 99, 'stdin': 0}})
        self.assertFalse(full['resumed'])
        for communicator in (alice, bob, carol, dave):
            await communicator.disconnect()

    async def test_presence_cursor_name_language_and_timer(self):
        alice, _ = await self.join('social', name='Alice')
        bob, _ = await self.join('social', name='Alice')            # same name: made unique
        await self.expect(alice, 'join')

        await bob.send_json_to({'t': 'cursor', 'doc': 'code', 'a': 3, 'h': 8})
        cursor = await self.expect(alice, 'cursor')
        self.assertEqual(cursor['cursor'], {'doc': 'code', 'a': 3, 'h': 8})
        await bob.send_json_to({'t': 'cursor', 'doc': 'code', 'a': -1, 'h': 'x'})      # ignored

        await bob.send_json_to({'t': 'name', 'name': '候选人 <i>'})
        renamed = await self.expect(alice, 'peer')
        self.assertEqual(renamed['peer']['name'], '候选人 i')

        await alice.send_json_to({'t': 'set', 'key': 'language', 'value': 'cobol'})
        self.assertEqual((await self.expect(alice, 'error'))['code'], 'language')
        if sandbox.find_binary(sandbox.get_language('cpp')):
            await alice.send_json_to({'t': 'set', 'key': 'language', 'value': 'c++'})
            changed = await self.expect(bob, 'state')
            self.assertEqual((changed['key'], changed['value']), ('language', 'cpp'))

        await alice.send_json_to({'t': 'timer', 'action': 'start'})
        started = await self.expect(bob, 'state')
        self.assertEqual(started['key'], 'timer')
        self.assertTrue(started['value']['running'])
        await asyncio.sleep(0.05)
        await bob.send_json_to({'t': 'timer', 'action': 'pause'})
        paused = await self.expect(alice, 'state')
        while paused['key'] != 'timer' or paused['value']['running']:
            paused = await self.expect(alice, 'state')
        self.assertGreater(paused['value']['elapsed'], 0)
        await alice.send_json_to({'t': 'timer', 'action': 'explode'})                  # ignored

        await bob.disconnect()
        self.assertEqual((await self.expect(alice, 'leave'))['id'], renamed['peer']['id'])
        await alice.disconnect()

    async def test_late_joiner_sees_cursors_and_state(self):
        alice, _ = await self.join('late', name='Alice')
        await alice.send_json_to({'t': 'cursor', 'doc': 'code', 'a': 1, 'h': 1})
        await alice.send_json_to({'t': 'timer', 'action': 'start'})
        await self.expect(alice, 'state')
        bob, init = await self.join('late')
        self.assertEqual(init['peers'][0]['cursor'], {'doc': 'code', 'a': 1, 'h': 1})
        self.assertTrue(init['state']['timer']['running'])
        self.assertIsInstance(init['now'], int)
        await alice.disconnect()
        await bob.disconnect()

    @override_settings(ROOM_MAX_PEERS=2)
    async def test_room_capacity(self):
        first, _ = await self.join('full')
        second, _ = await self.join('full')
        third = WebsocketCommunicator(application, '/ws/room/full/')
        await third.connect()
        await third.send_json_to({'t': 'hello', 'sid': 'x'})
        error = await third.receive_json_from(timeout=5)
        self.assertEqual(error['code'], 'room_full')
        self.assertEqual((await third.receive_output(timeout=5))['type'], 'websocket.close')
        await first.disconnect()
        await second.disconnect()

    async def test_messages_before_hello_are_ignored(self):
        communicator = WebsocketCommunicator(application, '/ws/room/quiet/')
        await communicator.connect()
        await communicator.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1, 'op': ['x']})
        await communicator.send_to(text_data='not json')
        await communicator.send_json_to(['not', 'an', 'object'])
        self.assertTrue(await communicator.receive_nothing(timeout=0.2))
        await communicator.send_json_to({'t': 'hello', 'sid': 's'})
        self.assertEqual((await communicator.receive_json_from(timeout=5))['docs']['code']['text'], TEMPLATE)
        await communicator.disconnect()

    async def test_run_broadcasts_the_result_to_everyone(self):
        alice, _ = await self.join('runner', name='Alice')
        bob, _ = await self.join('runner', name='Bob')
        program = 'import sys\nprint("sum:", sum(map(int, sys.stdin.read().split())))\n'
        await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1,
                                  'op': replace_all(to_units(TEMPLATE), program).to_json()})
        await alice.send_json_to({'t': 'op', 'doc': 'stdin', 'rev': 0, 'id': 1, 'op': ['20 22']})
        await alice.send_json_to({'t': 'run'})
        for communicator in (alice, bob):
            started = await self.expect(communicator, 'run')
            self.assertEqual((started['state'], started['name']), ('started', 'Alice'))
            done = await self.expect(communicator, 'run', timeout=20)
            self.assertEqual(done['state'], 'done')
            self.assertEqual(done['result']['status'], 'ok')
            self.assertEqual(done['result']['stdout'], 'sum: 42\n')
            self.assertEqual(done['result']['name'], 'Alice')

        carol, init = await self.join('runner')                     # sees the last result
        self.assertEqual(init['last_run']['stdout'], 'sum: 42\n')
        for communicator in (alice, bob, carol):
            await communicator.disconnect()
        state = await database_sync_to_async(RoomState.objects.get)(room_name='runner')
        self.assertEqual(state.output_text, 'sum: 42\n')

    @override_settings(RATE_LIMITS={'run_per_ip': '100/60', 'run_per_room': '1/60'})
    async def test_run_is_rate_limited(self):
        alice, _ = await self.join('limited')
        await alice.send_json_to({'t': 'run'})
        done = await self.expect(alice, 'run', timeout=20)
        while done['state'] != 'done':
            done = await self.expect(alice, 'run', timeout=20)
        await alice.send_json_to({'t': 'run'})
        self.assertEqual((await self.expect(alice, 'error'))['code'], 'rate')
        await alice.disconnect()

    async def test_empty_code_is_not_run(self):
        alice, _ = await self.join('empty')
        await alice.send_json_to({'t': 'op', 'doc': 'code', 'rev': 0, 'id': 1, 'op': [-len(TEMPLATE)]})
        await self.expect(alice, 'ack')
        await alice.send_json_to({'t': 'run'})
        self.assertEqual((await self.expect(alice, 'error'))['code'], 'run')
        await alice.disconnect()

    async def test_many_random_edits_from_two_clients_converge(self):
        """Clients that always edit against what they have seen end up with the server's text."""
        rng = random.Random(5)
        alice, init = await self.join('fuzz')
        bob, _ = await self.join('fuzz')
        await self.expect(alice, 'join')
        views = {alice: [to_units(''), 0], bob: [to_units(''), 0]}   # text, revision of the problem doc
        counters = {alice: 0, bob: 0}

        async def drain(communicator):
            while not await communicator.receive_nothing(timeout=0.01):
                message = await communicator.receive_json_from()
                if message['t'] == 'op' and message['doc'] == 'problem':
                    views[communicator][0] = TextOperation.from_json(message['op']).apply(views[communicator][0])
                    views[communicator][1] = message['rev']
                elif message['t'] == 'ack' and message['doc'] == 'problem':
                    views[communicator][1] = message['rev']

        for _ in range(40):
            communicator = rng.choice((alice, bob))
            await drain(communicator)                                # synchronous clients: see everything first
            text, revision = views[communicator]
            position = rng.randint(0, len(text))
            operation = TextOperation().retain(position).insert(rng.choice(['a', '中', '\n', 'xyz']))
            operation.retain(len(text) - position)
            counters[communicator] += 1
            await communicator.send_json_to({'t': 'op', 'doc': 'problem', 'rev': revision,
                                             'id': counters[communicator], 'op': operation.to_json()})
            views[communicator][0] = operation.apply(text)
            await self.expect(communicator, 'ack')
            views[communicator][1] += 1
        await drain(alice)
        await drain(bob)
        server = manager.rooms['fuzz'].documents['problem']
        self.assertEqual(views[alice][0], server.text)
        self.assertEqual(views[bob][0], server.text)
        self.assertEqual(server.revision, 40)
        await alice.disconnect()
        await bob.disconnect()


class LegacyEndpointTests(TransactionTestCase):

    async def test_old_pages_are_told_to_reload(self):
        communicator = WebsocketCommunicator(application, '/ws/editor/abc12345/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        notice = await communicator.receive_json_from(timeout=5)
        self.assertIn('刷新', notice['problem_text'])
        # whatever the old page sends is ignored and never stored
        await communicator.send_json_to({'code': 'overwrite everything', 'session_id': 'old'})
        self.assertTrue(await communicator.receive_nothing(timeout=0.2))
        await communicator.disconnect()
        self.assertFalse(await database_sync_to_async(RoomState.objects.filter(room_name='abc12345').exists)())
