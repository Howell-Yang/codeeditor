"""Run untrusted programs with as much containment as the host allows.

What every run gets, using nothing but the standard library:

* its own throw-away working directory and a clean environment;
* CPU time, address space, file size, open files and core dump limits;
* a wall-clock timeout after which the whole process group is killed;
* output written to size-capped files (no pipe can block or flood memory);
* real CPU time and peak memory, read from the kernel's accounting of that
  one child.

When the server runs as root it additionally

* switches to an unprivileged uid that is used by one run at a time (so
  concurrent runs cannot read each other's files), with a process-count limit,
* removes network access by entering an empty network namespace,
* kills whatever is left under that uid when the run is over.

This is hardening, not a container: the program can still read files that are
world-readable on the host. See the README for what to put in front of it.
"""
import errno
import json
import logging
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

from django.conf import settings

from .languages import find_binary, get_language

logger = logging.getLogger(__name__)

LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'launcher.py')
LAUNCHER_SETUP_FAILED = 120

STATUS_OK = 'ok'
STATUS_COMPILE_ERROR = 'compile_error'
STATUS_TIME_LIMIT = 'time_limit'
STATUS_MEMORY_LIMIT = 'memory_limit'
STATUS_OUTPUT_LIMIT = 'output_limit'
STATUS_RUNTIME_ERROR = 'runtime_error'
STATUS_INTERNAL_ERROR = 'internal_error'

MAX_STDERR_BYTES = 64 * 1024
FILE_SIZE_LIMIT = 16 * 1024 * 1024
COMPILE_ADDRESS_SPACE = 2048 * 1024 * 1024
COMPILE_FILE_SIZE_LIMIT = 128 * 1024 * 1024

# RLIMIT_AS counts virtual memory. On top of the advertised memory limit a
# program gets this much slack (allocator overhead, shared libraries) plus
# whatever its bare runtime occupies on this host (runtime_footprint_kb).
ADDRESS_SPACE_SLACK_MB = 64
MAX_RUNTIME_FOOTPRINT_KB = 1024 * 1024

# The launcher itself runs in the plain C locale: with any other locale an
# interpreter may map the whole glibc locale archive (100 MB on CentOS 7).
LAUNCHER_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}

_OUT_OF_MEMORY_HINTS = (
    'MemoryError',
    'std::bad_alloc',
    'Cannot allocate memory',
    'JavaScript heap out of memory',
    'out of memory',
)


class SandboxError(Exception):
    """The sandbox itself could not do its job (not the user's fault)."""


class UnsupportedLanguage(SandboxError):
    pass


class Limits(object):
    """Limits for one execution."""

    def __init__(self, cpu_seconds=None, wall_seconds=None, memory_mb=None, output_kb=None):
        config = settings.SANDBOX
        self.cpu_seconds = float(cpu_seconds if cpu_seconds is not None else config['RUN_CPU_SECONDS'])
        self.wall_seconds = float(wall_seconds if wall_seconds is not None else config['RUN_WALL_SECONDS'])
        self.memory_mb = int(memory_mb if memory_mb is not None else config['RUN_MEMORY_MB'])
        self.output_kb = int(output_kb if output_kb is not None else config['OUTPUT_KB'])


class RunResult(object):
    """Outcome of one compile or run step."""

    __slots__ = ('status', 'stdout', 'stderr', 'exit_code', 'signal', 'cpu_ms',
                 'wall_ms', 'memory_kb', 'truncated', 'message')

    def __init__(self, status=STATUS_OK, stdout='', stderr='', exit_code=None, signal_number=None,
                 cpu_ms=0, wall_ms=0, memory_kb=0, truncated=False, message=''):
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.signal = signal_number
        self.cpu_ms = cpu_ms
        self.wall_ms = wall_ms
        self.memory_kb = memory_kb
        self.truncated = truncated
        self.message = message

    @property
    def ok(self):
        return self.status == STATUS_OK

    def to_dict(self):
        return {
            'status': self.status,
            'stdout': self.stdout,
            'stderr': self.stderr,
            'exit_code': self.exit_code,
            'signal': self.signal,
            'cpu_ms': self.cpu_ms,
            'wall_ms': self.wall_ms,
            'memory_kb': self.memory_kb,
            'truncated': self.truncated,
            'message': self.message,
        }


# --------------------------------------------------------------------------
# Host facts
# --------------------------------------------------------------------------

def running_as_root():
    return os.geteuid() == 0


def sandbox_root():
    """Directory that holds the per-run working directories."""
    root = settings.SANDBOX.get('ROOT') or os.path.join(tempfile.gettempdir(), 'codetool-sandbox-%d' % os.getuid())
    try:
        os.mkdir(root, 0o711)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise SandboxError('cannot create %s: %s' % (root, exc))
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SandboxError('%s is not a directory owned by the server user' % root)
    if stat.S_IMODE(info.st_mode) != 0o711:
        os.chmod(root, 0o711)
    return root


class _UidPool(object):
    """Hands out the sandbox uids; a uid is used by one run at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = None
        self._size = 0

    def _ensure(self):
        size = max(1, int(settings.SANDBOX['MAX_CONCURRENCY']))
        with self._lock:
            if self._queue is None or self._size != size:
                self._queue = queue.Queue()
                self._size = size
                for slot in range(size):
                    self._queue.put(slot)
            return self._queue

    def acquire(self, timeout=120):
        pool = self._ensure()
        try:
            return pool, pool.get(timeout=timeout)
        except queue.Empty:
            raise SandboxError('all sandbox slots are busy')

    @staticmethod
    def release(pool, slot):
        pool.put(slot)


_uid_pool = _UidPool()


def _base_environment(workdir, extra_path_entries=()):
    path_entries = []
    for entry in list(extra_path_entries) + [
            '/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin', '/sbin', '/bin']:
        if entry and entry not in path_entries:
            path_entries.append(entry)
    return {
        'PATH': os.pathsep.join(path_entries),
        'HOME': workdir,
        'TMPDIR': workdir,
        'LANG': 'en_US.UTF-8',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    }


_footprint_lock = threading.Lock()
_footprint_cache = {}


def runtime_footprint_kb(language, binary=None):
    """Address space (kB) the idle runtime of ``language`` occupies on this host.

    The address space limit is the only memory limit an unprivileged process
    can be given, and it counts mappings, not memory in use. How much an
    interpreter maps just to start differs by a factor of five between hosts
    (the same CPython: 25 MB on one distribution, 128 MB on CentOS 7, where it
    maps the glibc locale archive). Measuring it once and adding it to the
    limit lets a program use its advertised memory everywhere, instead of
    failing to start under a small limit. 0 for compiled languages and
    whenever the measurement is not possible.
    """
    binary = binary or find_binary(language)
    if not binary:
        return 0
    with _footprint_lock:
        if binary in _footprint_cache:
            return _footprint_cache[binary]
    footprint = 0
    command = language.footprint_command(binary)
    if command:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                # The same environment as a real run: the locale is what decides
                # how much gets mapped. "/" keeps a writable directory out of it.
                env=_base_environment('/', [os.path.dirname(binary)]), cwd='/',
                universal_newlines=True, timeout=10,
            )
            if completed.returncode == 0:
                footprint = int((completed.stdout or '').strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            footprint = 0
        if not 0 < footprint <= MAX_RUNTIME_FOOTPRINT_KB:
            footprint = 0
    with _footprint_lock:
        _footprint_cache[binary] = footprint
    return footprint


def reset_caches():
    with _footprint_lock:
        _footprint_cache.clear()


def _read_capped(path, limit):
    """(text, truncated) with at most ``limit`` bytes read from ``path``."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as handle:
            data = handle.read(limit)
    except (IOError, OSError):
        return '', False
    return data.decode('utf-8', 'replace'), size > limit


def _quiet(command, timeout=60):
    try:
        subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass


def _remove_tree(path):
    """Delete a working directory whatever the program left in it.

    Returns True when it is gone. Programs can leave trees that defeat
    ``shutil.rmtree``: thousands of nested directories (it needs a stack
    frame and an open file per level) or directories without permissions.
    ``rm`` copes with both, so it is the fallback.
    """
    try:
        shutil.rmtree(path)
    except Exception:           # including RecursionError, which is not an OSError
        pass
    if os.path.lexists(path):
        if not running_as_root():
            # Root needs no permissions. As an ordinary user, give them back
            # first; "chmod -R" does not follow the symlinks it meets.
            _quiet(['chmod', '-R', 'u+rwX', '--', path])
        _quiet(['rm', '-rf', '--', path])
    return not os.path.lexists(path)


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------

class Session(object):
    """One program: write the source, compile once, run as often as needed.

        with Session('cpp', code) as session:
            compiled = session.compile()
            if compiled.ok:
                result = session.run('1 2\\n', Limits())
    """

    def __init__(self, language, code):
        self.language = get_language(language)
        if self.language is None:
            raise UnsupportedLanguage('不支持的语言：%s' % language)
        self.binary = find_binary(self.language)
        if not self.binary:
            raise UnsupportedLanguage('服务器上没有安装 %s 的运行环境' % self.language.name)
        self.code = code
        self.workdir = None
        self.outdir = None
        self.uid = None
        self._pool = None
        self._slot = None
        self._counter = 0
        self._compiled = False

    # -- lifecycle --

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def open(self):
        root = sandbox_root()
        self._pool, self._slot = _uid_pool.acquire()
        try:
            if running_as_root():
                # Never execute user code as root: every run gets its own
                # unprivileged uid. Leftovers of an earlier run under that uid
                # must not be able to watch this one.
                self.uid = int(settings.SANDBOX['UID_BASE']) + self._slot
                self._reap()
            self.workdir = tempfile.mkdtemp(prefix='run-', dir=root)
            self.outdir = tempfile.mkdtemp(prefix='out-', dir=root)
            source_path = os.path.join(self.workdir, self.language.source_name)
            with open(source_path, 'w', encoding='utf-8', errors='replace', newline='\n') as handle:
                handle.write(self.language.prepare_source(self.code))
            os.chmod(source_path, 0o644)
            if self.uid is not None:
                os.chown(self.workdir, self.uid, self.uid)
            os.chmod(self.workdir, 0o700)
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self.uid is not None:
                self._reap()
            for path in (self.workdir, self.outdir):
                if path and not _remove_tree(path):
                    logger.error('could not remove the sandbox directory %s', path)
        except Exception:
            logger.exception('cleaning up a sandbox session failed')
        finally:
            # Whatever happened above, the slot must go back: a leaked slot
            # would block every later run.
            self.workdir = None
            self.outdir = None
            if self._pool is not None:
                pool, slot = self._pool, self._slot
                self._pool = None
                _uid_pool.release(pool, slot)

    def _reap(self):
        """Kill everything still running under this session's uid (root only)."""
        try:
            subprocess.run(
                [sys.executable, '-I', '-S', LAUNCHER, 'reap', str(self.uid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, env=dict(LAUNCHER_ENVIRONMENT),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning('sandbox reap failed for uid %s: %s', self.uid, exc)

    # -- steps --

    @property
    def needs_compile(self):
        return self.language.compile_command(self.binary) is not None

    def compile(self):
        """Compile the program. Languages without a compile step return OK."""
        command = self.language.compile_command(self.binary)
        if command is None:
            self._compiled = True
            return RunResult()
        wall = float(settings.SANDBOX['COMPILE_WALL_SECONDS'])
        result = self._execute(
            command,
            stdin_text='',
            wall_seconds=wall,
            limits={
                'cpu_seconds': int(wall) + 5,
                'address_space_bytes': COMPILE_ADDRESS_SPACE,
                'file_size_bytes': COMPILE_FILE_SIZE_LIMIT,
                'open_files': 256,
            },
            output_bytes=MAX_STDERR_BYTES,
            cpu_limit=wall + 5,
            memory_mb=None,
        )
        if result.status == STATUS_TIME_LIMIT:
            result.status = STATUS_COMPILE_ERROR
            result.message = '编译超时（超过 %d 秒）' % int(wall)
        elif result.status == STATUS_INTERNAL_ERROR:
            pass
        elif result.status != STATUS_OK:
            result.status = STATUS_COMPILE_ERROR
            result.message = '编译失败'
        self._compiled = result.ok
        return result

    def run(self, stdin_text='', limits=None):
        """Run the (compiled) program once."""
        if not self._compiled:
            compiled = self.compile()
            if not compiled.ok:
                return compiled
        limits = limits or Limits()
        command = self.language.run_command(self.binary, limits.memory_mb)
        rlimits = {
            'cpu_seconds': max(1, int(limits.cpu_seconds + 0.999)),
            'file_size_bytes': FILE_SIZE_LIMIT,
            'open_files': 64,
        }
        if self.language.limit_address_space:
            rlimits['address_space_bytes'] = (
                (limits.memory_mb + ADDRESS_SPACE_SLACK_MB) * 1024 * 1024
                + runtime_footprint_kb(self.language, self.binary) * 1024)
        return self._execute(
            command,
            stdin_text=stdin_text,
            wall_seconds=limits.wall_seconds,
            limits=rlimits,
            output_bytes=limits.output_kb * 1024,
            cpu_limit=limits.cpu_seconds,
            memory_mb=limits.memory_mb,
        )

    # -- the actual execution --

    def _execute(self, command, stdin_text, wall_seconds, limits, output_bytes, cpu_limit, memory_mb):
        self._counter += 1
        prefix = os.path.join(self.outdir, 'step%d' % self._counter)
        stdin_path, stdout_path, stderr_path = prefix + '.in', prefix + '.out', prefix + '.err'
        with open(stdin_path, 'w', encoding='utf-8', errors='replace', newline='\n') as handle:
            handle.write(stdin_text or '')

        if self.uid is not None:
            limits = dict(limits, processes=int(settings.SANDBOX['MAX_PROCESSES']))
        report_path = prefix + '.report'
        config = {
            'argv': command,
            'cwd': self.workdir,
            'env': _base_environment(self.workdir, [os.path.dirname(self.binary)]),
            'uid': self.uid,
            'gid': self.uid,
            'isolate_network': not settings.SANDBOX.get('NETWORK'),
            'limits': limits,
            'report': report_path,
        }
        launcher_command = [sys.executable, '-I', '-S', LAUNCHER, 'run', json.dumps(config)]

        started = time.monotonic()
        try:
            with open(stdin_path, 'rb') as stdin_file, \
                    open(stdout_path, 'wb') as stdout_file, \
                    open(stderr_path, 'wb') as stderr_file:
                process = subprocess.Popen(
                    launcher_command,
                    stdin=stdin_file, stdout=stdout_file, stderr=stderr_file,
                    close_fds=True, start_new_session=True,
                    env=dict(LAUNCHER_ENVIRONMENT),
                )
        except OSError as exc:
            raise SandboxError('cannot start the sandbox launcher: %s' % exc)

        timed_out = False
        deadline = started + wall_seconds
        pause = 0.002
        try:
            while True:
                pid, wait_status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._kill_group(process.pid)
                    pid, wait_status, usage = os.wait4(process.pid, 0)
                    break
                time.sleep(pause)
                pause = min(pause * 1.5, 0.02)
        finally:
            # Background children in the same process group die with the run.
            self._kill_group(process.pid)
        wall_ms = int((time.monotonic() - started) * 1000)

        # What happened to the launcher itself.
        launcher_exit = None
        launcher_signal = None
        if os.WIFSIGNALED(wait_status):
            launcher_signal = os.WTERMSIG(wait_status)
            process.returncode = -launcher_signal
        else:
            launcher_exit = os.WEXITSTATUS(wait_status)
            process.returncode = launcher_exit

        # What happened to the program: the launcher's report.
        report = None
        try:
            with open(report_path) as handle:
                report = json.load(handle)
        except (IOError, OSError, ValueError):
            report = None

        stdout, truncated = _read_capped(stdout_path, output_bytes)
        stderr, stderr_truncated = _read_capped(stderr_path, MAX_STDERR_BYTES)
        try:
            output_size = os.path.getsize(stdout_path)
        except OSError:
            output_size = 0

        if report is not None:
            exit_code = report.get('exit_code')
            signal_number = report.get('signal')
            cpu_seconds = float(report.get('cpu_seconds') or 0.0)
            memory_kb = int(report.get('max_rss_kb') or 0)
        else:
            # No report: the launcher was killed (timeout) or failed before forking.
            exit_code = launcher_exit
            signal_number = launcher_signal
            cpu_seconds = usage.ru_utime + usage.ru_stime
            memory_kb = 0

        result = RunResult(
            stdout=stdout, stderr=stderr, exit_code=exit_code, signal_number=signal_number,
            cpu_ms=int(round(cpu_seconds * 1000)), wall_ms=wall_ms, memory_kb=memory_kb,
            truncated=truncated or stderr_truncated,
        )

        if timed_out:
            result.status = STATUS_TIME_LIMIT
            result.message = '运行超时（超过 %g 秒）' % wall_seconds
        elif exit_code == LAUNCHER_SETUP_FAILED and stderr.startswith('sandbox:'):
            result.status = STATUS_INTERNAL_ERROR
            result.message = stderr.strip()
            logger.error('sandbox launcher failed: %s', result.message)
        elif report is None and launcher_signal is None:
            result.status = STATUS_INTERNAL_ERROR
            result.message = 'sandbox: the launcher exited with status %s without a report' % launcher_exit
            logger.error(result.message)
        elif signal_number == signal.SIGXCPU or (
                signal_number == signal.SIGKILL and cpu_seconds >= cpu_limit):
            result.status = STATUS_TIME_LIMIT
            result.message = 'CPU 时间超限（超过 %g 秒）' % cpu_limit
        elif signal_number == signal.SIGXFSZ or output_size >= limits.get('file_size_bytes', FILE_SIZE_LIMIT):
            result.status = STATUS_OUTPUT_LIMIT
            result.message = '输出过多，程序已被终止'
        elif memory_mb is not None and memory_kb > memory_mb * 1024:
            result.status = STATUS_MEMORY_LIMIT
            result.message = '内存超限（超过 %d MB）' % memory_mb
        elif signal_number is not None or exit_code != 0:
            if memory_mb is not None and any(hint in stderr for hint in _OUT_OF_MEMORY_HINTS):
                result.status = STATUS_MEMORY_LIMIT
                result.message = '内存超限（超过 %d MB）' % memory_mb
            else:
                result.status = STATUS_RUNTIME_ERROR
                if signal_number is not None:
                    result.message = '程序被信号终止：%s' % _signal_name(signal_number)
                else:
                    result.message = '程序以非零状态码退出：%d' % exit_code
        return result

    @staticmethod
    def _kill_group(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def _signal_name(number):
    try:
        return signal.Signals(number).name
    except ValueError:
        return 'signal %d' % number


# --------------------------------------------------------------------------
# One-shot helper
# --------------------------------------------------------------------------

def run_code(language, code, stdin_text='', limits=None):
    """Compile and run ``code`` once; always returns a JSON-friendly dict."""
    started = time.time()
    payload = {
        'language': language,
        'status': STATUS_INTERNAL_ERROR,
        'stdout': '',
        'stderr': '',
        'compile_output': '',
        'exit_code': None,
        'signal': None,
        'cpu_ms': 0,
        'wall_ms': 0,
        'memory_kb': 0,
        'truncated': False,
        'message': '',
    }
    try:
        with Session(language, code) as session:
            payload['language'] = session.language.key
            compiled = session.compile()
            if session.needs_compile:
                payload['compile_output'] = (compiled.stderr or compiled.stdout).strip()
            if not compiled.ok:
                payload['status'] = compiled.status
                payload['message'] = compiled.message
                payload['stderr'] = compiled.stderr
            else:
                result = session.run(stdin_text, limits)
                payload.update(result.to_dict())
    except UnsupportedLanguage as exc:
        payload['status'] = STATUS_INTERNAL_ERROR
        payload['message'] = str(exc)
    except SandboxError as exc:
        logger.error('sandbox error: %s', exc)
        payload['status'] = STATUS_INTERNAL_ERROR
        payload['message'] = '运行环境暂时不可用，请稍后再试'
    except Exception:
        logger.exception('unexpected sandbox failure')
        payload['status'] = STATUS_INTERNAL_ERROR
        payload['message'] = '运行环境出错，请稍后再试'
    payload['total_ms'] = int((time.time() - started) * 1000)
    return payload
