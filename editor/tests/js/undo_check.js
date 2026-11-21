/*
 * Undo/redo of static/js/sync.js: only the user's own edits are undone, even
 * when somebody else changed the document in between.
 * Usage: node undo_check.js
 */
'use strict';
var path = require('path');
var assert = require('assert');
var staticDir = path.join(__dirname, '..', '..', 'static', 'js');
var ot = require(path.join(staticDir, 'ot.js'));
var DocSync = require(path.join(staticDir, 'sync.js'));

function makeClient(sid, outbox) {
  var holder = { text: '' };
  var clock = { value: 0 };
  var sync = new DocSync('code', sid, {
    getText: function () { return holder.text; },
    setText: function (text) { holder.text = text; },
    applyOperation: function (operation) { holder.text = operation.apply(holder.text); }
  }, function (message) { outbox.push(message); return true; });
  sync.now = function () { return clock.value; };
  sync.connected(1);
  return { sync: sync, holder: holder, clock: clock, outbox: outbox,
    type: function (text, gap) {
      clock.value += gap === undefined ? 5000 : gap;
      holder.text = text;
      sync.localChange();
    } };
}

// --- a single user: undo everything, redo everything -----------------------
(function () {
  var a = makeClient('a', []);
  a.sync.loadSnapshot({ rev: 0, text: 'start' });
  a.type('start one');
  a.type('start one two');
  a.type('START one two');
  assert.ok(a.sync.undo()); assert.strictEqual(a.holder.text, 'start one two');
  assert.ok(a.sync.undo()); assert.strictEqual(a.holder.text, 'start one');
  assert.ok(a.sync.undo()); assert.strictEqual(a.holder.text, 'start');
  assert.ok(!a.sync.undo());
  assert.ok(a.sync.redo()); assert.strictEqual(a.holder.text, 'start one');
  assert.ok(a.sync.redo()); assert.ok(a.sync.redo());
  assert.strictEqual(a.holder.text, 'START one two');
  assert.ok(!a.sync.redo());
  // a new edit clears the redo stack
  a.sync.undo();
  a.type('start one two!');
  assert.ok(!a.sync.canRedo());
}());

// --- quick typing is one undo step --------------------------------------------
(function () {
  var a = makeClient('a', []);
  a.sync.loadSnapshot({ rev: 0, text: '' });
  a.type('h'); a.type('he', 50); a.type('hel', 50); a.type('hell', 50); a.type('hello', 50);
  a.type('hello world');   // after a pause
  a.sync.undo(); assert.strictEqual(a.holder.text, 'hello');
  a.sync.undo(); assert.strictEqual(a.holder.text, '');
}());

// --- somebody else edits in between: only my own text goes away --------------
(function () {
  var outbox = [];
  var a = makeClient('a', outbox);
  a.sync.loadSnapshot({ rev: 0, text: 'line1\nline2\n' });
  a.type('line1 MINE\nline2\n');
  // the server acknowledges it, then the other person's edit arrives
  a.sync.onAck({ id: outbox[0].id, rev: 1 });
  var theirs = new ot.TextOperation().retain(17).insert('THEIRS\n');
  a.sync.onOperation({ rev: 2, op: theirs.toJSON() });
  assert.strictEqual(a.holder.text, 'line1 MINE\nline2\nTHEIRS\n');
  a.sync.undo();
  assert.strictEqual(a.holder.text, 'line1\nline2\nTHEIRS\n');
  a.sync.redo();
  assert.strictEqual(a.holder.text, 'line1 MINE\nline2\nTHEIRS\n');
  // the undo and the redo were sent to the server as ordinary operations
  assert.strictEqual(outbox.filter(function (m) { return m.t === 'op'; }).length, 2);
}());

// --- their edit removes what I typed: nothing left to undo ---------------------
(function () {
  var outbox = [];
  var a = makeClient('a', outbox);
  a.sync.loadSnapshot({ rev: 0, text: 'abc' });
  a.type('abXc');
  a.sync.onAck({ id: outbox[0].id, rev: 1 });
  a.sync.onOperation({ rev: 2, op: new ot.TextOperation()['delete'](4).insert('zzz').toJSON() });
  assert.strictEqual(a.holder.text, 'zzz');
  a.sync.undo();
  assert.strictEqual(a.holder.text, 'zzz');
}());

console.log('OK undo');
