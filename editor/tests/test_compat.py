"""The production host runs Python 3.6: keep the code base parseable by it."""
import ast
import os
import sys
import unittest

from django.conf import settings
from django.test import SimpleTestCase


def project_sources():
    base = str(settings.BASE_DIR)
    for folder in ('codetool', 'editor'):
        for directory, _, files in os.walk(os.path.join(base, folder)):
            for name in files:
                if name.endswith('.py'):
                    yield os.path.join(directory, name)
    yield os.path.join(base, 'manage.py')


class Python36SyntaxTests(SimpleTestCase):

    @unittest.skipUnless(sys.version_info >= (3, 8), 'needs ast feature_version (Python 3.8+)')
    def test_sources_parse_with_the_python36_grammar(self):
        checked = 0
        for path in project_sources():
            with open(path, encoding='utf-8') as handle:
                source = handle.read()
            try:
                ast.parse(source, filename=path, feature_version=(3, 6))
            except SyntaxError as exc:
                self.fail('%s is not valid Python 3.6: %s' % (path, exc))
            checked += 1
        self.assertGreater(checked, 20)

    def test_no_features_newer_than_python36(self):
        """A few things the grammar check cannot see."""
        forbidden = (
            'from __future__ import annotations',   # 3.7
            'capture_output=',                      # subprocess, 3.7
            'asyncio.run(',                         # 3.7
            'get_running_loop',                     # 3.7
            'removeprefix(', 'removesuffix(',       # 3.9
            'fromisoformat(',                       # 3.7
            'dataclass',                            # 3.7
            'time_ns(',                             # 3.7
        )
        this_file = os.path.abspath(__file__)
        for path in project_sources():
            if os.path.abspath(path) == this_file:
                continue
            with open(path, encoding='utf-8') as handle:
                source = handle.read()
            for needle in forbidden:
                self.assertNotIn(needle, source, '%s uses %r' % (path, needle))
            for number, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if 'subprocess.' in stripped and 'text=True' in stripped:
                    self.fail('%s:%d uses text=True (Python 3.7+)' % (path, number))
