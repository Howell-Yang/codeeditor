"""Judge submissions of the problem bank.

A submission is compiled once and then run against every test case of its
problem inside the sandbox. Judging happens on the sandbox worker threads
(``editor.execution``); the page polls ``/submission/<id>/`` for the verdict.
"""
import json
import logging
import threading
import time

from django.db import close_old_connections, transaction
from django.db.models import F
from django.db.utils import OperationalError
from django.utils import timezone

from . import execution, sandbox
from .models import Problem, Submission

logger = logging.getLogger(__name__)

MAX_SHOWN_CHARS = 2000        # per field shown for a visible test case
MAX_ERROR_CHARS = 4000
TOTAL_WALL_BUDGET = 60.0      # seconds for all test cases of one submission
JUDGE_OUTPUT_KB = 8 * 1024    # answers can be long (e.g. all permutations)
STALE_AFTER_SECONDS = 180

_STATUS_MAP = {
    sandbox.STATUS_TIME_LIMIT: 'time_limit_exceeded',
    sandbox.STATUS_MEMORY_LIMIT: 'memory_limit_exceeded',
    sandbox.STATUS_OUTPUT_LIMIT: 'output_limit_exceeded',
    sandbox.STATUS_RUNTIME_ERROR: 'runtime_error',
    sandbox.STATUS_INTERNAL_ERROR: 'system_error',
}

_active_lock = threading.Lock()
_active = set()


def normalize_output(text):
    """Lines without trailing whitespace, without trailing blank lines."""
    lines = [line.rstrip() for line in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    while lines and lines[-1] == '':
        lines.pop()
    return lines


def outputs_match(actual, expected):
    return normalize_output(actual) == normalize_output(expected)


def _clip(text, limit=MAX_SHOWN_CHARS):
    text = text or ''
    if len(text) <= limit:
        return text
    return text[:limit] + '\n…（已截断）'


def limits_for(problem, language):
    """Sandbox limits of ``problem`` for a program written in ``language``."""
    factor = language.time_factor if language is not None else 1
    cpu_seconds = max(0.1, problem.time_limit / 1000.0) * factor
    return sandbox.Limits(
        cpu_seconds=cpu_seconds,
        wall_seconds=cpu_seconds * 2 + 1,
        memory_mb=max(16, problem.memory_limit),
        output_kb=JUDGE_OUTPUT_KB,
    )


def judge_program(problem, language_key, code):
    """Run ``code`` against all test cases of ``problem``.

    Returns a dict with ``status``, ``score``, ``total_score``,
    ``execution_time`` (ms), ``memory_used`` (KB), ``error_message`` and
    ``test_results``. Never raises for problems in the user's program.
    """
    cases = list(problem.test_cases.all())
    outcome = {
        'status': 'system_error',
        'score': 0,
        'total_score': sum(case.points for case in cases),
        'execution_time': None,
        'memory_used': None,
        'error_message': '',
        'test_results': [],
    }
    if not cases:
        outcome['error_message'] = '这道题还没有测试用例，暂时无法判题'
        return outcome

    language = sandbox.get_language(language_key)
    limits = limits_for(problem, language)
    limit_ms = int(limits.cpu_seconds * 1000)
    started = time.monotonic()
    results = []
    first_failure = None
    slowest = 0
    peak_memory = 0
    earned = 0

    try:
        with sandbox.Session(language_key, code) as session:
            compiled = session.compile()
            if compiled.status == sandbox.STATUS_INTERNAL_ERROR:
                outcome['error_message'] = '判题环境出错，请稍后重试'
                return outcome
            if not compiled.ok:
                outcome['status'] = 'compilation_error'
                outcome['error_message'] = _clip(
                    (compiled.stderr or compiled.stdout or compiled.message).strip(), MAX_ERROR_CHARS)
                return outcome

            stop_reason = ''
            for index, case in enumerate(cases, 1):
                visible = case.is_sample or not case.is_hidden
                entry = {
                    'index': index,
                    'is_sample': case.is_sample,
                    'hidden': not visible,
                    'points': case.points,
                    'verdict': 'skipped',
                    'passed': False,
                    'time_ms': None,
                    'memory_kb': None,
                }
                if stop_reason:
                    entry['note'] = stop_reason
                    results.append(entry)
                    continue

                result = session.run(case.input_data, limits)
                entry['time_ms'] = result.cpu_ms
                entry['memory_kb'] = result.memory_kb
                slowest = max(slowest, result.cpu_ms)
                peak_memory = max(peak_memory, result.memory_kb)

                if result.status == sandbox.STATUS_INTERNAL_ERROR:
                    logger.error('judge: sandbox failure: %s', result.message)
                    outcome['error_message'] = '判题环境出错，请稍后重试'
                    outcome['test_results'] = results
                    return outcome

                if result.ok and result.cpu_ms > limit_ms:
                    verdict = 'time_limit_exceeded'
                elif result.ok:
                    verdict = 'accepted' if outputs_match(result.stdout, case.expected_output) else 'wrong_answer'
                else:
                    verdict = _STATUS_MAP.get(result.status, 'runtime_error')
                entry['verdict'] = verdict
                entry['passed'] = verdict == 'accepted'

                if visible:
                    entry['input'] = _clip(case.input_data)
                    entry['expected'] = _clip(case.expected_output)
                    entry['actual'] = _clip(result.stdout)
                    if result.stderr and verdict != 'accepted':
                        entry['stderr'] = _clip(result.stderr[-MAX_SHOWN_CHARS:])
                if verdict not in ('accepted', 'wrong_answer'):
                    entry['note'] = result.message

                if entry['passed']:
                    earned += case.points
                elif first_failure is None:
                    first_failure = entry
                results.append(entry)

                if verdict == 'time_limit_exceeded':
                    stop_reason = '前面的用例超时，未运行'
                elif time.monotonic() - started > TOTAL_WALL_BUDGET:
                    stop_reason = '判题总时间已用完，未运行'
    except sandbox.UnsupportedLanguage as exc:
        outcome['error_message'] = str(exc)
        return outcome
    except sandbox.SandboxError as exc:
        logger.error('judge: %s', exc)
        outcome['error_message'] = '判题环境暂时不可用，请稍后重试'
        return outcome

    outcome['test_results'] = results
    outcome['score'] = earned
    outcome['execution_time'] = slowest
    outcome['memory_used'] = peak_memory
    if first_failure is None:
        outcome['status'] = 'accepted'
    else:
        outcome['status'] = first_failure['verdict']
        label = '示例' if first_failure['is_sample'] else '用例'
        message = '%s %d 未通过' % (label, first_failure['index'])
        if first_failure.get('note'):
            message += '：' + first_failure['note']
        outcome['error_message'] = message
    return outcome


def _with_retry(operation, attempts=10):
    """Run a short database operation, retrying while SQLite reports a lock."""
    delay = 0.05
    for attempt in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            if 'locked' not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def _internal_error(message='判题时出现内部错误'):
    return {
        'status': 'system_error', 'score': 0, 'total_score': 0,
        'execution_time': None, 'memory_used': None,
        'error_message': message, 'test_results': [],
    }


def _store_outcome(submission_id, problem_id, outcome):
    """Store the verdict together with the problem's counters.

    One transaction: whoever sees the final status (the page polls for it)
    also sees the counters that go with it.
    """
    def write():
        with transaction.atomic():
            Submission.objects.filter(pk=submission_id).update(
                status=outcome['status'],
                score=outcome['score'],
                total_score=outcome['total_score'],
                execution_time=outcome['execution_time'],
                memory_used=outcome['memory_used'],
                error_message=outcome['error_message'],
                test_results=json.dumps(outcome['test_results'], ensure_ascii=False),
                judged_at=timezone.now(),
            )
            if outcome['status'] != 'system_error':
                counters = {'submission_count': F('submission_count') + 1}
                if outcome['status'] == 'accepted':
                    counters['accepted_count'] = F('accepted_count') + 1
                Problem.objects.filter(pk=problem_id).update(**counters)

    _with_retry(write)


def judge_submission(submission_id):
    """Judge one stored submission (runs on a sandbox worker thread)."""
    close_old_connections()
    try:
        try:
            submission = _with_retry(
                lambda: Submission.objects.select_related('problem').get(pk=submission_id))
        except Submission.DoesNotExist:
            return
        try:
            _with_retry(lambda: Submission.objects.filter(pk=submission_id).update(status='judging'))
            outcome = judge_program(submission.problem, submission.language, submission.code)
        except Exception:
            logger.exception('judging submission %s failed', submission_id)
            outcome = _internal_error()
        try:
            _store_outcome(submission_id, submission.problem_id, outcome)
        except Exception:
            # Leave no submission "pending" forever if the result cannot be stored.
            logger.exception('storing the verdict of submission %s failed', submission_id)
            _with_retry(lambda: Submission.objects.filter(pk=submission_id).update(
                status='system_error', error_message='判题结果保存失败，请重新提交', judged_at=timezone.now()))
    finally:
        with _active_lock:
            _active.discard(submission_id)
        close_old_connections()


def enqueue(submission):
    """Queue ``submission`` for judging. Returns False when the queue is full."""
    with _active_lock:
        _active.add(submission.pk)
    try:
        execution.submit(judge_submission, submission.pk)
    except execution.QueueFull:
        with _active_lock:
            _active.discard(submission.pk)
        Submission.objects.filter(pk=submission.pk).update(
            status='system_error', error_message='当前排队的任务太多，请稍后再提交', judged_at=timezone.now())
        return False
    return True


def recover_if_stale(submission):
    """A submission whose judging died with a restarted server would stay
    "pending" forever; close it so the page stops polling."""
    if submission.is_final:
        return submission
    with _active_lock:
        if submission.pk in _active:
            return submission
    age = (timezone.now() - submission.submitted_at).total_seconds()
    if age < STALE_AFTER_SECONDS:
        return submission
    Submission.objects.filter(pk=submission.pk, status__in=('pending', 'judging')).update(
        status='system_error', error_message='判题被中断（服务重启），请重新提交', judged_at=timezone.now())
    submission.refresh_from_db()
    return submission
