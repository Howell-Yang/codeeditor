/*
 * Randomised simulation of several browsers editing one document through the
 * real client code (static/js/ot.js + sync.js) and a minimal server that does
 * what editor.rooms.Document does.
 *
 * Messages travel through per-client FIFO queues and are delivered in random
 * order between clients. Clients randomly lose their connection (in-flight
 * messages in both directions are dropped) and come back, either resuming
 * from the server's history or - when the server "restarted" - from a full
 * snapshot. At the end everybody must hold exactly the server's text.
 *
 * Usage: node sync_simulation.js [rounds] [seed]
 */
'use strict';
var path = require('path');
var staticDir = path.join(__dirname, '..', '..', 'static', 'js');
var ot = require(path.join(staticDir, 'ot.js'));
var DocSync = require(path.join(staticDir, 'sync.js'));
var TextOperation = ot.TextOperation;

var rounds = parseInt(process.argv[2] || '300', 10);
var seed = parseInt(process.argv[3] || '12345', 10);

function random() {            // mulberry32
  seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
  var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
function randint(n) { return Math.floor(random() * n); }

var ALPHABET = ['a', 'b', 'c', ' ', '\n', '中', '文', '😀', 'x', '{', '}'];
function randomText(max) {
  var text = '', n = 1 + randint(max);
  for (var i = 0; i < n; i++) { text += ALPHABET[randint(ALPHABET.length)]; }
  return text;
}
function randomEdit(text) {
  // Cut positions are in code points so that surrogate pairs stay whole.
  var chars = Array.from(text);
  var start = randint(chars.length + 1);
  var removed = random() < 0.5 ? 0 : randint(Math.min(6, chars.length - start) + 1);
  var inserted = random() < 0.25 ? '' : randomText(5);
  return chars.slice(0, start).join('') + inserted + chars.slice(start + removed).join('');
}

function Server() { this.restart(''); }
Server.prototype.restart = function (text) {
  this.epoch = (this.epoch || 0) + 1;
  this.text = text;
  this.revision = 0;
  this.history = [];
};
Server.prototype.receive = function (client, message) {
  var operation = TextOperation.fromJSON(message.op);
  if (message.rev < 0 || message.rev > this.revision) { throw new Error('stale revision'); }
  for (var i = message.rev; i < this.history.length; i++) {
    var known = this.history[i];
    if (known.sid === client.sid && known.id === message.id) { throw new Error('duplicate operation'); }
    operation = TextOperation.transform(operation, known.op)[0];
  }
  this.text = operation.apply(this.text);
  this.history.push({ op: operation, sid: client.sid, id: message.id });
  this.revision++;
  return operation;
};

function SimClient(index, server, clients) {
  var self = this;
  this.sid = 'client' + index;
  this.text = '';
  this.connected = false;
  this.ready = false;
  this.connectionNumber = 0;
  this.toServer = [];
  this.fromServer = [];
  this.conflicts = 0;
  this.epoch = null;
  this.sync = new DocSync('code', this.sid, {
    getText: function () { return self.text; },
    setText: function (text) { self.text = text; },
    applyOperation: function (operation) { self.text = operation.apply(self.text); }
  }, function (message) {
    if (!self.ready) { return false; }
    self.toServer.push(message);
    return true;
  }, {
    onConflict: function () { self.conflicts++; },
    onDesync: function (key, why) { throw new Error(self.sid + ' desynchronised: ' + why); }
  });
  // A fake clock so that some edits are grouped into one undo step and some are not.
  this.clock = 0;
  this.sync.now = function () { self.clock += random() < 0.5 ? 100 : 2000; return self.clock; };
  this.server = server;
  this.clients = clients;
}
SimClient.prototype.connect = function () {
  var server = this.server;
  this.connected = true;
  this.toServer = [];
  this.fromServer = [];
  var init = { t: 'init', epoch: server.epoch };
  if (this.epoch === server.epoch && this.sync.initialized) {
    init.resumed = true;
    init.entries = server.history.slice(this.sync.revision()).map(function (entry) {
      return { op: entry.op.toJSON(), sid: entry.sid, id: entry.id };
    });
  } else {
    init.resumed = false;
    init.snapshot = { rev: server.revision, text: server.text };
  }
  this.fromServer.push(init);
};
SimClient.prototype.disconnect = function () {
  this.connected = false;
  this.ready = false;
  this.toServer = [];
  this.fromServer = [];
};
SimClient.prototype.deliverFromServer = function () {
  var message = this.fromServer.shift();
  if (message.t === 'init') {
    this.ready = true;
    this.connectionNumber++;
    this.epoch = message.epoch;
    this.sync.connected(this.connectionNumber);
    if (message.resumed) { this.sync.catchUp(message.entries); } else { this.sync.loadSnapshot(message.snapshot); }
  } else if (message.t === 'ack') {
    this.sync.onAck(message);
  } else if (message.t === 'op') {
    this.sync.onOperation(message);
  }
};
SimClient.prototype.deliverToServer = function () {
  var self = this;
  var message = this.toServer.shift();
  if (message.t !== 'op') { return; }
  var applied = this.server.receive(this, message);
  var revision = this.server.revision;
  this.fromServer.push({ t: 'ack', doc: 'code', rev: revision, id: message.id });
  this.clients.forEach(function (other) {
    // Like the real server: only connections that have said hello get it;
    // everybody else finds it in the catch-up when they (re)connect.
    if (other !== self && other.connected && other.hasJoined()) {
      other.fromServer.push({ t: 'op', doc: 'code', rev: revision, op: applied.toJSON() });
    }
  });
};
SimClient.prototype.hasJoined = function () {
  // The init message is produced at connect time in this simulation.
  return this.connected;
};

function runRound(round) {
  var server = new Server();
  server.restart(randomText(20));
  var clients = [];
  var count = 2 + randint(3);
  for (var i = 0; i < count; i++) { clients.push(new SimClient(i, server, clients)); }
  clients.forEach(function (client) { client.connect(); });

  var steps = 60 + randint(200);
  for (var step = 0; step < steps; step++) {
    var client = clients[randint(clients.length)];
    var action = random();
    if (action < 0.30) {
      if (client.sync.initialized) {
        client.text = randomEdit(client.text);
        client.sync.localChange();
      }
    } else if (action < 0.35) {
      // undo / redo of the client's own edits
      if (random() < 0.65) { client.sync.undo(); } else { client.sync.redo(); }
    } else if (action < 0.62) {
      if (client.connected && client.toServer.length) { client.deliverToServer(); }
    } else if (action < 0.92) {
      if (client.connected && client.fromServer.length) { client.deliverFromServer(); }
    } else if (action < 0.96) {
      if (client.connected) { client.disconnect(); } else { client.connect(); }
    } else if (action < 0.965) {
      // Server restart: everybody is disconnected, history is gone, the text
      // is whatever had been saved (= the current server text).
      clients.forEach(function (c) { c.disconnect(); });
      server.restart(server.text);
    }
  }

  // Quiesce: reconnect everybody and drain all queues.
  clients.forEach(function (c) { if (!c.connected) { c.connect(); } });
  var busy = true, guard = 0;
  while (busy) {
    busy = false;
    if (++guard > 100000) { throw new Error('round ' + round + ' does not settle'); }
    clients.forEach(function (c) {
      if (c.fromServer.length) { c.deliverFromServer(); busy = true; }
      if (c.toServer.length) { c.deliverToServer(); busy = true; }
    });
  }

  clients.forEach(function (c) {
    if (c.text !== server.text) {
      throw new Error('round ' + round + ': ' + c.sid + ' diverged\n  client: ' +
        JSON.stringify(c.text) + '\n  server: ' + JSON.stringify(server.text));
    }
    if (c.sync.serverText !== server.text) { throw new Error('round ' + round + ': serverText is off'); }
    if (c.sync.hasPending()) { throw new Error('round ' + round + ': pending operations left'); }
    if (c.sync.revision() !== server.revision) { throw new Error('round ' + round + ': revision is off'); }
  });
  return clients.reduce(function (sum, c) { return sum + c.conflicts; }, 0);
}

var conflicts = 0;
for (var round = 0; round < rounds; round++) { conflicts += runRound(round); }
console.log('OK ' + rounds + ' rounds, ' + conflicts + ' conflict notices');
