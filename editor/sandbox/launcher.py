"""Stand-alone launcher executed in a fresh interpreter for every sandboxed run.

    python -I -S launcher.py run  <json-config>
    python -I -S launcher.py reap <uid>

``run`` forks once. The child applies the restrictions described by the
config (resource limits, uid switch, no-new-privileges) and replaces itself
with the target program. The parent only waits for it and writes a small JSON
report with the exit status, the CPU time and the peak memory of exactly that
child. (Measuring from the web server instead would report the server's own
memory, because a forked child starts with a copy of its parent's pages.)

``reap`` kills every process that still belongs to a sandbox uid after a run
(children that escaped the process group with ``setsid`` for example).

The file is deliberately self-contained and standard-library only: it must
not import Django or anything from this project, and it has to keep working
on Python 3.6. Doing the setup here instead of in a ``preexec_fn`` keeps the
multi-threaded web process from forking arbitrary Python code.
"""
import json
import os
import signal
import sys

EXIT_SETUP_FAILED = 120
EXIT_REPORT_FAILED = 121

CLONE_NEWIPC = 0x08000000
CLONE_NEWNET = 0x40000000
PR_SET_NO_NEW_PRIVS = 38

MIN_SANDBOX_UID = 1000  # never switch to (or reap) a system account

# Room this interpreter keeps for itself between applying the address space
# limit and exec (see _limit_address_space).
SETUP_HEADROOM = 16 * 1024 * 1024


def _fail(message):
    try:
        os.write(2, ('sandbox: ' + message + '\n').encode('utf-8', 'replace'))
    finally:
        os._exit(EXIT_SETUP_FAILED)


def _libc():
    import ctypes
    return ctypes.CDLL('libc.so.6', use_errno=True)


def _unshare(flags):
    """unshare(2) through libc. Returns True on success."""
    try:
        return _libc().unshare(flags) == 0
    except Exception:
        return False


def _no_new_privileges():
    """After this, set-uid binaries (su, sudo, ping, ...) no longer elevate."""
    try:
        return _libc().prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0
    except Exception:
        return False


def _set_limits(limits):
    import resource

    def apply(name, soft, hard=None):
        number = getattr(resource, name, None)
        if number is None:
            return
        try:
            resource.setrlimit(number, (soft, soft if hard is None else hard))
        except (ValueError, OSError):
            # Asking for more than the hard limit we inherited: keep what we have.
            pass

    apply('RLIMIT_CORE', 0)
    cpu = limits.get('cpu_seconds')
    if cpu:
        # Soft limit -> SIGXCPU, hard limit one second later -> SIGKILL.
        apply('RLIMIT_CPU', int(cpu), int(cpu) + 1)
    # The address space limit is not in this list: see _limit_address_space.
    for key, name in (
            ('stack_bytes', 'RLIMIT_STACK'),
            ('file_size_bytes', 'RLIMIT_FSIZE'),
            ('open_files', 'RLIMIT_NOFILE'),
            ('processes', 'RLIMIT_NPROC')):
        value = limits.get(key)
        if value:
            apply(name, int(value))


def _virtual_size():
    """Bytes of address space this process occupies right now (0 if unknown)."""
    try:
        with open('/proc/self/statm') as handle:
            pages = int(handle.read().split()[0])
        return pages * os.sysconf('SC_PAGE_SIZE')
    except (IOError, OSError, ValueError, IndexError):
        return 0


def _limit_address_space(requested):
    """Apply RLIMIT_AS for the program that is about to be started.

    The limit is in force the moment it is set, so it also binds this
    interpreter for the few steps that are left before exec. An interpreter
    that may not grow any more cannot even extend its stack and dies with
    SIGSEGV before the program starts, and how much address space an idle
    interpreter occupies depends on the host (CPython on CentOS 7 maps the
    100 MB glibc locale archive). Therefore this is the very last step, and
    the limit is never set below what this process already uses plus a little
    room to finish. exec gives the program a fresh address space under the
    same limit.
    """
    if not requested:
        return
    import resource
    limit = int(requested)
    used = _virtual_size()
    if used:
        limit = max(limit, used + SETUP_HEADROOM)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        # Above the hard limit we inherited: the stricter one stays.
        pass


def _become_target(config, is_root):
    """Runs in the forked child; never returns."""
    argv = config['argv']
    uid = config.get('uid')
    gid = config.get('gid')
    limits = config.get('limits') or {}
    environment = config.get('env') or {}
    try:
        os.umask(0o077)
        os.chdir(config['cwd'])
    except OSError as exc:
        _fail('cannot enter the working directory: %s' % exc)
    try:
        _set_limits(limits)
    except Exception as exc:
        _fail('cannot apply resource limits: %s' % exc)
    if is_root:
        try:
            os.setgroups([])
            os.setgid(int(gid if gid is not None else uid))
            os.setuid(int(uid))
        except OSError as exc:
            _fail('cannot drop privileges: %s' % exc)
        if os.getuid() == 0 or os.geteuid() == 0:
            _fail('still root after dropping privileges')
    _no_new_privileges()
    # Python ignores these two at start-up and ignored signals survive exec;
    # the program must get the normal behaviour (e.g. die on SIGXFSZ when it
    # writes more than the file size limit).
    for name in ('SIGPIPE', 'SIGXFSZ'):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, signal.SIG_DFL)
    try:
        _limit_address_space(limits.get('address_space_bytes'))
    except Exception as exc:
        _fail('cannot apply the address space limit: %s' % exc)
    try:
        os.execvpe(argv[0], argv, environment)
    except OSError as exc:
        _fail('cannot start %s: %s' % (argv[0], exc))


def run(config):
    uid = config.get('uid')
    is_root = os.geteuid() == 0

    if is_root and uid is None:
        _fail('refusing to run a program as root')
    if uid is not None and int(uid) < MIN_SANDBOX_UID:
        _fail('refusing to run a program as a system account')

    if config.get('isolate_network') and is_root:
        # A new network namespace has no interfaces except an unconfigured
        # loopback: the program cannot reach anything.
        if not _unshare(CLONE_NEWNET | CLONE_NEWIPC):
            if config.get('require_network_isolation'):
                _fail('cannot create a network namespace')

    pid = os.fork()
    if pid == 0:
        try:
            _become_target(config, is_root)
        finally:
            os._exit(EXIT_SETUP_FAILED)

    _, status, usage = os.wait4(pid, 0)
    report = {
        'exit_code': os.WEXITSTATUS(status) if os.WIFEXITED(status) else None,
        'signal': os.WTERMSIG(status) if os.WIFSIGNALED(status) else None,
        'cpu_seconds': usage.ru_utime + usage.ru_stime,
        'max_rss_kb': int(usage.ru_maxrss),
    }
    report_path = config.get('report')
    if report_path:
        try:
            temporary = report_path + '.tmp'
            with open(temporary, 'w') as handle:
                json.dump(report, handle)
            os.rename(temporary, report_path)
        except (IOError, OSError):
            os._exit(EXIT_REPORT_FAILED)
    os._exit(0)


def reap(uid):
    """Kill every process owned by ``uid`` (called as root after a run)."""
    uid = int(uid)
    if uid < MIN_SANDBOX_UID:
        return 2
    if os.geteuid() != 0:
        # Without root nothing ran under another uid, and "kill everything I
        # may signal" would hit the developer's own session. Do nothing.
        return 0
    try:
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    except OSError:
        return 1
    if os.getuid() != uid or os.geteuid() != uid:
        return 1
    try:
        # As a non-root uid, -1 means "everything I am allowed to signal",
        # i.e. all processes of this uid (Linux leaves the caller alone).
        os.kill(-1, signal.SIGKILL)
    except OSError:
        pass
    return 0


def main(argv):
    if len(argv) == 3 and argv[1] == 'run':
        try:
            config = json.loads(argv[2])
        except ValueError as exc:
            _fail('bad configuration: %s' % exc)
        run(config)
        return EXIT_SETUP_FAILED
    if len(argv) == 3 and argv[1] == 'reap':
        return reap(argv[2])
    os.write(2, b'usage: launcher.py run <json> | reap <uid>\n')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
