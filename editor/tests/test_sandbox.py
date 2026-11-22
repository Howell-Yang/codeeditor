import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from editor import sandbox
from editor.sandbox import runner

HAS_GXX = bool(sandbox.find_binary(sandbox.get_language('cpp')))
HAS_GCC = bool(sandbox.find_binary(sandbox.get_language('c')))
HAS_NODE = bool(sandbox.find_binary(sandbox.get_language('javascript')))


def run(language, code, stdin='', **limits):
    return sandbox.run_code(language, code, stdin, sandbox.Limits(**limits) if limits else None)


class LanguageTests(SimpleTestCase):

    def test_aliases(self):
        self.assertEqual(sandbox.normalize_language('Python3'), 'python')
        self.assertEqual(sandbox.normalize_language('c++'), 'cpp')
        self.assertEqual(sandbox.normalize_language('cplusplus'), 'cpp')
        self.assertIsNone(sandbox.get_language('cobol'))
        self.assertEqual(sandbox.get_language('js').key, 'javascript')

    def test_catalog_lists_only_installed_languages(self):
        catalog = sandbox.language_catalog()
        self.assertIn('python', [item['key'] for item in catalog])
        for item in catalog:
            self.assertTrue(item['label'])
            self.assertTrue(item['template'].strip())
            self.assertTrue(sandbox.find_binary(sandbox.get_language(item['key'])))

    def test_templates_run(self):
        for language in sandbox.available_languages():
            result = run(language.key, language.template)
            self.assertEqual(result['status'], 'ok', (language.key, result))
            self.assertIn('Hello, World!', result['stdout'])


class RunTests(SimpleTestCase):

    def test_python_reads_stdin_and_prints_unicode(self):
        result = run('python', 'import sys\nprint("你好", sum(map(int, sys.stdin.read().split())))', '1 2 3\n')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['stdout'], '你好 6\n')
        self.assertEqual(result['exit_code'], 0)
        self.assertGreater(result['memory_kb'], 1000)
        self.assertLess(result['memory_kb'], 60000)   # the program, not the web server
        self.assertLess(result['cpu_ms'], 2000)

    def test_stderr_and_exit_code(self):
        result = run('python', 'import sys\nprint("out")\nsys.stderr.write("oops\\n")\nsys.exit(3)')
        self.assertEqual(result['status'], 'runtime_error')
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['stdout'], 'out\n')
        self.assertIn('oops', result['stderr'])

    def test_exception_is_a_runtime_error(self):
        result = run('python', 'raise ValueError("boom")')
        self.assertEqual(result['status'], 'runtime_error')
        self.assertIn('ValueError: boom', result['stderr'])

    def test_cpu_time_limit(self):
        result = run('python', 'while True:\n    pass', cpu_seconds=1, wall_seconds=5)
        self.assertEqual(result['status'], 'time_limit')
        self.assertGreaterEqual(result['cpu_ms'], 900)

    def test_wall_clock_limit(self):
        result = run('python', 'import time\ntime.sleep(60)', cpu_seconds=1, wall_seconds=1)
        self.assertEqual(result['status'], 'time_limit')
        self.assertLess(result['wall_ms'], 4000)

    def test_memory_limit(self):
        result = run('python', 'x = bytearray(2 * 1024 * 1024 * 1024)\nprint(len(x))', memory_mb=64)
        self.assertEqual(result['status'], 'memory_limit')

    def test_small_memory_limit_still_lets_the_program_start(self):
        # The limit has to bind the program, not the start-up of its runtime:
        # where an idle CPython maps more than 100 MB (CentOS 7), a limit below
        # that used to end in SIGSEGV before the first line ran.
        result = run('python', 'print(6 * 7)', memory_mb=16)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'].strip(), '42')

    def test_memory_within_limit_is_measured(self):
        result = run('python', 'x = bytearray(60 * 1024 * 1024)\nprint(len(x))', memory_mb=128)
        self.assertEqual(result['status'], 'ok')
        self.assertGreater(result['memory_kb'], 60 * 1024)

    def test_output_flood_is_stopped(self):
        result = run('python', 'import sys\nwhile True:\n    sys.stdout.write("x" * 65536)',
                     cpu_seconds=3, wall_seconds=6)
        self.assertEqual(result['status'], 'output_limit')
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['stdout']), settings.SANDBOX['OUTPUT_KB'] * 1024)

    def test_long_output_is_truncated_not_failed(self):
        result = run('python', 'print("y" * (2 * 1024 * 1024))')
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['truncated'])

    def test_background_process_does_not_hold_the_run(self):
        code = (
            'import os, time\n'
            'if os.fork() == 0:\n'
            '    time.sleep(60)\n'
            '    os._exit(0)\n'
            'print("parent done")\n'
        )
        result = run('python', code)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['stdout'], 'parent done\n')
        self.assertLess(result['total_ms'], 5000)

    def test_environment_is_clean(self):
        os.environ['CODETOOL_TEST_SECRET'] = 'do-not-leak'
        try:
            result = run('python', 'import os\nprint(sorted(os.environ))\nprint(os.getcwd() == os.environ["HOME"])')
        finally:
            del os.environ['CODETOOL_TEST_SECRET']
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('CODETOOL_TEST_SECRET', result['stdout'])
        self.assertNotIn('DJANGO', result['stdout'])
        self.assertIn('True', result['stdout'])

    def test_working_directories_are_removed(self):
        before = set(os.listdir(runner.sandbox_root()))
        run('python', 'open("scratch.txt", "w").write("x")\nprint(1)')
        self.assertEqual(set(os.listdir(runner.sandbox_root())), before)

    def test_deep_directory_trees_are_removed_and_do_not_leak_slots(self):
        # 3000 nested directories: too deep for a recursive delete, and the
        # path is longer than the kernel accepts in one piece.
        deep = 'import os\nfor _ in range(3000):\n    os.mkdir("d")\n    os.chdir("d")\nprint("deep")\n'
        before = set(os.listdir(runner.sandbox_root()))
        for _ in range(settings.SANDBOX['MAX_CONCURRENCY'] + 1):
            result = run('python', deep)
            self.assertEqual((result['status'], result['stdout']), ('ok', 'deep\n'))
        self.assertEqual(set(os.listdir(runner.sandbox_root())), before)
        self.assertEqual(run('python', 'print("still running")')['stdout'], 'still running\n')

    def test_directories_without_permissions_are_removed(self):
        code = ('import os\n'
                'os.makedirs("locked/inner")\n'
                'open("locked/inner/file", "w").close()\n'
                'os.symlink("/etc/hostname", "link")\n'
                'os.chmod("locked/inner", 0)\n'
                'os.chmod("locked", 0)\n'
                'print("locked")\n')
        before = set(os.listdir(runner.sandbox_root()))
        mode = os.stat('/etc/hostname').st_mode if os.path.exists('/etc/hostname') else None
        self.assertEqual(run('python', code)['stdout'], 'locked\n')
        self.assertEqual(set(os.listdir(runner.sandbox_root())), before)
        if mode is not None:
            self.assertEqual(os.stat('/etc/hostname').st_mode, mode)     # the symlink target was left alone

    def test_cleanup_failure_still_releases_the_slot(self):
        from unittest import mock
        with mock.patch('editor.sandbox.runner._remove_tree', side_effect=RuntimeError('disk on fire')):
            for _ in range(settings.SANDBOX['MAX_CONCURRENCY'] + 1):
                self.assertEqual(run('python', 'print(1)')['stdout'], '1\n')
        self.assertEqual(run('python', 'print(2)')['stdout'], '2\n')
        # remove what the patched cleanup left behind
        root = runner.sandbox_root()
        for name in os.listdir(root):
            runner._remove_tree(os.path.join(root, name))

    def test_unknown_language(self):
        result = run('cobol', 'DISPLAY "HI".')
        self.assertEqual(result['status'], 'internal_error')
        self.assertIn('cobol', result['message'])

    def test_parallel_runs(self):
        results = []

        def work(number):
            results.append(run('python', 'print(%d * 2)' % number)['stdout'].strip())

        threads = [threading.Thread(target=work, args=(number,)) for number in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(results, key=int), [str(number * 2) for number in range(6)])

    def test_session_compiles_once_and_runs_many(self):
        with sandbox.Session('python', 'print(input()[::-1])') as session:
            self.assertFalse(session.needs_compile)
            self.assertEqual(session.run('abc\n').stdout, 'cba\n')
            self.assertEqual(session.run('xyz\n').stdout, 'zyx\n')


@unittest.skipUnless(HAS_GXX, 'g++ is not installed')
class CppTests(SimpleTestCase):

    def test_standard_headers_are_available(self):
        code = ('using namespace std;\n'
                'int main() {\n'
                '    vector<int> v{3, 1, 2};\n'
                '    sort(v.begin(), v.end());\n'
                '    unordered_map<string, int> m; m["a"] = v[0];\n'
                '    cout << v[0] << v[1] << v[2] << m["a"] << endl;\n'
                '}\n')
        result = run('cpp', code)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'], '1231\n')

    def test_compile_error_points_at_the_users_line(self):
        result = run('cpp', 'int main() {\n  return undefined_name;\n}\n')
        self.assertEqual(result['status'], 'compile_error')
        self.assertIn('main.cpp:2:', result['compile_output'])
        self.assertIn('undefined_name', result['compile_output'])

    def test_segfault(self):
        result = run('cpp', 'int main() { int *p = nullptr; *p = 1; return 0; }')
        self.assertEqual(result['status'], 'runtime_error')
        self.assertEqual(result['signal'], 11)

    def test_memory_bomb(self):
        result = run('cpp', 'int main() { std::vector<long long> v; for (;;) v.resize(v.size() + (1 << 22), 1); }',
                     memory_mb=64)
        self.assertEqual(result['status'], 'memory_limit')

    def test_output_flood(self):
        result = run('cpp', 'int main() { for (;;) puts("yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"); }',
                     cpu_seconds=3, wall_seconds=6)
        self.assertEqual(result['status'], 'output_limit')


@unittest.skipUnless(HAS_GCC, 'gcc is not installed')
class CTests(SimpleTestCase):

    def test_c_program(self):
        code = '#include <stdio.h>\nint main(void) { int a, b; if (scanf("%d %d", &a, &b) == 2) printf("%d\\n", a + b); return 0; }'
        result = run('c', code, '20 22')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'], '42\n')


@unittest.skipUnless(HAS_NODE, 'node is not installed')
class JavaScriptTests(SimpleTestCase):

    def test_javascript_program(self):
        result = run('javascript', "const text = require('fs').readFileSync(0, 'utf8');\nconsole.log(text.trim().toUpperCase());", 'abc')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['stdout'], 'ABC\n')

    def test_javascript_memory_limit(self):
        result = run('javascript', 'const a = []; for (;;) a.push(new Array(1e6).fill(1.5));', memory_mb=64)
        self.assertEqual(result['status'], 'memory_limit')


class LauncherTests(SimpleTestCase):
    """The stand-alone launcher must be careful with its dangerous paths."""

    def call(self, *arguments):
        return subprocess.run(
            [sys.executable, '-I', '-S', runner.LAUNCHER] + list(arguments),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30)

    def test_reap_refuses_system_accounts(self):
        for uid in ('0', '1', '999', '-5'):
            self.assertEqual(self.call('reap', uid).returncode, 2)

    @unittest.skipIf(os.geteuid() == 0, 'meaningless as root')
    def test_reap_is_a_no_op_without_root(self):
        # Must neither fail nor signal anything that belongs to this user.
        self.assertEqual(self.call('reap', '61000').returncode, 0)

    def test_usage(self):
        self.assertEqual(self.call().returncode, 2)

    def test_launcher_is_standalone(self):
        with open(runner.LAUNCHER) as handle:
            source = handle.read()
        self.assertNotIn('import django', source)
        self.assertNotIn('from django', source)
        self.assertNotIn('from .', source)

    def launch(self, argv, limits):
        """Run ``argv`` through the launcher; returns (completed process, report)."""
        directory = tempfile.mkdtemp(prefix='launcher-test-')
        try:
            report = os.path.join(directory, 'report.json')
            config = {
                'argv': argv, 'cwd': directory, 'env': {'PATH': '/usr/bin:/bin'},
                # As root the launcher refuses to keep its uid.
                'uid': settings.SANDBOX['UID_BASE'] if os.geteuid() == 0 else None,
                'gid': settings.SANDBOX['UID_BASE'] if os.geteuid() == 0 else None,
                'limits': limits, 'report': report,
            }
            completed = self.call('run', json.dumps(config))
            try:
                with open(report) as handle:
                    return completed, json.load(handle)
            except (IOError, OSError, ValueError):
                return completed, None
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_address_space_limit_reaches_the_program(self):
        completed, report = self.launch(
            [sys.executable, '-I', '-S', '-c',
             'import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])'],
            {'address_space_bytes': 1024 * 1024 * 1024})
        self.assertEqual((report or {}).get('exit_code'), 0, (report, completed.stderr))
        self.assertEqual(completed.stdout.strip(), str(1024 * 1024 * 1024))

    @unittest.skipUnless(shutil.which('true'), 'no "true" binary')
    def test_address_space_limit_below_the_launcher_itself(self):
        # Less address space than the launcher's own interpreter occupies: the
        # program must still start (the limit is raised to what the launcher
        # needs to reach exec) instead of the launcher dying on the way.
        completed, report = self.launch([shutil.which('true')], {'address_space_bytes': 1024 * 1024})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual((report or {}).get('exit_code'), 0, report)
        self.assertIsNone((report or {}).get('signal'), report)


class FootprintTests(SimpleTestCase):
    """How much address space the bare runtime needs is measured, not assumed."""

    def setUp(self):
        runner.reset_caches()
        self.addCleanup(runner.reset_caches)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'needs /proc')
    def test_python_is_measured_once(self):
        python = sandbox.get_language('python')
        footprint = runner.runtime_footprint_kb(python)
        self.assertGreater(footprint, 1024)
        self.assertLessEqual(footprint, runner.MAX_RUNTIME_FOOTPRINT_KB)
        with mock.patch.object(runner.subprocess, 'run', side_effect=AssertionError('measured twice')):
            self.assertEqual(runner.runtime_footprint_kb(python), footprint)

    def test_compiled_languages_get_no_allowance(self):
        for key in ('c', 'cpp'):
            self.assertEqual(runner.runtime_footprint_kb(sandbox.get_language(key)), 0)

    def test_failed_or_absurd_measurement_means_no_allowance(self):
        python = sandbox.get_language('python')
        with mock.patch.object(runner.subprocess, 'run', side_effect=OSError('no such file')):
            self.assertEqual(runner.runtime_footprint_kb(python), 0)
        for output in ('', 'garbage\n', '-5\n', '%d\n' % (runner.MAX_RUNTIME_FOOTPRINT_KB + 1)):
            runner.reset_caches()
            fake = mock.Mock(returncode=0, stdout=output)
            with mock.patch.object(runner.subprocess, 'run', return_value=fake):
                self.assertEqual(runner.runtime_footprint_kb(python), 0, repr(output))

    def test_the_allowance_is_added_to_the_address_space_limit(self):
        seen = {}

        def fake_execute(self, command, **arguments):
            seen.update(arguments['limits'])
            return runner.RunResult()

        with mock.patch.object(runner, 'runtime_footprint_kb', return_value=100 * 1024), \
                mock.patch.object(runner.Session, '_execute', fake_execute):
            with runner.Session('python', 'pass') as session:
                session.run('', sandbox.Limits(memory_mb=32))
        self.assertEqual(seen['address_space_bytes'],
                         (32 + runner.ADDRESS_SPACE_SLACK_MB + 100) * 1024 * 1024)
