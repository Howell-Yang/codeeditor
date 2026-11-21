/*
 * Keeps one shared document (code, problem text, stdin) in sync with the
 * server. Editor-agnostic: the editor is reached through a small adapter.
 *
 *   adapter.getText()                     current text in the editor
 *   adapter.setText(text)                 replace everything (first load)
 *   adapter.applyOperation(op, isOwn)     apply a change, keeping the caret
 *                                         (isOwn: the user's own undo/redo)
 *
 *   send(message) -> boolean              hand a message to the socket; false
 *                                         when the connection is not ready
 *
 * Also provides undo/redo that only ever touches the user's own edits: the
 * undo stack holds inverse operations which are transformed over everything
 * the other participants do in the meantime.
 *
 * Has no DOM dependencies, so the test-suite drives it under Node.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./ot.js'));
  } else {
    root.CodeTool = root.CodeTool || {};
    root.CodeTool.DocSync = factory(root.CodeTool.ot);
  }
}(typeof self !== 'undefined' ? self : this, function (ot) {
  'use strict';

  var TextOperation = ot.TextOperation;
  var UNDO_LIMIT = 300;
  var UNDO_GROUP_MS = 700;

  function DocSync(key, sid, adapter, send, callbacks) {
    var self = this;
    this.key = key;
    this.sid = sid;
    this.adapter = adapter;
    this.send = send;
    this.callbacks = callbacks || {};
    this.initialized = false;
    this.serverText = '';   // the document at client.revision, as the server has it
    this.shadow = '';       // the editor content we last looked at
    this.applyingRemote = false;
    this.lastCheckedRevision = -1;
    this.undoStack = [];
    this.redoStack = [];
    this.lastEditAt = 0;
    this.now = function () { return Date.now(); };
    this.client = new ot.Client(0, {
      send: function (revision, operation, id) {
        return self.send({ t: 'op', doc: self.key, rev: revision, id: id, op: operation.toJSON() });
      },
      apply: function (operation) { self._applyRemote(operation); }
    });
    // Remember whether the transmission really happened (see Client.resend).
    this.client._send = function () {
      var sent = this.hooks.send(this.revision, this.outstanding.op, this.outstanding.id);
      this.outstanding.sent = sent ? this.connection : -1;
    };
  }

  // ---- changes coming from the server ---------------------------------------

  DocSync.prototype._applyRemote = function (operation) {
    if (operation.isNoop()) { return; }
    this.applyingRemote = true;
    try {
      this.adapter.applyOperation(operation, false);
    } finally {
      this.applyingRemote = false;
    }
    this.shadow = this.adapter.getText();
    this.undoStack = transformStack(this.undoStack, operation);
    this.redoStack = transformStack(this.redoStack, operation);
  };

  // Rewrite a stack of inverse operations so that they still apply after
  // `operation` (somebody else's change) happened underneath them.
  function transformStack(stack, operation) {
    var result = [];
    for (var i = stack.length - 1; i >= 0; i--) {
      var pair = TextOperation.transform(stack[i], operation);
      if (!pair[0].isNoop()) { result.push(pair[0]); }
      operation = pair[1];
    }
    return result.reverse();
  }

  // ---- changes made by the user -----------------------------------------------

  // The user changed the editor: turn the difference into an operation.
  DocSync.prototype.localChange = function () {
    if (this.applyingRemote || !this.initialized) { return; }
    var text = this.adapter.getText();
    if (text === this.shadow) { return; }
    var operation = ot.diffOperation(this.shadow, text);
    var inverse = operation.invert(this.shadow);
    var now = this.now();
    if (this.undoStack.length && now - this.lastEditAt < UNDO_GROUP_MS) {
      // Typing in one go is undone in one go.
      this.undoStack.push(inverse.compose(this.undoStack.pop()));
    } else {
      this.undoStack.push(inverse);
      if (this.undoStack.length > UNDO_LIMIT) { this.undoStack.shift(); }
    }
    this.lastEditAt = now;
    this.redoStack = [];
    this.shadow = text;
    this.client.applyClient(operation);
    this._changed();
  };

  DocSync.prototype.canUndo = function () { return this.undoStack.length > 0; };
  DocSync.prototype.canRedo = function () { return this.redoStack.length > 0; };

  DocSync.prototype.undo = function () { return this._replay(this.undoStack, this.redoStack); };
  DocSync.prototype.redo = function () { return this._replay(this.redoStack, this.undoStack); };

  DocSync.prototype._replay = function (from, to) {
    if (!this.initialized) { return false; }
    this.localChange();          // pick up anything not yet seen
    if (!from.length) { return false; }
    var operation = from.pop();
    var inverse = operation.invert(this.shadow);
    this.applyingRemote = true;
    try {
      this.adapter.applyOperation(operation, true);
    } finally {
      this.applyingRemote = false;
    }
    this.shadow = this.adapter.getText();
    to.push(inverse);
    this.lastEditAt = 0;         // the next edit starts a new undo step
    this.client.applyClient(operation);
    this._changed();
    return true;
  };

  DocSync.prototype._changed = function () {
    if (this.callbacks.onPendingChange) { this.callbacks.onPendingChange(this.hasPending()); }
  };

  DocSync.prototype.hasPending = function () {
    return !this.client.isSynchronized();
  };

  // ---- connection life-cycle ------------------------------------------------------

  // A new connection is ready; operations may be sent again.
  DocSync.prototype.connected = function (connectionNumber) {
    this.client.connection = connectionNumber;
  };

  // Full state from the server (first load, or the server could not resume).
  DocSync.prototype.loadSnapshot = function (snapshot) {
    var text = snapshot.text;
    if (!this.initialized) {
      this.client.reset(snapshot.rev);
      this.serverText = text;
      this.applyingRemote = true;
      try { this.adapter.setText(text); } finally { this.applyingRemote = false; }
      this.shadow = this.adapter.getText();
      this.initialized = true;
      this._changed();
      return;
    }
    this.localChange();
    var localText = this.shadow;
    var pending = this.client.pending();
    this.client.reset(snapshot.rev);
    if (pending === null) {
      this.serverText = text;
      if (localText !== text) { this._applyRemote(ot.diffOperation(localText, text)); }
    } else {
      // Three-way merge. Our unconfirmed edits (`pending`) and whatever
      // happened on the server (`serverChange`) both start from the last
      // text we had confirmed, so they can be transformed over each other:
      // the editor gets the server's changes, the server gets our edits.
      var serverChange = ot.diffOperation(this.serverText, text);
      var pair = TextOperation.transform(pending, serverChange);
      this.serverText = text;
      this._applyRemote(pair[1]);
      this.client.applyClient(pair[0]);
      if (!serverChange.isNoop() && this.callbacks.onMerged) { this.callbacks.onMerged(); }
    }
    this._changed();
  };

  // The operations we missed while disconnected (same server state as before).
  DocSync.prototype.catchUp = function (entries) {
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      var outstanding = this.client.outstanding;
      if (entry.sid === this.sid && outstanding !== null && entry.id === outstanding.id) {
        this._acknowledge();
      } else {
        this._serverOperation(TextOperation.fromJSON(entry.op));
      }
    }
    this.client.resend();
    this._changed();
  };

  DocSync.prototype._acknowledge = function () {
    var acknowledged = this.client.outstanding.op;
    this.serverText = acknowledged.apply(this.serverText);
    this.client.serverAck();
  };

  DocSync.prototype._serverOperation = function (operation) {
    this.serverText = operation.apply(this.serverText);
    this.client.applyServer(operation);
  };

  // ---- messages -----------------------------------------------------------------------

  DocSync.prototype.onAck = function (message) {
    if (this.client.outstanding === null || message.id !== this.client.outstanding.id) {
      return this._desynchronized('unexpected ack');
    }
    this._acknowledge();
    if (message.rev !== this.client.revision) { return this._desynchronized('revision mismatch after ack'); }
    this._changed();
  };

  DocSync.prototype.onOperation = function (message) {
    if (message.rev !== this.client.revision + 1) {
      return this._desynchronized('missed an operation');
    }
    try {
      this._serverOperation(TextOperation.fromJSON(message.op));
    } catch (error) {
      return this._desynchronized(String(error));
    }
    this._changed();
  };

  // The server told us to drop our state for this document.
  DocSync.prototype.onResync = function (message) {
    this.localChange();
    var localText = this.shadow;
    var hadPending = this.client.pending() !== null;
    this.client.reset(message.rev);
    this.serverText = message.text;
    this.undoStack = [];
    this.redoStack = [];
    if (localText !== message.text) {
      this.applyingRemote = true;
      try {
        this.adapter.applyOperation(ot.diffOperation(localText, message.text), false);
      } finally {
        this.applyingRemote = false;
      }
      this.shadow = this.adapter.getText();
      if (hadPending && this.callbacks.onConflict) { this.callbacks.onConflict(localText, message.reason); }
    }
    this._changed();
  };

  DocSync.prototype._desynchronized = function (why) {
    // Should not happen; the page reconnects to get a clean copy.
    if (this.callbacks.onDesync) { this.callbacks.onDesync(this.key, why); }
  };

  // Ask the server to compare checksums when nothing is in flight.
  DocSync.prototype.maybeCheck = function () {
    if (!this.initialized || this.hasPending()) { return; }
    if (this.lastCheckedRevision === this.client.revision) { return; }
    if (this.adapter.getText() !== this.shadow) { return; }
    if (this.send({ t: 'check', doc: this.key, rev: this.client.revision, hash: ot.textHash(this.shadow) })) {
      this.lastCheckedRevision = this.client.revision;
    }
  };

  DocSync.prototype.revision = function () { return this.client.revision; };

  return DocSync;
}));
