import json
import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from editor import judge, problem_seeds
from editor.models import Problem, Submission, TestCase as Case

ECHO_SUM = 'import sys\nprint(sum(map(int, sys.stdin.read().split())))\n'


def make_problem(**fields):
    defaults = {'title': '求和', 'slug': 'sum', 'description': '输出所有整数的和', 'status': 'published'}
    defaults.update(fields)
    problem = Problem.objects.create(**defaults)
    Case.objects.create(problem=problem, input_data='1 2\n', expected_output='3\n',
                        is_sample=True, is_hidden=False, order=1)
    Case.objects.create(problem=problem, input_data='10 20 30\n', expected_output='60\n', order=2)
    Case.objects.create(problem=problem, input_data='-5 5\n', expected_output='0\n', order=3)
    return problem


class OutputComparisonTests(TestCase):

    def test_trailing_whitespace_is_ignored(self):
        self.assertTrue(judge.outputs_match('1 2 \n3\n\n\n', '1 2\n3'))
        self.assertTrue(judge.outputs_match('a\r\nb\r\n', 'a\nb\n'))
        self.assertTrue(judge.outputs_match('', '\n'))

    def test_line_structure_matters(self):
        self.assertFalse(judge.outputs_match('1 2\n3\n', '1\n2 3\n'))
        self.assertFalse(judge.outputs_match('1  2\n', '1 2\n'))
        self.assertFalse(judge.outputs_match(' 1\n', '1\n'))


class JudgeProgramTests(TestCase):

    def setUp(self):
        self.problem = make_problem()

    def test_accepted(self):
        outcome = judge.judge_program(self.problem, 'python', ECHO_SUM)
        self.assertEqual(outcome['status'], 'accepted')
        self.assertEqual((outcome['score'], outcome['total_score']), (3, 3))
        self.assertEqual([entry['verdict'] for entry in outcome['test_results']], ['accepted'] * 3)
        self.assertIsNotNone(outcome['execution_time'])
        self.assertGreater(outcome['memory_used'], 0)

    def test_wrong_answer_shows_samples_but_not_hidden_cases(self):
        outcome = judge.judge_program(self.problem, 'python', 'print(3)')
        self.assertEqual(outcome['status'], 'wrong_answer')
        self.assertEqual(outcome['score'], 1)
        sample, hidden = outcome['test_results'][0], outcome['test_results'][1]
        self.assertTrue(sample['passed'])
        self.assertEqual(sample['input'], '1 2\n')
        self.assertFalse(hidden['passed'])
        self.assertTrue(hidden['hidden'])
        for secret in ('input', 'expected', 'actual', 'stderr'):
            self.assertNotIn(secret, hidden)
        self.assertIn('用例 2', outcome['error_message'])

    def test_hidden_case_does_not_leak_through_stderr(self):
        code = 'import sys\ndata = sys.stdin.read()\nsys.stderr.write(data)\nsys.exit(1)\n'
        outcome = judge.judge_program(self.problem, 'python', code)
        self.assertEqual(outcome['status'], 'runtime_error')
        self.assertNotIn('10 20 30', json.dumps(outcome, ensure_ascii=False))

    def test_compilation_error(self):
        outcome = judge.judge_program(self.problem, 'cpp', 'int main() { return nope; }')
        if 'main.cpp' not in outcome['error_message']:     # g++ missing on this host
            self.assertEqual(outcome['status'], 'system_error')
            return
        self.assertEqual(outcome['status'], 'compilation_error')
        self.assertEqual(outcome['test_results'], [])

    def test_time_limit_skips_the_remaining_cases(self):
        self.problem.time_limit = 300
        self.problem.save()
        outcome = judge.judge_program(self.problem, 'python', 'while True:\n    pass\n')
        self.assertEqual(outcome['status'], 'time_limit_exceeded')
        self.assertEqual([entry['verdict'] for entry in outcome['test_results']],
                         ['time_limit_exceeded', 'skipped', 'skipped'])

    def test_problem_without_cases_cannot_be_judged(self):
        empty = Problem.objects.create(title='空', slug='empty', description='x', status='published')
        outcome = judge.judge_program(empty, 'python', 'print(1)')
        self.assertEqual(outcome['status'], 'system_error')

    def test_unknown_language(self):
        outcome = judge.judge_program(self.problem, 'cobol', 'x')
        self.assertEqual(outcome['status'], 'system_error')

    def test_limits_follow_the_language(self):
        from editor import sandbox
        python_limits = judge.limits_for(self.problem, sandbox.get_language('python'))
        cpp_limits = judge.limits_for(self.problem, sandbox.get_language('cpp'))
        self.assertEqual(python_limits.cpu_seconds, 3 * cpp_limits.cpu_seconds)
        self.assertEqual(python_limits.memory_mb, self.problem.memory_limit)


class JudgeSubmissionTests(TransactionTestCase):

    def setUp(self):
        self.problem = make_problem()
        self.user = User.objects.create_user('alice', password='correct horse')

    def wait(self, submission, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            submission.refresh_from_db()
            if submission.is_final:
                return submission
            time.sleep(0.05)
        self.fail('submission was not judged in time')

    def test_enqueue_judges_in_the_background_and_updates_counters(self):
        submission = Submission.objects.create(user=self.user, problem=self.problem, code=ECHO_SUM, language='python')
        self.assertTrue(judge.enqueue(submission))
        submission = self.wait(submission)
        self.assertEqual(submission.status, 'accepted')
        self.assertEqual(len(submission.get_test_results()), 3)
        self.assertIsNotNone(submission.judged_at)
        self.problem.refresh_from_db()
        self.assertEqual((self.problem.submission_count, self.problem.accepted_count), (1, 1))

        wrong = Submission.objects.create(user=self.user, problem=self.problem, code='print(0)', language='python')
        judge.enqueue(wrong)
        self.assertEqual(self.wait(wrong).status, 'wrong_answer')
        self.problem.refresh_from_db()
        self.assertEqual((self.problem.submission_count, self.problem.accepted_count), (2, 1))

    def test_stale_pending_submission_is_closed(self):
        submission = Submission.objects.create(user=self.user, problem=self.problem, code='x', language='python')
        self.assertEqual(judge.recover_if_stale(submission).status, 'pending')   # still fresh
        Submission.objects.filter(pk=submission.pk).update(submitted_at=timezone.now() - timedelta(minutes=10))
        submission.refresh_from_db()
        self.assertEqual(judge.recover_if_stale(submission).status, 'system_error')


class SeedTests(TestCase):

    def test_specifications_are_complete(self):
        slugs = [spec['slug'] for spec in problem_seeds.PROBLEMS]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertGreaterEqual(len(slugs), 25)
        categories = set(slug for slug, _, _ in problem_seeds.CATEGORIES)
        for spec in problem_seeds.PROBLEMS:
            self.assertIn(spec['category'], categories, spec['slug'])
            self.assertIn(spec['difficulty'], ('easy', 'medium', 'hard'), spec['slug'])
            self.assertGreaterEqual(len(spec['samples']), 2, spec['slug'])
            for key in ('title', 'description', 'input_format', 'output_format', 'constraints', 'solution'):
                self.assertTrue(spec[key].strip(), (spec['slug'], key))

    def test_cases_are_deterministic(self):
        spec = problem_seeds.PROBLEMS[0]
        self.assertEqual(problem_seeds.build_cases(spec), problem_seeds.build_cases(spec))

    def test_seed_command_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_problems', only=['two-sum', 'climbing-stairs'], stdout=StringIO())
        self.assertEqual(Problem.objects.count(), 2)
        problem = Problem.objects.get(slug='two-sum')
        cases = problem.test_cases.count()
        self.assertGreater(cases, 4)
        self.assertEqual(problem.test_cases.filter(is_sample=True).count(), 2)
        Problem.objects.filter(pk=problem.pk).update(title='改过的标题')
        call_command('seed_problems', only=['two-sum', 'climbing-stairs'], stdout=StringIO())
        self.assertEqual(Problem.objects.get(slug='two-sum').title, '改过的标题')     # left alone
        call_command('seed_problems', only=['two-sum'], update=True, stdout=StringIO())
        problem.refresh_from_db()
        self.assertEqual(problem.title, '两数之和')
        self.assertEqual(problem.test_cases.count(), cases)

    def test_every_reference_solution_is_accepted(self):
        """Statement, generated tests and reference solution agree, judged for real."""
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_problems', stdout=StringIO())
        for problem in Problem.objects.all():
            outcome = judge.judge_program(problem, 'python', problem.solution)
            self.assertEqual(outcome['status'], 'accepted', (problem.slug, outcome['error_message']))
            limit = problem.time_limit * 3
            self.assertLess(outcome['execution_time'], limit / 2.0, problem.slug)
