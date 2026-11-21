/*
 * Operational transformation for plain text - browser side.
 *
 * Mirrors editor/ot.py (same algorithm, same wire format); the test-suite
 * checks that both give identical results. The algorithm and the client
 * state machine follow ot.js by Tim Baumann (MIT).
 *
 * An operation is an array walking over the whole document:
 *   n > 0   keep n characters
 *   n < 0   delete -n characters
 *   "text"  insert text
 * Lengths are JavaScript string lengths (UTF-16 code units).
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.CodeTool = root.CodeTool || {};
    root.CodeTool.ot = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function isRetain(op) { return typeof op === 'number' && op > 0; }
  function isInsert(op) { return typeof op === 'string'; }
  function isDelete(op) { return typeof op === 'number' && op < 0; }

  function TextOperation() {
    this.ops = [];
    this.baseLength = 0;
    this.targetLength = 0;
  }

  TextOperation.prototype.retain = function (n) {
    if (n === 0) { return this; }
    if (typeof n !== 'number' || n < 0) { throw new Error('retain expects a positive integer'); }
    this.baseLength += n;
    this.targetLength += n;
    var ops = this.ops;
    if (isRetain(ops[ops.length - 1])) {
      ops[ops.length - 1] += n;
    } else {
      ops.push(n);
    }
    return this;
  };

  TextOperation.prototype.insert = function (str) {
    if (typeof str !== 'string') { throw new Error('insert expects a string'); }
    if (str === '') { return this; }
    this.targetLength += str.length;
    var ops = this.ops;
    if (isInsert(ops[ops.length - 1])) {
      ops[ops.length - 1] += str;
    } else if (isDelete(ops[ops.length - 1])) {
      // Same effect either way round; always keep the insert first so that
      // equal operations look equal.
      if (isInsert(ops[ops.length - 2])) {
        ops[ops.length - 2] += str;
      } else {
        ops[ops.length] = ops[ops.length - 1];
        ops[ops.length - 2] = str;
      }
    } else {
      ops.push(str);
    }
    return this;
  };

  TextOperation.prototype['delete'] = function (n) {
    if (typeof n === 'string') { n = n.length; }
    if (typeof n !== 'number') { throw new Error('delete expects an integer or a string'); }
    if (n === 0) { return this; }
    if (n < 0) { n = -n; }
    this.baseLength += n;
    var ops = this.ops;
    if (isDelete(ops[ops.length - 1])) {
      ops[ops.length - 1] -= n;
    } else {
      ops.push(-n);
    }
    return this;
  };

  TextOperation.prototype.isNoop = function () {
    return this.ops.length === 0 || (this.ops.length === 1 && isRetain(this.ops[0]));
  };

  TextOperation.prototype.toJSON = function () { return this.ops.slice(); };

  TextOperation.fromJSON = function (ops) {
    var operation = new TextOperation();
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (isRetain(op)) {
        operation.retain(op);
      } else if (isInsert(op)) {
        operation.insert(op);
      } else if (isDelete(op)) {
        operation['delete'](op);
      } else if (op !== 0) {
        throw new Error('unknown operation component: ' + JSON.stringify(op));
      }
    }
    return operation;
  };

  TextOperation.prototype.apply = function (str) {
    if (str.length !== this.baseLength) {
      throw new Error('operation expects a document of length ' + this.baseLength + ', got ' + str.length);
    }
    var parts = [];
    var index = 0;
    for (var i = 0; i < this.ops.length; i++) {
      var op = this.ops[i];
      if (isRetain(op)) {
        parts.push(str.slice(index, index + op));
        index += op;
      } else if (isInsert(op)) {
        parts.push(op);
      } else {
        index -= op;
      }
    }
    return parts.join('');
  };

  // The operation that undoes this one. `str` is the text this operation
  // applies to.
  TextOperation.prototype.invert = function (str) {
    var index = 0;
    var inverse = new TextOperation();
    for (var i = 0; i < this.ops.length; i++) {
      var op = this.ops[i];
      if (isRetain(op)) {
        inverse.retain(op);
        index += op;
      } else if (isInsert(op)) {
        inverse['delete'](op.length);
      } else {
        inverse.insert(str.slice(index, index - op));
        index -= op;
      }
    }
    return inverse;
  };

  // Where a caret at `index` ends up after this operation. `insertBefore`:
  // an insertion exactly at the caret leaves the caret in front of it.
  TextOperation.prototype.transformIndex = function (index, insertBefore) {
    var newIndex = index;
    var position = 0;
    for (var i = 0; i < this.ops.length; i++) {
      var op = this.ops[i];
      if (isRetain(op)) {
        position += op;
      } else if (isInsert(op)) {
        if (position < index || (position === index && !insertBefore)) {
          newIndex += op.length;
        }
      } else {
        var length = -op;
        if (position < index) {
          newIndex -= Math.min(length, index - position);
        }
        position += length;
      }
      if (position > index) { break; }
    }
    return newIndex;
  };

  TextOperation.prototype.compose = function (operation2) {
    var operation1 = this;
    if (operation1.targetLength !== operation2.baseLength) {
      throw new Error('compose: the second operation does not start where the first ends');
    }
    var operation = new TextOperation();
    var ops1 = operation1.ops, ops2 = operation2.ops;
    var i1 = 0, i2 = 0;
    var op1 = ops1[i1++], op2 = ops2[i2++];
    for (;;) {
      if (typeof op1 === 'undefined' && typeof op2 === 'undefined') { break; }
      if (isDelete(op1)) {
        operation['delete'](op1);
        op1 = ops1[i1++];
        continue;
      }
      if (isInsert(op2)) {
        operation.insert(op2);
        op2 = ops2[i2++];
        continue;
      }
      if (typeof op1 === 'undefined') { throw new Error('compose: first operation is too short'); }
      if (typeof op2 === 'undefined') { throw new Error('compose: first operation is too long'); }

      if (isRetain(op1) && isRetain(op2)) {
        if (op1 > op2) {
          operation.retain(op2);
          op1 = op1 - op2;
          op2 = ops2[i2++];
        } else if (op1 === op2) {
          operation.retain(op1);
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          operation.retain(op1);
          op2 = op2 - op1;
          op1 = ops1[i1++];
        }
      } else if (isInsert(op1) && isDelete(op2)) {
        if (op1.length > -op2) {
          op1 = op1.slice(-op2);
          op2 = ops2[i2++];
        } else if (op1.length === -op2) {
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          op2 = op2 + op1.length;
          op1 = ops1[i1++];
        }
      } else if (isInsert(op1) && isRetain(op2)) {
        if (op1.length > op2) {
          operation.insert(op1.slice(0, op2));
          op1 = op1.slice(op2);
          op2 = ops2[i2++];
        } else if (op1.length === op2) {
          operation.insert(op1);
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          operation.insert(op1);
          op2 = op2 - op1.length;
          op1 = ops1[i1++];
        }
      } else if (isRetain(op1) && isDelete(op2)) {
        if (op1 > -op2) {
          operation['delete'](op2);
          op1 = op1 + op2;
          op2 = ops2[i2++];
        } else if (op1 === -op2) {
          operation['delete'](op2);
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          operation['delete'](op1);
          op2 = op2 + op1;
          op1 = ops1[i1++];
        }
      } else {
        throw new Error('compose: incompatible operations');
      }
    }
    return operation;
  };

  // Rewrite two concurrent operations so that they can follow each other:
  // apply(apply(S, a), b') === apply(apply(S, b), a'). When both insert at
  // the same place, operation1's text goes first.
  TextOperation.transform = function (operation1, operation2) {
    if (operation1.baseLength !== operation2.baseLength) {
      throw new Error('transform: operations start from documents of different length');
    }
    var operation1prime = new TextOperation();
    var operation2prime = new TextOperation();
    var ops1 = operation1.ops, ops2 = operation2.ops;
    var i1 = 0, i2 = 0;
    var op1 = ops1[i1++], op2 = ops2[i2++];
    for (;;) {
      if (typeof op1 === 'undefined' && typeof op2 === 'undefined') { break; }
      if (isInsert(op1)) {
        operation1prime.insert(op1);
        operation2prime.retain(op1.length);
        op1 = ops1[i1++];
        continue;
      }
      if (isInsert(op2)) {
        operation1prime.retain(op2.length);
        operation2prime.insert(op2);
        op2 = ops2[i2++];
        continue;
      }
      if (typeof op1 === 'undefined') { throw new Error('transform: first operation is too short'); }
      if (typeof op2 === 'undefined') { throw new Error('transform: first operation is too long'); }

      var minl;
      if (isRetain(op1) && isRetain(op2)) {
        if (op1 > op2) {
          minl = op2;
          op1 = op1 - op2;
          op2 = ops2[i2++];
        } else if (op1 === op2) {
          minl = op2;
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          minl = op1;
          op2 = op2 - op1;
          op1 = ops1[i1++];
        }
        operation1prime.retain(minl);
        operation2prime.retain(minl);
      } else if (isDelete(op1) && isDelete(op2)) {
        // Both delete the same text: nothing left to do for either.
        if (-op1 > -op2) {
          op1 = op1 - op2;
          op2 = ops2[i2++];
        } else if (op1 === op2) {
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          op2 = op2 - op1;
          op1 = ops1[i1++];
        }
      } else if (isDelete(op1) && isRetain(op2)) {
        if (-op1 > op2) {
          minl = op2;
          op1 = op1 + op2;
          op2 = ops2[i2++];
        } else if (-op1 === op2) {
          minl = op2;
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          minl = -op1;
          op2 = op2 + op1;
          op1 = ops1[i1++];
        }
        operation1prime['delete'](minl);
      } else if (isRetain(op1) && isDelete(op2)) {
        if (op1 > -op2) {
          minl = -op2;
          op1 = op1 + op2;
          op2 = ops2[i2++];
        } else if (op1 === -op2) {
          minl = op1;
          op1 = ops1[i1++];
          op2 = ops2[i2++];
        } else {
          minl = op1;
          op2 = op2 + op1;
          op1 = ops1[i1++];
        }
        operation2prime['delete'](minl);
      } else {
        throw new Error('transform: incompatible operations');
      }
    }
    return [operation1prime, operation2prime];
  };

  function isHighSurrogate(code) { return code >= 0xd800 && code <= 0xdbff; }
  function isLowSurrogate(code) { return code >= 0xdc00 && code <= 0xdfff; }

  // The operation that turns oldText into newText, touching only the part in
  // the middle that differs. Never cuts a surrogate pair in half.
  function diffOperation(oldText, newText) {
    var limit = Math.min(oldText.length, newText.length);
    var start = 0;
    while (start < limit && oldText.charCodeAt(start) === newText.charCodeAt(start)) { start++; }
    if (start > 0 && isHighSurrogate(oldText.charCodeAt(start - 1))) { start--; }
    var end = 0;
    var maxEnd = limit - start;
    while (end < maxEnd &&
        oldText.charCodeAt(oldText.length - 1 - end) === newText.charCodeAt(newText.length - 1 - end)) {
      end++;
    }
    if (end > 0 && isLowSurrogate(oldText.charCodeAt(oldText.length - end))) { end--; }
    var operation = new TextOperation();
    operation.retain(start);
    operation['delete'](oldText.length - start - end);
    operation.insert(newText.slice(start, newText.length - end));
    operation.retain(end);
    return operation;
  }

  // 32-bit FNV-1a over UTF-16 code units; editor/ot.py computes the same.
  function textHash(text) {
    var hash = 0x811c9dc5;
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  /*
   * Client side of the protocol for one document.
   *
   * At most one operation is "outstanding" (sent, not yet acknowledged).
   * Edits made meanwhile are merged into a "buffer" that is sent when the
   * acknowledgement arrives. Operations from the server are transformed over
   * both before they are applied locally.
   *
   * hooks.send(revision, operation, id)  - transmit an operation
   * hooks.apply(operation)               - apply a server operation locally
   */
  function Client(revision, hooks) {
    this.revision = revision;
    this.hooks = hooks;
    this.outstanding = null;   // {op, id, sent}
    this.buffer = null;
    this.nextId = 1;
    this.connection = 0;       // bumped on every (re)connect
  }

  Client.prototype.isSynchronized = function () {
    return this.outstanding === null;
  };

  Client.prototype._send = function () {
    this.outstanding.sent = this.connection;
    this.hooks.send(this.revision, this.outstanding.op, this.outstanding.id);
  };

  // A local edit.
  Client.prototype.applyClient = function (operation) {
    if (operation.isNoop()) { return; }
    if (this.outstanding === null) {
      this.outstanding = { op: operation, id: this.nextId++, sent: -1 };
      this._send();
    } else if (this.buffer === null) {
      this.buffer = operation;
    } else {
      this.buffer = this.buffer.compose(operation);
    }
  };

  // An operation by somebody else, as broadcast by the server.
  Client.prototype.applyServer = function (operation) {
    this.revision++;
    if (this.outstanding !== null) {
      var pair = TextOperation.transform(this.outstanding.op, operation);
      this.outstanding.op = pair[0];
      operation = pair[1];
      if (this.buffer !== null) {
        var pair2 = TextOperation.transform(this.buffer, operation);
        this.buffer = pair2[0];
        operation = pair2[1];
      }
    }
    this.hooks.apply(operation);
  };

  // The server accepted the outstanding operation. Returns it as accepted.
  Client.prototype.serverAck = function () {
    if (this.outstanding === null) { throw new Error('ack without an outstanding operation'); }
    var acknowledged = this.outstanding.op;
    this.revision++;
    this.outstanding = null;
    if (this.buffer !== null) {
      this.outstanding = { op: this.buffer, id: this.nextId++, sent: -1 };
      this.buffer = null;
      this._send();
    }
    return acknowledged;
  };

  // After a reconnect: transmit the outstanding operation again unless it
  // was already sent on this connection.
  Client.prototype.resend = function () {
    if (this.outstanding !== null && this.outstanding.sent !== this.connection) {
      this._send();
    }
  };

  // Everything the server has not confirmed yet as one operation, or null.
  Client.prototype.pending = function () {
    if (this.outstanding === null) { return null; }
    return this.buffer === null ? this.outstanding.op : this.outstanding.op.compose(this.buffer);
  };

  Client.prototype.reset = function (revision) {
    this.revision = revision;
    this.outstanding = null;
    this.buffer = null;
  };

  return {
    TextOperation: TextOperation,
    Client: Client,
    diffOperation: diffOperation,
    textHash: textHash
  };
}));
