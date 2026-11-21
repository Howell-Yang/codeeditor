"""Languages the sandbox can run.

A language is offered only when its interpreter / compiler is installed on
the host, so the list adapts to the server it is deployed on.
"""
import os
import shutil
import subprocess
import sys
import threading

from django.conf import settings

# Headers every C++ program gets for free (passed to g++ with ``-include``, so
# the line numbers in compiler messages still match what the user typed).
CPP_HEADERS = [
    'algorithm', 'array', 'bitset', 'cctype', 'climits', 'cmath', 'complex',
    'cstdio', 'cstdlib', 'cstring', 'deque', 'functional', 'iomanip', 'iostream',
    'list', 'map', 'numeric', 'queue', 'set', 'sstream', 'stack', 'string',
    'unordered_map', 'unordered_set', 'utility', 'vector',
]

ALIASES = {
    'python3': 'python',
    'py': 'python',
    'py3': 'python',
    'c++': 'cpp',
    'cplusplus': 'cpp',
    'cxx': 'cpp',
    'js': 'javascript',
    'node': 'javascript',
    'nodejs': 'javascript',
}

TEMPLATES = {
    'python': (
        'import sys\n'
        '\n'
        '\n'
        'def main():\n'
        '    data = sys.stdin.read().split()\n'
        '    print("Hello, World!")\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    ),
    'c': (
        '#include <stdio.h>\n'
        '\n'
        'int main(void) {\n'
        '    printf("Hello, World!\\n");\n'
        '    return 0;\n'
        '}\n'
    ),
    'cpp': (
        '#include <iostream>\n'
        'using namespace std;\n'
        '\n'
        'int main() {\n'
        '    cout << "Hello, World!" << endl;\n'
        '    return 0;\n'
        '}\n'
    ),
    'javascript': (
        "const lines = require('fs').readFileSync(0, 'utf8').split('\\n');\n"
        '\n'
        "console.log('Hello, World!');\n"
    ),
}


class Language(object):
    def __init__(self, key, name, source_name, editor_mode, binary_setting, binary_names,
                 version_args, time_factor=1, limit_address_space=True, toolchain=''):
        self.key = key
        self.name = name
        self.toolchain = toolchain              # shown next to the version, e.g. "GCC"
        self.source_name = source_name
        self.editor_mode = editor_mode          # CodeMirror mode
        self.binary_setting = binary_setting    # key in settings.SANDBOX
        self.binary_names = binary_names        # candidates on PATH
        self.version_args = version_args
        self.time_factor = time_factor          # judge: time limit multiplier
        self.limit_address_space = limit_address_space

    # -- overridden per language --
    def prepare_source(self, code):
        return code

    def compile_command(self, binary):
        return None

    def run_command(self, binary, memory_mb):
        raise NotImplementedError

    @property
    def template(self):
        return TEMPLATES.get(self.key, '')


class PythonLanguage(Language):
    def run_command(self, binary, memory_mb):
        # -B: no .pyc files, -s: ignore the user site directory.
        return [binary, '-B', '-s', self.source_name]


class CLanguage(Language):
    def compile_command(self, binary):
        standard = settings.SANDBOX.get('C_STD') or 'gnu99'
        return [binary, '-std=' + standard, '-O2', '-pipe', '-o', 'main', self.source_name, '-lm']

    def run_command(self, binary, memory_mb):
        return ['./main']


class CppLanguage(Language):
    def compile_command(self, binary):
        standard = settings.SANDBOX.get('CXX_STD') or 'c++11'
        command = [binary, '-std=' + standard, '-O2', '-pipe']
        for header in CPP_HEADERS:
            command += ['-include', header]
        return command + ['-o', 'main', self.source_name]

    def run_command(self, binary, memory_mb):
        return ['./main']


class JavaScriptLanguage(Language):
    def run_command(self, binary, memory_mb):
        # V8 reserves far more address space than it uses, so the heap is
        # capped with its own flag instead of RLIMIT_AS.
        return [binary, '--max-old-space-size=%d' % max(16, int(memory_mb)), self.source_name]


LANGUAGES = [
    PythonLanguage('python', 'Python', 'main.py', 'python', 'PYTHON',
                   ['python3'], ['--version'], time_factor=3),
    CppLanguage('cpp', 'C++', 'main.cpp', 'text/x-c++src', 'GXX',
                ['g++'], ['-dumpfullversion', '-dumpversion'], toolchain='GCC'),
    CLanguage('c', 'C', 'main.c', 'text/x-csrc', 'GCC',
              ['gcc'], ['-dumpfullversion', '-dumpversion'], toolchain='GCC'),
    JavaScriptLanguage('javascript', 'JavaScript', 'main.js', 'javascript', 'NODE',
                       ['node', 'nodejs'], ['--version'], time_factor=2,
                       limit_address_space=False, toolchain='Node'),
]
_BY_KEY = dict((language.key, language) for language in LANGUAGES)

_lock = threading.Lock()
_binary_cache = {}
_version_cache = {}


def normalize_language(key):
    key = (key or '').strip().lower()
    return ALIASES.get(key, key)


def get_language(key):
    """The Language for ``key`` (aliases allowed) or None."""
    return _BY_KEY.get(normalize_language(key))


def find_binary(language):
    """Absolute path of the interpreter/compiler, or None when not installed."""
    configured = settings.SANDBOX.get(language.binary_setting) or ''
    cache_key = (language.key, configured)
    with _lock:
        if cache_key in _binary_cache:
            return _binary_cache[cache_key]
    path = None
    if configured:
        path = shutil.which(configured)
    else:
        if language.key == 'python':
            # Default to the interpreter the server itself runs on.
            path = sys.executable or None
        if not path:
            for name in language.binary_names:
                path = shutil.which(name)
                if path:
                    break
    if path:
        path = os.path.abspath(path)
    with _lock:
        _binary_cache[cache_key] = path
    return path


def language_version(language):
    """Short version string such as "3.6.8" ('' when unknown)."""
    binary = find_binary(language)
    if not binary:
        return ''
    with _lock:
        if binary in _version_cache:
            return _version_cache[binary]
    version = ''
    try:
        completed = subprocess.run(
            [binary] + language.version_args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=10,
        )
        text = (completed.stdout or '').strip().splitlines()
        if text:
            version = text[0].strip()
            for prefix in ('Python ', 'v'):
                if version.startswith(prefix):
                    version = version[len(prefix):]
    except (OSError, subprocess.SubprocessError):
        version = ''
    with _lock:
        _version_cache[binary] = version
    return version


def available_languages():
    """Languages whose toolchain exists on this host, in display order."""
    return [language for language in LANGUAGES if find_binary(language)]


def language_catalog():
    """JSON-friendly description of the available languages (for the pages)."""
    catalog = []
    for language in available_languages():
        version = language_version(language)
        label = language.name
        if version and language.toolchain:
            label = '%s (%s %s)' % (language.name, language.toolchain, version)
        elif version:
            label = '%s %s' % (language.name, version)
        catalog.append({
            'key': language.key,
            'name': language.name,
            'label': label,
            'version': version,
            'mode': language.editor_mode,
            'template': language.template,
            'time_factor': language.time_factor,
        })
    return catalog


def reset_caches():
    with _lock:
        _binary_cache.clear()
        _version_cache.clear()
