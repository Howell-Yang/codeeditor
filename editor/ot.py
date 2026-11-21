"""Operational transformation for plain text (server side).

Two people typing in the same room send their edits as *operations* against
the revision they last saw. The server rewrites ("transforms") every incoming
operation over the operations it has accepted in the meantime, so all clients
converge on the same text without anybody's cursor being thrown around.

The algorithm and the wire format are the classic ones used by ot.js
(https://github.com/Operational-Transformation/ot.js, MIT): an operation is a
list that walks over the whole document, where

* a positive integer ``n`` keeps the next ``n`` characters,
* a negative integer ``-n`` deletes the next ``n`` characters,
* a string is inserted at the current position.

``static/js/ot.js`` is the browser counterpart; ``tests/test_ot.py`` checks
that both implementations agree.

Lengths are counted in UTF-16 code units because that is what JavaScript
strings (and therefore the browsers' indexes) use. ``to_units`` /
``from_units`` convert between ordinary Python strings and strings whose
"characters" are UTF-16 units.
"""
import struct

__all__ = ['TextOperation', 'OTError', 'to_units', 'from_units', 'text_hash']


class OTError(ValueError):
    """An operation is malformed or does not fit the document."""


# --------------------------------------------------------------------------
# UTF-16 helpers
# --------------------------------------------------------------------------

def to_units(text):
    """Return ``text`` as a string of UTF-16 code units.

    Characters outside the Basic Multilingual Plane (emoji, rare CJK) become
    two surrogate characters, exactly like in a JavaScript string. Text without
    such characters is returned unchanged.
    """
    if not text or max(text) < '\U00010000':
        return text
    data = text.encode('utf-16-le', 'surrogatepass')
    return ''.join(map(chr, struct.unpack('<%dH' % (len(data) // 2), data)))


def from_units(units):
    """Inverse of :func:`to_units`. Unpaired surrogates become U+FFFD."""
    if not units or max(units) < '\ud800':
        return units
    return units.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')


def text_hash(units):
    """32-bit FNV-1a over UTF-16 code units; the browser computes the same value."""
    value = 0x811c9dc5
    for char in units:
        value ^= ord(char)
        value = (value * 0x01000193) & 0xffffffff
    return value


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

def _is_retain(component):
    return isinstance(component, int) and component > 0


def _is_delete(component):
    return isinstance(component, int) and component < 0


def _is_insert(component):
    return isinstance(component, str)


class TextOperation(object):
    """A change that turns a document of ``base_length`` into one of ``target_length``."""

    __slots__ = ('ops', 'base_length', 'target_length')

    def __init__(self):
        self.ops = []
        self.base_length = 0
        self.target_length = 0

    def __eq__(self, other):
        return isinstance(other, TextOperation) and self.ops == other.ops

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return 'TextOperation(%r)' % (self.ops,)

    # ---- building ----

    def retain(self, count):
        if count == 0:
            return self
        if count < 0:
            raise OTError('retain expects a positive integer')
        self.base_length += count
        self.target_length += count
        if self.ops and _is_retain(self.ops[-1]):
            self.ops[-1] += count
        else:
            self.ops.append(count)
        return self

    def insert(self, text):
        if text == '':
            return self
        self.target_length += len(text)
        ops = self.ops
        if ops and _is_insert(ops[-1]):
            ops[-1] += text
        elif ops and _is_delete(ops[-1]):
            # "delete then insert" and "insert then delete" have the same
            # effect; always store the insert first so that equal operations
            # have equal representations.
            if len(ops) >= 2 and _is_insert(ops[-2]):
                ops[-2] += text
            else:
                ops.append(ops[-1])
                ops[-2] = text
        else:
            ops.append(text)
        return self

    def delete(self, count):
        if count == 0:
            return self
        if count < 0:
            count = -count
        self.base_length += count
        if self.ops and _is_delete(self.ops[-1]):
            self.ops[-1] -= count
        else:
            self.ops.append(-count)
        return self

    def is_noop(self):
        return not self.ops or (len(self.ops) == 1 and _is_retain(self.ops[0]))

    # ---- (de)serialisation ----

    def to_json(self):
        return list(self.ops)

    @classmethod
    def from_json(cls, components, max_components=20000):
        """Build an operation from its wire form, validating every component.

        Inserted strings are converted with :func:`to_units`.
        """
        if not isinstance(components, list):
            raise OTError('operation must be a list')
        if len(components) > max_components:
            raise OTError('operation has too many components')
        operation = cls()
        for component in components:
            if isinstance(component, bool):
                raise OTError('invalid operation component')
            if isinstance(component, int):
                if component > 0:
                    operation.retain(component)
                elif component < 0:
                    operation.delete(-component)
                # a zero is harmless noise
            elif isinstance(component, str):
                operation.insert(to_units(component))
            else:
                raise OTError('invalid operation component')
        return operation

    # ---- applying ----

    def apply(self, document):
        """Return the document after this operation. ``document`` is in UTF-16 units."""
        if len(document) != self.base_length:
            raise OTError(
                "operation expects a document of length %d, got %d"
                % (self.base_length, len(document))
            )
        parts = []
        index = 0
        for component in self.ops:
            if _is_retain(component):
                parts.append(document[index:index + component])
                index += component
            elif _is_insert(component):
                parts.append(component)
            else:
                index -= component
        return ''.join(parts)

    def transform_index(self, index, insert_before=False):
        """Where a caret at ``index`` ends up after this operation.

        ``insert_before``: an insertion exactly at the caret pushes the caret
        to the right (False, the default) or leaves it in front of the new
        text (True).
        """
        new_index = index
        position = 0  # how far we have walked through the base document
        for component in self.ops:
            if _is_retain(component):
                position += component
            elif _is_insert(component):
                if position < index or (position == index and not insert_before):
                    new_index += len(component)
            else:
                length = -component
                if position < index:
                    new_index -= min(length, index - position)
                position += length
            if position > index:
                break  # everything else happens to the right of the caret
        return new_index

    # ---- combining ----

    def compose(self, other):
        """One operation with the effect of ``self`` followed by ``other``."""
        if self.target_length != other.base_length:
            raise OTError('compose: the second operation does not start where the first ends')
        result = TextOperation()
        ops1, ops2 = self.ops, other.ops
        i1 = i2 = 0
        op1 = ops1[i1] if i1 < len(ops1) else None
        i1 += 1
        op2 = ops2[i2] if i2 < len(ops2) else None
        i2 += 1
        while True:
            if op1 is None and op2 is None:
                break
            if _is_delete(op1):
                result.delete(-op1)
                op1 = ops1[i1] if i1 < len(ops1) else None
                i1 += 1
                continue
            if _is_insert(op2):
                result.insert(op2)
                op2 = ops2[i2] if i2 < len(ops2) else None
                i2 += 1
                continue
            if op1 is None:
                raise OTError('compose: first operation is too short')
            if op2 is None:
                raise OTError('compose: first operation is too long')

            if _is_retain(op1) and _is_retain(op2):
                if op1 > op2:
                    result.retain(op2)
                    op1 -= op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif op1 == op2:
                    result.retain(op1)
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    result.retain(op1)
                    op2 -= op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
            elif _is_insert(op1) and _is_delete(op2):
                if len(op1) > -op2:
                    op1 = op1[-op2:]
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif len(op1) == -op2:
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    op2 += len(op1)
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
            elif _is_insert(op1) and _is_retain(op2):
                if len(op1) > op2:
                    result.insert(op1[:op2])
                    op1 = op1[op2:]
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif len(op1) == op2:
                    result.insert(op1)
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    result.insert(op1)
                    op2 -= len(op1)
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
            elif _is_retain(op1) and _is_delete(op2):
                if op1 > -op2:
                    result.delete(-op2)
                    op1 += op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif op1 == -op2:
                    result.delete(-op2)
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    result.delete(op1)
                    op2 += op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
            else:
                raise OTError('compose: incompatible operations')
        return result

    @staticmethod
    def transform(operation1, operation2):
        """Rewrite two concurrent operations so that they can follow each other.

        Returns ``(operation1', operation2')`` with
        ``apply(apply(S, operation1), operation2') == apply(apply(S, operation2), operation1')``.
        When both insert at the same place, ``operation1``'s text goes first.
        """
        if operation1.base_length != operation2.base_length:
            raise OTError('transform: operations start from documents of different length')
        prime1 = TextOperation()
        prime2 = TextOperation()
        ops1, ops2 = operation1.ops, operation2.ops
        i1 = i2 = 0
        op1 = ops1[i1] if i1 < len(ops1) else None
        i1 += 1
        op2 = ops2[i2] if i2 < len(ops2) else None
        i2 += 1
        while True:
            if op1 is None and op2 is None:
                break
            if _is_insert(op1):
                prime1.insert(op1)
                prime2.retain(len(op1))
                op1 = ops1[i1] if i1 < len(ops1) else None
                i1 += 1
                continue
            if _is_insert(op2):
                prime1.retain(len(op2))
                prime2.insert(op2)
                op2 = ops2[i2] if i2 < len(ops2) else None
                i2 += 1
                continue
            if op1 is None:
                raise OTError('transform: first operation is too short')
            if op2 is None:
                raise OTError('transform: first operation is too long')

            if _is_retain(op1) and _is_retain(op2):
                if op1 > op2:
                    length = op2
                    op1 -= op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif op1 == op2:
                    length = op2
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    length = op1
                    op2 -= op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                prime1.retain(length)
                prime2.retain(length)
            elif _is_delete(op1) and _is_delete(op2):
                # Both delete the same text: nothing left to do for either.
                if -op1 > -op2:
                    op1 -= op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif op1 == op2:
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    op2 -= op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
            elif _is_delete(op1) and _is_retain(op2):
                if -op1 > op2:
                    length = op2
                    op1 += op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif -op1 == op2:
                    length = op2
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    length = -op1
                    op2 += op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                prime1.delete(length)
            elif _is_retain(op1) and _is_delete(op2):
                if op1 > -op2:
                    length = -op2
                    op1 += op2
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                elif op1 == -op2:
                    length = op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                    op2 = ops2[i2] if i2 < len(ops2) else None
                    i2 += 1
                else:
                    length = op1
                    op2 += op1
                    op1 = ops1[i1] if i1 < len(ops1) else None
                    i1 += 1
                prime2.delete(length)
            else:
                raise OTError('transform: incompatible operations')
        return prime1, prime2


def replace_all(old_units, new_units):
    """Operation that turns ``old_units`` into ``new_units`` touching only the differing middle."""
    limit = min(len(old_units), len(new_units))
    start = 0
    while start < limit and old_units[start] == new_units[start]:
        start += 1
    end = 0
    while end < limit - start and old_units[len(old_units) - 1 - end] == new_units[len(new_units) - 1 - end]:
        end += 1
    operation = TextOperation()
    operation.retain(start)
    operation.delete(len(old_units) - start - end)
    operation.insert(new_units[start:len(new_units) - end])
    operation.retain(end)
    return operation
