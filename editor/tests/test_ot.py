import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest

from django.test import SimpleTestCase

from editor.ot import OTError, TextOperation, from_units, replace_all, text_hash, to_units

JS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js')
ALPHABET = ['a', 'b', 'c', ' ', '\n', '中', '文', '😀', 'x', '(', ')']


def random_text(rng, maximum):
    return to_units(''.join(rng.choice(ALPHABET) for _ in range(rng.randint(1, maximum))))


def random_operation(rng, document):
    """A random operation applicable to ``document`` (a string of UTF-16 units)."""
    operation = TextOperation()
    position = 0
    while position < len(document):
        length = rng.randint(1, min(8, len(document) - position))
        choice = rng.random()
        if choice < 0.2:
            operation.insert(random_text(rng, 5))
        elif choice < 0.45:
            operation.delete(length)
            position += length
        else:
            operation.retain(length)
            position += length
    if rng.random() < 0.3:
        operation.insert(random_text(rng, 5))
    return operation


class TextOperationTests(SimpleTestCase):

    def test_apply(self):
        operation = TextOperation().retain(2).insert('XY').delete(1).retain(2)
        self.assertEqual(operation.apply('abcde'), 'abXYde')
        self.assertEqual(operation.base_length, 5)
        self.assertEqual(operation.target_length, 6)

    def test_apply_rejects_wrong_length(self):
        with self.assertRaises(OTError):
            TextOperation().retain(3).apply('ab')

    def test_canonical_form(self):
        # delete+insert and insert+delete describe the same change
        first = TextOperation().retain(1).delete(2).insert('x')
        second = TextOperation().retain(1).insert('x').delete(2)
        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), [1, 'x', -2])
        merged = TextOperation().retain(1).retain(2).insert('a').insert('b').delete(1).delete(1)
        self.assertEqual(merged.to_json(), [3, 'ab', -2])

    def test_from_json_validation(self):
        self.assertEqual(TextOperation.from_json([2, 'ab', -1, 0]).to_json(), [2, 'ab', -1])
        for bad in ('x', [1.5], [None], [True], [['a']], {'a': 1}):
            with self.assertRaises(OTError):
                TextOperation.from_json(bad)
        with self.assertRaises(OTError):
            TextOperation.from_json([1] * 10, max_components=5)

    def test_transform_keeps_first_insert_first(self):
        first = TextOperation().retain(1).insert('A').retain(1)
        second = TextOperation().retain(1).insert('B').retain(1)
        first_prime, second_prime = TextOperation.transform(first, second)
        self.assertEqual(second_prime.apply(first.apply('xy')), 'xABy')
        self.assertEqual(first_prime.apply(second.apply('xy')), 'xABy')

    def test_random_transform_and_compose(self):
        rng = random.Random(20261121)
        for _ in range(1500):
            document = random_text(rng, 40)
            first = random_operation(rng, document)
            second = random_operation(rng, document)
            first_prime, second_prime = TextOperation.transform(first, second)
            left = second_prime.apply(first.apply(document))
            right = first_prime.apply(second.apply(document))
            self.assertEqual(left, right)
            # compose == applying one after the other
            follow_up = random_operation(rng, first.apply(document))
            self.assertEqual(first.compose(follow_up).apply(document), follow_up.apply(first.apply(document)))
            self.assertEqual(first.compose(second_prime).apply(document), left)

    def test_transform_index(self):
        operation = TextOperation().retain(2).insert('XYZ').retain(1).delete(2).retain(1)
        # base: "ab|c|de|f"
        self.assertEqual(operation.transform_index(0), 0)
        self.assertEqual(operation.transform_index(2), 5)
        self.assertEqual(operation.transform_index(2, insert_before=True), 2)
        self.assertEqual(operation.transform_index(3), 6)
        self.assertEqual(operation.transform_index(4), 6)   # inside the deleted part
        self.assertEqual(operation.transform_index(5), 6)
        self.assertEqual(operation.transform_index(6), 7)

    def test_random_transform_index_matches_marker(self):
        rng = random.Random(7)
        for _ in range(500):
            document = random_text(rng, 30)
            operation = random_operation(rng, document)
            index = rng.randint(0, len(document))
            # Expected position: characters that survive in front of the caret
            # plus everything inserted at or before it.
            expected = 0
            position = 0
            for component in operation.ops:
                if isinstance(component, str):
                    if position <= index:
                        expected += len(component)
                elif component > 0:
                    expected += max(0, min(component, index - position))
                    position += component
                else:
                    position -= component
            self.assertEqual(operation.transform_index(index), expected)

    def test_replace_all(self):
        rng = random.Random(3)
        for _ in range(300):
            old, new = random_text(rng, 20), random_text(rng, 20)
            self.assertEqual(replace_all(old, new).apply(old), new)
        self.assertTrue(replace_all('same', 'same').is_noop())

    def test_utf16_units(self):
        text = 'a😀中b'
        units = to_units(text)
        self.assertEqual(len(units), 5)          # the emoji is two units, as in JavaScript
        self.assertEqual(from_units(units), text)
        plain = 'plain 中文'
        self.assertIs(to_units(plain), plain)     # nothing to convert: same object back
        # half a surrogate pair must not break saving
        self.assertEqual(from_units(units[:2]), 'a�')
        # JSON round trip the way the consumer does it
        self.assertEqual(to_units(json.loads(json.dumps(units))), units)

    def test_hash(self):
        self.assertEqual(text_hash(''), 0x811c9dc5)
        self.assertEqual(text_hash('a'), 0xe40c292c)
        self.assertNotEqual(text_hash('ab'), text_hash('ba'))


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class JavaScriptParityTests(SimpleTestCase):
    """static/js/ot.js must behave exactly like editor/ot.py."""

    def run_node(self, script, *arguments):
        completed = subprocess.run(
            ['node', os.path.join(JS_DIR, script)] + list(arguments),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout

    def test_vectors(self):
        rng = random.Random(42)
        vectors = []
        for _ in range(600):
            document = random_text(rng, 40)
            first = random_operation(rng, document)
            second = random_operation(rng, document)
            first_prime, second_prime = TextOperation.transform(first, second)
            indexes = []
            for index in sorted(set(rng.randint(0, len(document)) for _ in range(4))):
                indexes.append([index, first.transform_index(index), first.transform_index(index, True)])
            vectors.append({
                'doc': document,
                'a': first.to_json(),
                'b': second.to_json(),
                'after_a': first.apply(document),
                'after_b': second.apply(document),
                'a_prime': first_prime.to_json(),
                'b_prime': second_prime.to_json(),
                'merged': second_prime.apply(first.apply(document)),
                'composed': first.compose(second_prime).to_json(),
                'hash': text_hash(document),
                'indexes': indexes,
            })
        handle, path = tempfile.mkstemp(suffix='.json')
        try:
            with os.fdopen(handle, 'w') as stream:
                json.dump(vectors, stream)
            output = self.run_node('ot_vectors.js', path)
        finally:
            os.unlink(path)
        self.assertIn('OK 600', output)

    def test_client_simulation_converges(self):
        output = self.run_node('sync_simulation.js', '400', '20261121')
        self.assertIn('OK 400 rounds', output)
