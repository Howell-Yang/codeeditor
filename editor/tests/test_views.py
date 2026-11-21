import json
import os
import tempfile
import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from editor import ratelimit
from editor.models import CustomProblem, Membership, Problem, ProblemBookmark, RoomState, Submission
from editor.models import TestCase as Case

PASSWORD = 'correct horse battery'


def post_json(client, url, data=None):
    return client.post(url, data=json.dumps(data or {}), content_type='application/json')


def make_problem(slug='sum', title='求和', **fields):
    values = {'description': '输出所有整数的**和**。', 'status': 'published', 'tags': '数学, 入门'}
    values.update(fields)
    problem = Problem.objects.create(slug=slug, title=title, **values)
    Case.objects.create(problem=problem, input_data='1 2\n', expected_output='3\n',
                        is_sample=True, is_hidden=False, order=1)
    Case.objects.create(problem=problem, input_data='10 20 30\n', expected_output='60\n', order=2)
    return problem


class BaseTestCase(TestCase):
    def setUp(self):
        ratelimit.reset()
        self.user = User.objects.create_user('alice', password=PASSWORD)


class PageTests(BaseTestCase):

    def test_public_pages_render(self):
        make_problem()
        for url in ('/', '/problems/', '/problem/sum/', '/login/', '/signup/', '/membership/', '/abc12345/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, 'CodeEditor')

    def test_private_pages_need_a_login(self):
        for url in ('/my-submissions/', '/my-bookmarks/', '/profile/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertTrue(response['Location'].startswith('/login/?next='), url)
        self.client.login(username='alice', password=PASSWORD)
        for url in ('/my-submissions/', '/my-bookmarks/', '/profile/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_new_room_redirects_to_a_fresh_room(self):
        first = self.client.get('/new/')
        second = self.client.get('/new/')
        self.assertEqual(first.status_code, 302)
        self.assertRegex(first['Location'], r'^/[a-z0-9]{8}/$')
        self.assertNotEqual(first['Location'], second['Location'])
        with_problem = self.client.get('/new/?problem=two-sum')
        self.assertRegex(with_problem['Location'], r'^/[a-z0-9]{8}/\?problem=two-sum$')
        self.assertNotIn('problem', self.client.get('/new/?problem=<script>')['Location'])

    def test_room_page(self):
        response = self.client.get('/my-room_1/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'noindex')
        self.assertContains(response, '/ws/room/my-room_1/')
        self.assertIn('csrftoken', response.cookies)
        self.assertIn('no-cache', response['Cache-Control'])

    def test_bad_room_names_are_not_rooms(self):
        for url in ('/ab/', '/has space/', '/' + 'x' * 65 + '/', '/名字/'):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_not_found_page_and_json(self):
        page = self.client.get('/no/such/page')
        self.assertEqual(page.status_code, 404)
        self.assertContains(page, '页面不存在', status_code=404)
        api = self.client.get('/api/nothing/here')
        self.assertEqual(api.status_code, 404)
        self.assertFalse(api.json()['success'])

    def test_robots_and_health(self):
        self.assertContains(self.client.get('/robots.txt'), 'Disallow: /admin/')
        self.assertEqual(self.client.get('/healthz/').json(), {'ok': True})

    def test_legacy_endpoints_tell_old_pages_to_reload(self):
        self.assertIn('刷新', self.client.post('/execute/', data='{}', content_type='application/json').json()['output'])
        self.assertIn('刷新', self.client.post('/abc12345/execute/', data='{}',
                                             content_type='application/json').json()['output'])
        self.assertEqual(self.client.get('/main/').status_code, 302)

    def test_old_purchase_urls_lead_to_the_membership_page(self):
        Membership.objects.create(user=self.user, end_date=timezone.now() - timedelta(days=1), remaining_rooms=0)
        self.client.login(username='alice', password=PASSWORD)
        for url in ('/purchase-membership/', '/purchase-by-count/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], '/membership/')
        self.assertFalse(Membership.objects.get(user=self.user).is_active())

    def test_site_identity_comes_from_settings(self):
        with override_settings(SITE_ICP_NUMBER='测ICP备00000000号', SITE_POLICE_NUMBER='测公网安备 0000号'):
            for url in ('/', '/abc12345/'):
                response = self.client.get(url)
                self.assertContains(response, '测ICP备00000000号')
                self.assertContains(response, '测公网安备 0000号')
        with override_settings(SITE_ICP_NUMBER='', SITE_POLICE_NUMBER=''):
            self.assertNotContains(self.client.get('/'), 'beian.miit.gov.cn')

    def test_acme_challenge(self):
        self.assertEqual(self.client.get('/.well-known/acme-challenge/token').status_code, 404)
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, 'token-1'), 'w') as handle:
                handle.write('token-1.signature')
            with override_settings(ACME_CHALLENGE_DIR=directory):
                response = self.client.get('/.well-known/acme-challenge/token-1')
                self.assertEqual(response.content, b'token-1.signature')
                self.assertEqual(self.client.get('/.well-known/acme-challenge/missing').status_code, 404)
                self.assertEqual(self.client.get('/.well-known/acme-challenge/..').status_code, 404)


class AuthTests(BaseTestCase):

    def test_signup_creates_account_membership_and_session(self):
        response = post_json(self.client, '/signup/', {
            'username': 'bob', 'password1': PASSWORD, 'password2': PASSWORD})
        data = response.json()
        self.assertTrue(data['success'], data)
        user = User.objects.get(username='bob')
        membership = Membership.objects.get(user=user)
        self.assertTrue(membership.has_time_plan())
        self.assertEqual(membership.remaining_rooms, 5)
        self.assertEqual(self.client.get('/get-user-info/').json()['user']['username'], 'bob')

    def test_signup_errors_are_reported_per_field(self):
        data = post_json(self.client, '/signup/', {
            'username': 'alice', 'password1': PASSWORD, 'password2': 'something else'}).json()
        self.assertFalse(data['success'])
        self.assertIn('username', data['errors'])
        self.assertIn('password2', data['errors'])
        self.assertTrue(data['error'])
        weak = post_json(self.client, '/signup/', {'username': 'carol', 'password1': '12345678',
                                                    'password2': '12345678'}).json()
        self.assertFalse(weak['success'])
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_login_logout(self):
        bad = post_json(self.client, '/login/', {'username': 'alice', 'password': 'wrong'}).json()
        self.assertFalse(bad['success'])
        self.assertIn('password', bad['errors'])
        self.assertIsNone(self.client.get('/get-user-info/').json()['user'])

        good = post_json(self.client, '/login/?next=/problems/', {'username': 'alice', 'password': PASSWORD}).json()
        self.assertTrue(good['success'])
        self.assertEqual(good['next'], '/problems/')
        self.assertEqual(self.client.get('/get-user-info/').json()['user']['username'], 'alice')

        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertTrue(self.client.post('/logout/').json()['success'])
        self.assertIsNone(self.client.get('/get-user-info/').json()['user'])

    def test_open_redirects_are_refused(self):
        data = post_json(self.client, '/login/?next=https://evil.example/', {
            'username': 'alice', 'password': PASSWORD}).json()
        self.assertEqual(data['next'], '/')

    def test_login_requires_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        response = strict.post('/login/', data=json.dumps({'username': 'alice', 'password': PASSWORD}),
                               content_type='application/json')
        self.assertEqual(response.status_code, 403)
        strict.get('/login/')                                  # sets the cookie
        token = strict.cookies['csrftoken'].value
        response = strict.post('/login/', data=json.dumps({'username': 'alice', 'password': PASSWORD}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertTrue(response.json()['success'])

    @override_settings(RATE_LIMITS={'auth_per_ip': '3/60'})
    def test_login_attempts_are_rate_limited(self):
        for _ in range(3):
            self.assertEqual(post_json(self.client, '/login/', {'username': 'alice', 'password': 'x'}).status_code, 200)
        blocked = post_json(self.client, '/login/', {'username': 'alice', 'password': PASSWORD})
        self.assertEqual(blocked.status_code, 429)
        other = post_json(self.client, '/login/', {'username': 'alice', 'password': PASSWORD},)
        self.assertEqual(other.status_code, 429)
        elsewhere = self.client.post('/login/', data=json.dumps({'username': 'alice', 'password': PASSWORD}),
                                     content_type='application/json', HTTP_X_FORWARDED_FOR='203.0.113.9')
        self.assertTrue(elsewhere.json()['success'])

    def test_profile_update(self):
        self.client.login(username='alice', password=PASSWORD)
        response = self.client.post('/profile/', {'username': 'alice', 'email': 'a@example.com', 'first_name': '面试官 A'})
        self.assertTrue(response.json()['success'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, '面试官 A')
        room_config = self.client.get('/abc12345/').context['room_config']
        self.assertEqual(room_config['user'], {'authenticated': True, 'name': '面试官 A'})
        User.objects.create_user('taken', password=PASSWORD)
        clash = self.client.post('/profile/', {'username': 'taken', 'email': '', 'first_name': ''})
        self.assertEqual(clash.status_code, 400)


class ProblemPageTests(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.problem = make_problem()
        make_problem('hard-one', '困难题', difficulty='hard', tags='图')
        Problem.objects.create(slug='draft', title='草稿题', description='x', status='draft')

    def test_list_filters(self):
        response = self.client.get('/problems/')
        self.assertContains(response, '求和')
        self.assertContains(response, '困难题')
        self.assertNotContains(response, '草稿题')
        self.assertNotContains(self.client.get('/problems/?difficulty=hard'), '求和')
        self.assertContains(self.client.get('/problems/?search=求'), '求和')
        self.assertNotContains(self.client.get('/problems/?search=求'), '困难题')
        self.assertContains(self.client.get('/problems/?tag=图'), '困难题')
        self.assertContains(self.client.get('/problems/?search=zzz'), '没有符合条件的题目')

    def test_status_filter_for_signed_in_users(self):
        Submission.objects.create(user=self.user, problem=self.problem, code='x', status='accepted')
        self.client.login(username='alice', password=PASSWORD)
        self.assertContains(self.client.get('/problems/?status=solved'), '求和')
        self.assertNotContains(self.client.get('/problems/?status=solved'), '困难题')
        self.assertNotContains(self.client.get('/problems/?status=todo'), '求和')

    def test_detail(self):
        response = self.client.get('/problem/sum/')
        self.assertContains(response, '输出所有整数的**和**。')      # plain text; rendered by md.js
        self.assertContains(response, '示例 1')
        self.assertNotContains(response, '10 20 30')                 # hidden case
        self.assertEqual(self.client.get('/problem/draft/').status_code, 404)
        self.assertEqual(self.client.get('/problem/missing/').status_code, 404)

    def test_statement_is_escaped(self):
        make_problem('xss', '<script>alert(1)</script>', description='<img src=x onerror=alert(1)>')
        response = self.client.get('/problem/xss/')
        self.assertNotContains(response, '<img src=x')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_api_for_the_room_picker(self):
        listing = self.client.get('/api/problems/?q=求').json()
        self.assertEqual([item['slug'] for item in listing['problems']], ['sum'])
        detail = self.client.get('/api/problems/sum/').json()['problem']
        self.assertIn('# 求和', detail['markdown'])
        self.assertIn('## 示例 1', detail['markdown'])
        self.assertEqual(detail['sample_input'], '1 2\n')
        self.assertNotIn('10 20 30', json.dumps(detail))
        self.assertEqual(self.client.get('/api/problems/draft/').status_code, 404)

    def test_bookmarks(self):
        self.assertEqual(post_json(self.client, '/problem/sum/bookmark/').status_code, 401)
        self.client.login(username='alice', password=PASSWORD)
        self.assertEqual(self.client.get('/problem/sum/bookmark/').status_code, 405)
        self.assertTrue(post_json(self.client, '/problem/sum/bookmark/').json()['bookmarked'])
        self.assertEqual(ProblemBookmark.objects.count(), 1)
        self.assertContains(self.client.get('/my-bookmarks/'), '求和')
        self.assertFalse(post_json(self.client, '/problem/sum/bookmark/').json()['bookmarked'])
        self.assertEqual(ProblemBookmark.objects.count(), 0)

    def test_submissions_page_only_shows_own_submissions(self):
        other = User.objects.create_user('bob', password=PASSWORD)
        Submission.objects.create(user=self.user, problem=self.problem, code='mine', status='accepted')
        Submission.objects.create(user=other, problem=self.problem, code='theirs', status='wrong_answer')
        self.client.login(username='alice', password=PASSWORD)
        response = self.client.get('/my-submissions/')
        self.assertEqual(len(response.context['page_obj'].object_list), 1)
        filtered = self.client.get('/my-submissions/?status=wrong_answer')
        self.assertEqual(len(filtered.context['page_obj'].object_list), 0)
        # the "all" pill must drop the filter (an empty href would keep it)
        self.assertContains(filtered, '<a class="pill" href="?">全部</a>', html=True)
        self.assertContains(filtered, 'href="?status=accepted"')


class CustomProblemTests(BaseTestCase):

    def test_crud_and_isolation(self):
        self.assertEqual(post_json(self.client, '/custom-problems/save/', {'name': 'x'}).status_code, 401)
        self.client.login(username='alice', password=PASSWORD)
        self.assertFalse(post_json(self.client, '/custom-problems/save/', {'name': '  '}).json()['success'])
        saved = post_json(self.client, '/custom-problems/save/', {
            'name': 'LRU', 'description': '# LRU 缓存', 'code': 'print(1)', 'language': 'python3',
            'input_text': '1 2'}).json()
        self.assertTrue(saved['success'] and saved['created'])
        again = post_json(self.client, '/custom-problems/save/', {'name': 'LRU', 'description': '改过'}).json()
        self.assertFalse(again['created'])
        self.assertEqual(CustomProblem.objects.count(), 1)
        self.assertEqual(CustomProblem.objects.get().language, 'python')

        listing = self.client.get('/custom-problems/').json()
        self.assertEqual([item['name'] for item in listing['problems']], ['LRU'])
        loaded = self.client.get('/custom-problems/%d/load/' % saved['id']).json()['problem']
        self.assertEqual(loaded['description'], '改过')

        intruder = Client()
        User.objects.create_user('mallory', password=PASSWORD)
        intruder.login(username='mallory', password=PASSWORD)
        self.assertEqual(intruder.get('/custom-problems/%d/load/' % saved['id']).status_code, 404)
        self.assertEqual(post_json(intruder, '/custom-problems/%d/delete/' % saved['id']).status_code, 404)
        self.assertEqual(intruder.get('/custom-problems/').json()['problems'], [])

        self.assertTrue(post_json(self.client, '/custom-problems/%d/delete/' % saved['id']).json()['success'])
        self.assertEqual(CustomProblem.objects.count(), 0)

    def test_oversized_fields_are_rejected(self):
        self.client.login(username='alice', password=PASSWORD)
        response = post_json(self.client, '/custom-problems/save/', {'name': 'big', 'code': 'x' * 100001})
        self.assertEqual(response.status_code, 400)


class PinRoomTests(BaseTestCase):

    def test_pin_consumes_one_credit_per_room(self):
        self.assertEqual(post_json(self.client, '/api/rooms/abc12345/pin/').status_code, 401)
        Membership.objects.create(user=self.user, remaining_rooms=1)
        self.client.login(username='alice', password=PASSWORD)
        first = post_json(self.client, '/api/rooms/abc12345/pin/').json()
        self.assertTrue(first['success'])
        room = RoomState.objects.get(room_name='abc12345')
        self.assertTrue(room.is_vip)
        self.assertEqual(room.owner, self.user)
        self.assertEqual(Membership.objects.get(user=self.user).remaining_rooms, 0)
        # pinning the same room again is free
        self.assertTrue(post_json(self.client, '/api/rooms/abc12345/pin/').json()['already'])
        # no credit left for another room
        refused = post_json(self.client, '/api/rooms/other123/pin/')
        self.assertEqual(refused.status_code, 402)
        self.assertFalse(RoomState.objects.get(room_name='other123').is_vip)

    def test_time_plan_pins_without_using_credits(self):
        Membership.objects.create(user=self.user, remaining_rooms=2, end_date=timezone.now() + timedelta(days=30))
        self.client.login(username='alice', password=PASSWORD)
        for name in ('room-one', 'room-two', 'room-three'):
            self.assertTrue(post_json(self.client, '/api/rooms/%s/pin/' % name).json()['success'])
        self.assertEqual(Membership.objects.get(user=self.user).remaining_rooms, 2)


class CleanupRoomsTests(BaseTestCase):

    def test_only_old_unpinned_rooms_are_deleted(self):
        from django.core.management import call_command
        from io import StringIO
        old = timezone.now() - timedelta(days=45)
        for name, pinned, stamp in (('old-free', False, old), ('old-pinned', True, old),
                                    ('fresh', False, timezone.now())):
            RoomState.objects.create(room_name=name, is_vip=pinned)
            RoomState.objects.filter(room_name=name).update(updated_at=stamp)
        output = StringIO()
        call_command('cleanup_rooms', dry_run=True, stdout=output)
        self.assertIn('1 rooms', output.getvalue())
        self.assertEqual(RoomState.objects.count(), 3)
        call_command('cleanup_rooms', stdout=StringIO())
        self.assertEqual(sorted(RoomState.objects.values_list('room_name', flat=True)), ['fresh', 'old-pinned'])


class RunAndSubmitTests(TransactionTestCase):
    """End to end through the sandbox worker threads."""

    def setUp(self):
        ratelimit.reset()
        self.user = User.objects.create_user('alice', password=PASSWORD)
        self.problem = make_problem()

    def poll(self, client, url, done, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            data = client.get(url).json()
            if done(data):
                return data
            time.sleep(0.05)
        self.fail('timed out waiting for %s' % url)

    def test_anonymous_run(self):
        started = post_json(self.client, '/problem/sum/run/', {
            'language': 'python', 'code': 'print(sum(map(int, input().split())))', 'input': '4 5\n'}).json()
        self.assertTrue(started['success'], started)
        job = self.poll(self.client, '/api/jobs/%s/' % started['job_id'], lambda data: data.get('done'))
        self.assertEqual(job['result']['status'], 'ok')
        self.assertEqual(job['result']['stdout'], '9\n')
        # somebody else cannot read the result
        self.assertEqual(Client().get('/api/jobs/%s/' % started['job_id']).status_code, 404)

    def test_run_validation(self):
        self.assertEqual(post_json(self.client, '/problem/sum/run/', {'language': 'python', 'code': ' '}).status_code, 400)
        self.assertEqual(post_json(self.client, '/problem/sum/run/', {'language': 'cobol', 'code': 'x'}).status_code, 400)
        self.assertEqual(post_json(self.client, '/problem/missing/run/', {'language': 'python', 'code': 'x'}).status_code, 404)
        self.assertEqual(self.client.get('/problem/sum/run/').status_code, 405)

    @override_settings(RATE_LIMITS={'run_per_ip': '2/60'})
    def test_runs_are_rate_limited(self):
        body = {'language': 'python', 'code': 'print(1)'}
        self.assertEqual(post_json(self.client, '/problem/sum/run/', body).status_code, 200)
        self.assertEqual(post_json(self.client, '/problem/sum/run/', body).status_code, 200)
        self.assertEqual(post_json(self.client, '/problem/sum/run/', body).status_code, 429)

    def test_submit_and_poll_verdict(self):
        body = {'language': 'python', 'code': 'import sys\nprint(sum(map(int, sys.stdin.read().split())))'}
        self.assertEqual(post_json(self.client, '/problem/sum/submit/', body).status_code, 401)
        self.client.login(username='alice', password=PASSWORD)
        created = post_json(self.client, '/problem/sum/submit/', body).json()
        self.assertTrue(created['success'], created)
        verdict = self.poll(self.client, '/submission/%d/' % created['submission_id'], lambda data: data.get('done'))
        self.assertEqual(verdict['status'], 'accepted')
        self.assertEqual((verdict['score'], verdict['total_score']), (2, 2))
        self.assertEqual(len(verdict['test_results']), 2)
        self.assertNotIn('code', verdict)

        stranger = Client()
        User.objects.create_user('bob', password=PASSWORD)
        stranger.login(username='bob', password=PASSWORD)
        self.assertEqual(stranger.get('/submission/%d/' % created['submission_id']).status_code, 403)
        self.assertEqual(Client().get('/submission/%d/' % created['submission_id']).status_code, 401)

        self.assertContains(self.client.get('/problem/sum/'), '已通过')
        self.assertContains(self.client.get('/my-submissions/'), '通过')


class ClientAddressTests(TestCase):

    def test_proxy_headers(self):
        from editor.ratelimit import client_ip_from_headers
        self.assertEqual(client_ip_from_headers({'x-real-ip': '198.51.100.7', 'x-forwarded-for': '1.1.1.1'}, '10.0.0.1'),
                         '198.51.100.7')
        # a client-supplied X-Forwarded-For prefix must not choose the bucket
        self.assertEqual(client_ip_from_headers({'x-forwarded-for': '6.6.6.6, 198.51.100.7'}, ''), '198.51.100.7')
        self.assertEqual(client_ip_from_headers({}, '10.0.0.1'), '10.0.0.1')
        self.assertEqual(client_ip_from_headers({}, ''), 'unknown')
        with override_settings(TRUST_PROXY_HEADERS=False):
            self.assertEqual(client_ip_from_headers({'x-real-ip': '198.51.100.7'}, '10.0.0.1'), '10.0.0.1')

    @override_settings(RATE_LIMITS={'x': '1/60'})
    def test_unknown_clients_are_not_lumped_together(self):
        ratelimit.reset()
        self.assertTrue(ratelimit.allow('x', 'unknown'))
        self.assertTrue(ratelimit.allow('x', 'unknown'))
        self.assertTrue(ratelimit.allow('x', '203.0.113.5'))
        self.assertFalse(ratelimit.allow('x', '203.0.113.5'))
        self.assertTrue(ratelimit.allow('x', '203.0.113.6'))
