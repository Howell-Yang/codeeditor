"""Queue for everything that executes user code.

A fixed pool of worker threads runs the sandbox, so at most
``SANDBOX['MAX_CONCURRENCY']`` programs run at the same time no matter how
many people press "run". HTTP pages submit a job and poll for its result;
rooms await the future directly.
"""
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

from . import sandbox

logger = logging.getLogger(__name__)

MAX_CODE_CHARS = 100000
MAX_STDIN_CHARS = 100000
JOB_TTL_SECONDS = 600
MAX_QUEUED = 50

_lock = threading.Lock()
_executor = None
_executor_size = 0
_jobs = {}
_pending = [0]


class QueueFull(Exception):
    pass


def get_executor():
    global _executor, _executor_size
    size = max(1, int(settings.SANDBOX['MAX_CONCURRENCY']))
    with _lock:
        if _executor is None or _executor_size != size:
            _executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix='sandbox')
            _executor_size = size
        return _executor


def submit(function, *args, **kwargs):
    """Schedule ``function`` on the sandbox workers and return its Future."""
    with _lock:
        if _pending[0] >= MAX_QUEUED:
            raise QueueFull()
        _pending[0] += 1

    def call():
        try:
            return function(*args, **kwargs)
        except Exception:
            # Nobody may be waiting on the future: never lose an error silently.
            logger.exception('background job %s failed', getattr(function, '__name__', function))
            raise
        finally:
            with _lock:
                _pending[0] -= 1

    return get_executor().submit(call)


def validate_program(language, code, stdin_text):
    """Return an error message for input we refuse to run, else ''."""
    if sandbox.get_language(language) is None:
        return '不支持的语言'
    if not isinstance(code, str) or not code.strip():
        return '代码不能为空'
    if len(code) > MAX_CODE_CHARS:
        return '代码过长（最多 %d 个字符）' % MAX_CODE_CHARS
    if not isinstance(stdin_text, str):
        return '输入格式不正确'
    if len(stdin_text) > MAX_STDIN_CHARS:
        return '输入过长（最多 %d 个字符）' % MAX_STDIN_CHARS
    return ''


def run_program(language, code, stdin_text='', limits=None):
    """Blocking: compile and run once. Returns the sandbox result dict."""
    return sandbox.run_code(language, code, stdin_text, limits)


# --------------------------------------------------------------------------
# Polled jobs (used by the problem pages)
# --------------------------------------------------------------------------

def _purge_jobs(now):
    for job_id in [key for key, job in _jobs.items() if now - job['created'] > JOB_TTL_SECONDS]:
        del _jobs[job_id]


def start_job(owner, function, *args, **kwargs):
    """Run ``function`` in the background; returns a job id to poll with."""
    job_id = secrets.token_urlsafe(16)
    now = time.time()
    job = {'created': now, 'owner': owner, 'done': False, 'result': None}
    with _lock:
        _purge_jobs(now)
        _jobs[job_id] = job

    def work():
        try:
            result = function(*args, **kwargs)
        except Exception:
            logger.exception('execution job failed')
            result = {'status': sandbox.STATUS_INTERNAL_ERROR, 'message': '运行环境出错，请稍后再试'}
        with _lock:
            job['result'] = result
            job['done'] = True

    try:
        submit(work)
    except QueueFull:
        with _lock:
            _jobs.pop(job_id, None)
        raise
    return job_id


def get_job(job_id, owner):
    """The job dict (``done``/``result``) or None if unknown or not the caller's."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job['owner'] != owner:
            return None
        return {'done': job['done'], 'result': job['result']}
