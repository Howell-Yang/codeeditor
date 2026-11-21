import os

from django.conf import settings
from django.core.management.base import BaseCommand

from editor import sandbox
from editor.sandbox import runner

PROBES = [
    ('who am I', 'python',
     'import os\nprint("uid=%d gid=%d" % (os.getuid(), os.getgid()))'),
    ('network interfaces', 'python',
     'import socket\nprint(" ".join(sorted(name for _, name in socket.if_nameindex())) or "(none)")'),
    ('outbound connection', 'python',
     'import socket\n'
     'try:\n'
     '    socket.create_connection(("223.5.5.5", 53), timeout=3).close()\n'
     '    print("REACHABLE")\n'
     'except Exception as exc:\n'
     '    print("blocked:", exc)'),
    ('read the project database', 'python',
     'try:\n'
     '    open(%r, "rb").read(16)\n'
     '    print("READABLE")\n'
     'except Exception as exc:\n'
     '    print("denied:", exc)' % str(settings.DATABASES['default']['NAME'])),
    ('endless loop', 'python', 'while True:\n    pass'),
    ('memory bomb', 'python', 'x = bytearray(4 * 1024 * 1024 * 1024)\nprint(len(x))'),
    ('fork bomb', 'python',
     'import os\n'
     'count = 0\n'
     'try:\n'
     '    for _ in range(2000):\n'
     '        if os.fork() == 0:\n'
     '            import time\n'
     '            time.sleep(30)\n'
     '            os._exit(0)\n'
     '        count += 1\n'
     'except OSError as exc:\n'
     '    print("stopped after", count, "processes:", exc)\n'
     'else:\n'
     '    print("NOT LIMITED: started", count, "processes")'),
    ('output flood', 'python', 'import sys\nwhile True:\n    sys.stdout.write("x" * 65536)'),
]


class Command(BaseCommand):
    help = 'Show which protections the code sandbox has on this host and try them out.'

    def handle(self, *args, **options):
        write = self.stdout.write
        root = runner.running_as_root()
        write('Server user: uid %d%s' % (os.getuid(), ' (root)' if root else ''))
        if root:
            base = settings.SANDBOX['UID_BASE']
            write('  programs run as uids %d-%d, without network unless SANDBOX_NETWORK=1'
                  % (base, base + settings.SANDBOX['MAX_CONCURRENCY'] - 1))
        else:
            write(self.style.WARNING(
                '  not root: programs run as the server user, keep their network access and can\n'
                '  read whatever this user can read. Fine for development, not for a public site.'))
        write('Working directories: %s' % runner.sandbox_root())
        write('Languages:')
        for language in sandbox.LANGUAGES:
            binary = sandbox.find_binary(language)
            if binary:
                write('  %-11s %s (%s)' % (language.name, binary, sandbox.language_version(language) or '?'))
            else:
                write(self.style.WARNING('  %-11s not installed' % language.name))
        write('')
        write('Probes (limits: %ds CPU, %d MB):' % (
            settings.SANDBOX['RUN_CPU_SECONDS'], settings.SANDBOX['RUN_MEMORY_MB']))
        for title, language, code in PROBES:
            result = sandbox.run_code(language, code, '', sandbox.Limits(wall_seconds=8))
            detail = (result['stdout'] or result['stderr'] or result['message']).strip().splitlines()
            detail = detail[-1][:90] if detail else ''
            line = '  %-26s %-14s %s' % (title, result['status'], detail)
            alarming = any(word in line for word in ('REACHABLE', 'READABLE', 'NOT LIMITED', 'uid=0 '))
            write(self.style.ERROR(line) if alarming else line)
        for language in sandbox.available_languages():
            result = sandbox.run_code(language.key, language.template, '')
            write('  hello world in %-11s %-14s %s' % (
                language.name, result['status'], (result['stdout'] or result['message']).strip()[:60]))
