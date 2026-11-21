/*
 * The interview room: connection, shared documents, presence, run, timer.
 * The protocol is documented at the top of editor/consumers.py.
 */
(function () {
  'use strict';
  var CT = window.CodeTool;
  var DocSync = CT.DocSync;
  var config = JSON.parse(document.getElementById('room-config').textContent);
  var $ = function (id) { return document.getElementById(id); };

  var languages = {};
  config.languages.forEach(function (language) { languages[language.key] = language; });
  var defaultLanguage = languages.python ? 'python' : (config.languages[0] || { key: 'python' }).key;

  var EXTENSIONS = { python: 'py', cpp: 'cpp', c: 'c', javascript: 'js' };
  var STATUS_TEXT = {
    ok: ['运行成功', 'ok'],
    compile_error: ['编译失败', 'warn'],
    time_limit: ['运行超时', 'warn'],
    memory_limit: ['内存超限', 'warn'],
    output_limit: ['输出超限', 'warn'],
    runtime_error: ['运行出错', 'bad'],
    internal_error: ['系统错误', 'bad'],
    saved: ['上次的输出', 'muted']
  };

  // Optional URL parameters: ?name=<display name> (handy in invitation
  // links) and ?flags=1 (always show the name tags of the other carets).
  (function () {
    var name = /[?&]name=([^&]*)/.exec(location.search);
    if (name) {
      try {
        var value = decodeURIComponent(name[1].replace(/\+/g, ' ')).replace(/[\u0000-\u001f<>]/g, '').trim();
        if (value) { CT.store.set('ct:name', value.slice(0, 20)); }
      } catch (error) { /* malformed escape: ignore */ }
    }
    if (/[?&]flags=1\b/.test(location.search)) { document.body.classList.add('show-flags'); }
  }());

  function randomId() {
    var bytes = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  // ---- state ---------------------------------------------------------------------------

  var sid = randomId();
  var socket = null;
  var ready = false;              // init received on the current connection
  var everConnected = false;
  var connectionNumber = 0;
  var reconnectDelay = 500;
  var reconnectTimer = null;
  var lastMessageAt = 0;
  var stopReconnecting = false;
  var serverOffset = 0;           // server clock - local clock (ms)
  var epoch = null;
  var me = null;
  var peers = {};                 // id -> {id, name, color, auth, cursor}
  var language = defaultLanguage;
  var timer = { running: false, started_at: null, elapsed: 0 };
  var isVip = false;
  var running = false;
  var runRequested = false;
  var composing = { code: false, problem: false, stdin: false };
  var deferred = { code: [], problem: [], stdin: [] };

  // ---- editor ----------------------------------------------------------------------------

  var host = $('editor-host');
  var cm = window.CodeMirror(host, {
    value: '',
    mode: (languages[language] || {}).mode || 'python',
    theme: 'ct',
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    smartIndent: true,
    autoCloseBrackets: true,
    matchBrackets: true,
    styleActiveLine: true,
    undoDepth: 0,                 // undo is handled by DocSync (own edits only)
    readOnly: true,
    viewportMargin: 20,
    extraKeys: {
      'Ctrl-Enter': function () { requestRun(); },
      'Cmd-Enter': function () { requestRun(); },
      'Ctrl-/': 'toggleComment',
      'Cmd-/': 'toggleComment',
      'Ctrl-Z': function () { docs.code.undo(); },
      'Cmd-Z': function () { docs.code.undo(); },
      'Ctrl-Y': function () { docs.code.redo(); },
      'Cmd-Y': function () { docs.code.redo(); },
      'Shift-Ctrl-Z': function () { docs.code.redo(); },
      'Shift-Cmd-Z': function () { docs.code.redo(); },
      'Ctrl-S': function () { CT.toast('内容会自动保存，不用手动保存', 'info'); },
      'Cmd-S': function () { CT.toast('内容会自动保存，不用手动保存', 'info'); },
      Tab: function (editor) {
        if (editor.somethingSelected()) {
          editor.indentSelection('add');
        } else {
          editor.replaceSelection(new Array(editor.getOption('indentUnit') + 1).join(' '), 'end', '+input');
        }
      },
      'Shift-Tab': 'indentLess'
    }
  });

  function applyToCodeMirror(operation, isOwn) {
    var lastIndex = null;
    cm.operation(function () {
      var index = 0;
      operation.ops.forEach(function (op) {
        if (typeof op === 'string') {
          cm.replaceRange(op, cm.posFromIndex(index), null, 'remote');
          index += op.length;
          lastIndex = index;
        } else if (op > 0) {
          index += op;
        } else {
          cm.replaceRange('', cm.posFromIndex(index), cm.posFromIndex(index - op), 'remote');
          lastIndex = index;
        }
      });
      if (isOwn && lastIndex !== null) { cm.setCursor(cm.posFromIndex(lastIndex)); }
    });
  }

  function textareaAdapter(element) {
    return {
      getText: function () { return element.value; },
      setText: function (text) { element.value = text; },
      applyOperation: function (operation, isOwn) {
        var focused = document.activeElement === element;
        var start = element.selectionStart, end = element.selectionEnd, scroll = element.scrollTop;
        element.value = operation.apply(element.value);
        if (focused) {
          if (isOwn) {
            var caret = operation.transformIndex(end, false);
            element.setSelectionRange(caret, caret);
          } else {
            element.setSelectionRange(operation.transformIndex(start, false), operation.transformIndex(end, false));
          }
        }
        element.scrollTop = scroll;
      }
    };
  }

  var problemEditor = $('problem-editor');
  var stdinEditor = $('stdin-editor');

  function send(message) {
    if (!ready || !socket || socket.readyState !== 1) { return false; }
    socket.send(JSON.stringify(message));
    return true;
  }

  var syncCallbacks = {
    onPendingChange: function () { renderSyncStatus(); },
    onMerged: function () { CT.toast('已自动合并离线期间双方的修改', 'info'); },
    onConflict: function (localText, reason) {
      CT.toast(reason || '内容已和服务器重新同步', 'warn', {
        duration: 12000,
        action: '复制我刚才的版本',
        onAction: function () {
          CT.copy(localText).then(function () { CT.toast('已复制到剪贴板', 'ok'); });
        }
      });
    },
    onDesync: function () {
      // Start over with a clean copy from the server.
      if (socket) { socket.close(4000); }
    }
  };

  var docs = {
    code: new DocSync('code', sid, {
      getText: function () { return cm.getValue(); },
      setText: function (text) { cm.setValue(text); },
      applyOperation: applyToCodeMirror
    }, send, syncCallbacks),
    problem: new DocSync('problem', sid, textareaAdapter(problemEditor), send, syncCallbacks),
    stdin: new DocSync('stdin', sid, textareaAdapter(stdinEditor), send, syncCallbacks)
  };

  cm.on('changes', function () {
    docs.code.localChange();
  });
  problemEditor.addEventListener('input', function () {
    docs.problem.localChange();
    scheduleProblemRender();
  });
  stdinEditor.addEventListener('input', function () { docs.stdin.localChange(); });

  function bindUndoKeys(element, key) {
    element.addEventListener('keydown', function (event) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) { return; }
      var letter = (event.key || '').toLowerCase();
      if (letter === 'z' && !event.shiftKey) {
        event.preventDefault();
        docs[key].undo();
      } else if (letter === 'y' || (letter === 'z' && event.shiftKey)) {
        event.preventDefault();
        docs[key].redo();
      } else if (letter === 'enter') {
        event.preventDefault();
        requestRun();
      } else {
        return;
      }
      if (key === 'problem') { scheduleProblemRender(); }
    });
  }
  bindUndoKeys(problemEditor, 'problem');
  bindUndoKeys(stdinEditor, 'stdin');

  // While an input method is composing (Chinese, Japanese, ...), remote
  // changes to that document wait: rewriting the text under the composition
  // would cancel it.
  function watchComposition(element, key) {
    element.addEventListener('compositionstart', function () { composing[key] = true; });
    element.addEventListener('compositionend', function () {
      composing[key] = false;
      setTimeout(function () { flushDeferred(key); }, 0);
    });
  }
  watchComposition(cm.getInputField(), 'code');
  watchComposition(problemEditor, 'problem');
  watchComposition(stdinEditor, 'stdin');

  function flushDeferred(key) {
    if (composing[key]) { return; }
    var queue = deferred[key];
    deferred[key] = [];
    queue.forEach(handleDocumentMessage);
  }

  // ---- connection -----------------------------------------------------------------------------

  function setConnection(state, text) {
    var element = $('conn-status');
    element.setAttribute('data-state', state);
    element.lastElementChild.textContent = text;
  }

  function connect() {
    if (stopReconnecting) { return; }
    clearTimeout(reconnectTimer);
    setConnection('connecting', everConnected ? '正在重新连接…' : '正在连接…');
    var protocol = location.protocol === 'https:' ? 'wss://' : 'ws://';
    var current = socket = new WebSocket(protocol + location.host + config.wsPath);
    current.onopen = function () {
      lastMessageAt = Date.now();
      var hello = { t: 'hello', sid: sid, name: CT.store.get('ct:name', '') || config.user.name || '' };
      if (epoch !== null && docs.code.initialized) {
        hello.resume = {
          epoch: epoch,
          revs: { code: docs.code.revision(), problem: docs.problem.revision(), stdin: docs.stdin.revision() }
        };
      }
      current.send(JSON.stringify(hello));
    };
    current.onmessage = function (event) {
      if (current !== socket) { return; }
      lastMessageAt = Date.now();
      var message;
      try { message = JSON.parse(event.data); } catch (error) { return; }
      handleMessage(message);
    };
    current.onclose = function (event) {
      if (current !== socket) { return; }
      ready = false;
      socket = null;
      Object.keys(deferred).forEach(function (key) { deferred[key] = []; });
      if (event.code === 4409) {
        stopReconnecting = true;
        setConnection('offline', '房间人数已满');
        CT.toast('房间人数已满，暂时无法加入', 'error', { duration: 0 });
        return;
      }
      if (event.code === 4404) {
        stopReconnecting = true;
        setConnection('offline', '房间不存在');
        return;
      }
      setConnection('offline', '连接已断开，正在重连…');
      renderSyncStatus();
      if (running) { setRunning(false); }
      reconnectTimer = setTimeout(connect, reconnectDelay + Math.random() * 300);
      reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    };
  }

  setInterval(function () {
    if (!socket) { return; }
    if (socket.readyState === 1 && Date.now() - lastMessageAt > 45000) {
      socket.close(4001);         // silent for too long: assume it is dead
      return;
    }
    if (ready) {
      send({ t: 'ping' });
      Object.keys(docs).forEach(function (key) { docs[key].maybeCheck(); });
    }
  }, 15000);

  window.addEventListener('online', function () {
    if (!socket && !stopReconnecting) { reconnectDelay = 500; connect(); }
  });

  window.addEventListener('beforeunload', function (event) {
    var pending = Object.keys(docs).some(function (key) { return docs[key].hasPending(); });
    if (pending && !ready) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  // ---- messages --------------------------------------------------------------------------------

  function handleMessage(message) {
    switch (message.t) {
      case 'init': return onInit(message);
      case 'ack':
      case 'op':
      case 'resync':
        if (!docs[message.doc]) { return undefined; }
        if (composing[message.doc] || deferred[message.doc].length) {
          deferred[message.doc].push(message);
          return undefined;
        }
        return handleDocumentMessage(message);
      case 'cursor': return onCursor(message);
      case 'join':
        peers[message.peer.id] = message.peer;
        renderPeers();
        if (pendingLeaves[message.peer.name]) {
          // Same person back after a network hiccup: no need to announce it.
          clearTimeout(pendingLeaves[message.peer.name]);
          delete pendingLeaves[message.peer.name];
        } else {
          CT.toast(message.peer.name + ' 进入了房间', 'info');
        }
        return undefined;
      case 'leave': return onLeave(message);
      case 'peer':
        if (me && message.peer.id === me.id) {
          me = message.peer;                 // our own new name, confirmed by the server
        } else {
          peers[message.peer.id] = Object.assign(peers[message.peer.id] || {}, message.peer);
          refreshCursor(message.peer.id);
        }
        renderPeers();
        return undefined;
      case 'state': return onState(message);
      case 'run': return onRun(message);
      case 'error': return onError(message);
      default: return undefined;
    }
  }

  function handleDocumentMessage(message) {
    var doc = docs[message.doc];
    if (!doc) { return; }
    if (message.t === 'ack') {
      doc.onAck(message);
    } else if (message.t === 'op') {
      doc.onOperation(message);
    } else {
      doc.onResync(message);
    }
    if (message.doc === 'problem') { scheduleProblemRender(); }
  }

  function onInit(message) {
    ready = true;
    connectionNumber++;
    reconnectDelay = 500;
    serverOffset = message.now - Date.now();
    epoch = message.epoch;
    me = message.you;
    Object.keys(peers).forEach(clearCursor);
    peers = {};
    message.peers.forEach(function (peer) { peers[peer.id] = peer; });

    Object.keys(docs).forEach(function (key) {
      docs[key].connected(connectionNumber);
      if (message.resumed) {
        docs[key].catchUp(message.catchup[key] || []);
      } else {
        docs[key].loadSnapshot(message.docs[key]);
      }
    });

    setLanguage(message.state.language, true);
    timer = message.state.timer;
    isVip = !!message.state.is_vip;
    setRunning(!!message.state.running);
    if (message.last_run && !running) { renderRun(message.last_run); }

    if (!everConnected) {
      everConnected = true;
      cm.setOption('readOnly', false);
      var loading = $('editor-loading');
      if (loading) { loading.parentNode.removeChild(loading); }
      cm.refresh();
      afterFirstInit();
    }
    setConnection('online', '已连接');
    renderPeers();
    Object.keys(peers).forEach(refreshCursor);
    renderTimer();
    renderPin();
    renderSyncStatus();
    scheduleProblemRender();
    sendCursor();
  }

  function onError(message) {
    if (message.code === 'busy' || message.code === 'rate' || message.code === 'run') {
      runRequested = false;
      if (!running) { setRunning(false); }
    }
    CT.toast(message.message || '出错了', message.code === 'busy' ? 'warn' : 'error');
  }

  function onState(message) {
    if (message.key === 'language') {
      setLanguage(message.value, true);
    } else if (message.key === 'timer') {
      timer = message.value;
      if (message.now) { serverOffset = message.now - Date.now(); }
      renderTimer();
    } else if (message.key === 'is_vip') {
      isVip = !!message.value;
      renderPin();
    }
  }

  // ---- presence ------------------------------------------------------------------------------------

  function initial(name) {
    name = String(name || '?').trim();
    var guest = /^访客(\d+)$/.exec(name);      // default names: show the number
    if (guest) { return guest[1]; }
    return (Array.from(name)[0] || '?').toUpperCase();
  }

  function renderPeers() {
    var container = $('peers');
    container.textContent = '';
    var everyone = [];
    if (me) { everyone.push(Object.assign({ isMe: true }, me)); }
    Object.keys(peers).forEach(function (id) { everyone.push(peers[id]); });
    everyone.forEach(function (peer) {
      var avatar = CT.el('button', {
        type: 'button',
        'class': 'peer' + (peer.isMe ? ' is-me' : ''),
        style: 'background:' + peer.color,
        title: peer.name + (peer.isMe ? '（我，点击改名）' : ''),
        'aria-label': peer.name
      }, [initial(peer.name)]);
      if (peer.isMe) {
        avatar.addEventListener('click', function () {
          $('name-form').elements.name.value = me.name;
          CT.openModal('name-modal');
        });
      } else {
        avatar.addEventListener('click', function () { jumpToPeer(peer.id); });
      }
      container.appendChild(avatar);
    });
    container.appendChild(CT.el('span', { 'class': 'peer-count', text: everyone.length + ' 人在线' }));
  }

  var pendingLeaves = {};          // name -> timer; a short absence is not announced

  function onLeave(message) {
    var peer = peers[message.id];
    clearCursor(message.id);
    delete peers[message.id];
    renderPeers();
    if (!peer) { return; }
    clearTimeout(pendingLeaves[peer.name]);
    pendingLeaves[peer.name] = setTimeout(function () {
      delete pendingLeaves[peer.name];
      CT.toast(peer.name + ' 离开了房间', 'info');
    }, 8000);
  }

  $('name-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var name = this.elements.name.value.trim().slice(0, 20);
    if (!name) { return; }
    CT.store.set('ct:name', name);
    send({ t: 'name', name: name });
    CT.closeModal('name-modal');
  });

  // ---- remote cursors (code editor) -----------------------------------------------------------------

  var cursorMarks = {};            // peer id -> {caret, selection, flagTimer}

  function clearCursor(id) {
    var marks = cursorMarks[id];
    if (!marks) { return; }
    if (marks.caret) { marks.caret.clear(); }
    if (marks.selection) { marks.selection.clear(); }
    clearTimeout(marks.flagTimer);
    delete cursorMarks[id];
  }

  function hexToRgba(hex, alpha) {
    var value = parseInt(hex.slice(1), 16);
    return 'rgba(' + ((value >> 16) & 255) + ',' + ((value >> 8) & 255) + ',' + (value & 255) + ',' + alpha + ')';
  }

  function refreshCursor(id) {
    var peer = peers[id];
    clearCursor(id);
    if (!peer || !peer.cursor || peer.cursor.doc !== 'code') { return; }
    var length = cm.getValue().length;
    var anchor = Math.min(peer.cursor.a, length);
    var head = Math.min(peer.cursor.h, length);
    var flag = CT.el('span', { 'class': 'remote-flag', style: 'background:' + peer.color, text: peer.name });
    var caret = CT.el('span', { 'class': 'remote-caret is-fresh', style: 'border-color:' + peer.color }, [flag]);
    var marks = cursorMarks[id] = {};
    marks.caret = cm.setBookmark(cm.posFromIndex(head), { widget: caret, insertLeft: true });
    if (anchor !== head) {
      marks.selection = cm.markText(
        cm.posFromIndex(Math.min(anchor, head)), cm.posFromIndex(Math.max(anchor, head)),
        { css: 'background-color:' + hexToRgba(peer.color, 0.28) });
    }
    marks.flagTimer = setTimeout(function () { caret.classList.remove('is-fresh'); }, 2500);
  }

  function onCursor(message) {
    var peer = peers[message.by];
    if (!peer) { return; }
    peer.cursor = message.cursor;
    refreshCursor(message.by);
  }

  function jumpToPeer(id) {
    var marks = cursorMarks[id];
    if (!marks || !marks.caret) { return; }
    var position = marks.caret.find();
    if (position) { cm.scrollIntoView(position, 80); }
  }

  var cursorTimer = null;
  function sendCursor() {
    cursorTimer = null;
    if (!ready || !docs.code.initialized) { return; }
    var selection = cm.listSelections()[0];
    send({ t: 'cursor', doc: 'code', a: cm.indexFromPos(selection.anchor), h: cm.indexFromPos(selection.head) });
  }
  cm.on('cursorActivity', function () {
    var head = cm.getCursor();
    $('cursor-status').textContent = '行 ' + (head.line + 1) + '，列 ' + (head.ch + 1);
    if (cursorTimer === null) { cursorTimer = setTimeout(sendCursor, 80); }
  });

  // ---- status bar ---------------------------------------------------------------------------------------

  function renderSyncStatus() {
    var pending = Object.keys(docs).some(function (key) { return docs[key].hasPending(); });
    var element = $('sync-status');
    if (!everConnected) {
      element.textContent = '';
    } else if (pending) {
      element.textContent = ready ? '同步中…' : '有尚未同步的修改，恢复连接后自动提交';
    } else {
      element.textContent = '已同步 · 自动保存';
    }
    element.classList.toggle('is-warn', pending && !ready);
  }

  // ---- language, font, template, download ------------------------------------------------------------------

  var languageSelect = $('language-select');

  function setLanguage(key, fromServer) {
    if (!languages[key]) { key = defaultLanguage; }
    var previous = language;
    language = key;
    languageSelect.value = key;
    cm.setOption('mode', languages[key] ? languages[key].mode : 'python');
    if (!fromServer && key !== previous) {
      send({ t: 'set', key: 'language', value: key });
      // Swap the starter code along if the user had not written anything yet.
      var kind = pristineKind(cm.getValue());
      if (kind === 'starter') {
        replaceCode(config.starters[key] || languages[key].template || '');
      } else if (kind === 'template') {
        replaceCode(languages[key].template || '');
      }
    }
  }

  // 'template' / 'starter' when the code is still exactly one of the texts
  // we put there ourselves (or empty), null once somebody has worked on it.
  function pristineKind(code) {
    var trimmed = code.trim();
    if (!trimmed) { return 'template'; }
    if (config.languages.some(function (item) { return item.template.trim() === trimmed; })) {
      return 'template';
    }
    var starters = config.starters || {};
    if (Object.keys(starters).some(function (key) { return starters[key].trim() === trimmed; })) {
      return 'starter';
    }
    return null;
  }

  function isUntouched(code) {
    return pristineKind(code) !== null;
  }

  function replaceCode(text) {
    cm.setValue(text);               // goes through the normal "changes" path
    cm.setCursor(cm.posFromIndex(text.length));
  }

  languageSelect.addEventListener('change', function () { setLanguage(this.value, false); });

  $('template-button').addEventListener('click', function () {
    var template = (languages[language] || {}).template || '';
    if (isUntouched(cm.getValue(), language)) {
      replaceCode(template);
      return;
    }
    CT.confirm('用 ' + languages[language].name + ' 的示例代码替换当前代码？（可以用 Ctrl+Z 撤销）', '替换')
      .then(function (yes) { if (yes) { replaceCode(template); } });
  });

  var fontSize = Math.min(24, Math.max(11, parseInt(CT.store.get('ct:font', '14'), 10) || 14));
  function applyFontSize() {
    host.style.fontSize = fontSize + 'px';
    $('font-size-value').textContent = fontSize;
    cm.refresh();
  }
  $('font-smaller').addEventListener('click', function () {
    fontSize = Math.max(11, fontSize - 1); CT.store.set('ct:font', String(fontSize)); applyFontSize();
  });
  $('font-larger').addEventListener('click', function () {
    fontSize = Math.min(24, fontSize + 1); CT.store.set('ct:font', String(fontSize)); applyFontSize();
  });
  applyFontSize();

  $('download-button').addEventListener('click', function () {
    var blob = new Blob([cm.getValue()], { type: 'text/plain;charset=utf-8' });
    var link = CT.el('a', { href: URL.createObjectURL(blob), download: 'main.' + (EXTENSIONS[language] || 'txt') });
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  });

  $('invite-button').addEventListener('click', function () {
    CT.copy(location.origin + '/' + config.room + '/').then(function (ok) {
      CT.toast(ok ? '房间链接已复制，发给对方即可加入' : '复制失败，请手动复制地址栏里的链接', ok ? 'ok' : 'warn');
    });
  });

  // ---- run ---------------------------------------------------------------------------------------------------

  var runButton = $('run-button');

  function setRunning(value, byName) {
    running = value;
    runButton.disabled = value;
    runButton.classList.toggle('is-busy', value);
    if (value) {
      var status = $('run-status');
      status.className = 'run-status chip chip-wait';
      status.textContent = (byName ? byName + ' ' : '') + '正在运行…';
      $('run-meta').textContent = '';
    }
  }

  function requestRun() {
    if (running || runRequested) { return; }
    if (!ready) { CT.toast('还没有连接到服务器，稍后再试', 'warn'); return; }
    runRequested = true;
    setRunning(true, '');
    // The server runs its own copy of the code: wait until our last
    // keystrokes have been confirmed.
    var deadline = Date.now() + 3000;
    (function waitForSync() {
      docs.code.localChange();
      docs.stdin.localChange();
      if ((docs.code.hasPending() || docs.stdin.hasPending()) && Date.now() < deadline && ready) {
        setTimeout(waitForSync, 30);
        return;
      }
      if (!send({ t: 'run' })) {
        runRequested = false;
        setRunning(false);
        $('run-status').textContent = '';
        CT.toast('连接已断开，无法运行', 'warn');
      }
    }());
    showTab('io');
  }

  runButton.addEventListener('click', requestRun);
  document.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.defaultPrevented &&
        !event.target.closest('.modal')) {
      event.preventDefault();
      requestRun();
    }
  });

  function onRun(message) {
    if (message.state === 'started') {
      runRequested = false;
      setRunning(true, message.name);
    } else if (message.state === 'done') {
      runRequested = false;
      setRunning(false);
      renderRun(message.result);
    }
  }

  function outputBlock(className, label, text) {
    var block = CT.el('div', { 'class': 'out-block ' + className });
    if (label) { block.appendChild(CT.el('div', { 'class': 'out-label', text: label })); }
    block.appendChild(CT.el('pre', { text: text }));
    return block;
  }

  function renderRun(result) {
    var info = STATUS_TEXT[result.status] || ['运行结束', 'muted'];
    var status = $('run-status');
    status.className = 'run-status chip chip-' + info[1];
    status.textContent = info[0];

    var meta = [];
    if (!result.saved) {
      if (result.status !== 'compile_error' && result.status !== 'internal_error') {
        meta.push((result.cpu_ms || 0) + ' ms');
        meta.push(((result.memory_kb || 0) / 1024).toFixed(1) + ' MB');
      }
      if (result.name) { meta.push('由 ' + result.name + ' 运行'); }
    }
    $('run-meta').textContent = meta.join(' · ');

    var output = $('run-output');
    output.textContent = '';
    if (result.message && result.status !== 'ok' && !result.saved) {
      output.appendChild(CT.el('p', { 'class': 'out-message', text: result.message }));
    }
    if (result.status === 'compile_error' && result.compile_output) {
      output.appendChild(outputBlock('out-stderr', '编译器输出', result.compile_output));
    } else {
      if (result.stdout) {
        output.appendChild(outputBlock('out-stdout', '', result.stdout));
      } else if (result.status === 'ok') {
        output.appendChild(CT.el('p', { 'class': 'io-placeholder', text: '（程序没有输出任何内容）' }));
      }
      if (result.stderr) {
        output.appendChild(outputBlock('out-stderr', result.saved ? '' : '标准错误', result.stderr));
      }
    }
    if (result.truncated) {
      output.appendChild(CT.el('p', { 'class': 'out-note', text: '输出过长，只显示了前面一部分。' }));
    }
    output.scrollTop = 0;
  }

  // ---- timer ------------------------------------------------------------------------------------------------------

  function timerElapsed() {
    var elapsed = timer.elapsed || 0;
    if (timer.running && timer.started_at) {
      elapsed += Math.max(0, Date.now() + serverOffset - timer.started_at);
    }
    return elapsed;
  }

  function renderTimer() {
    var total = Math.floor(timerElapsed() / 1000);
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = total % 60;
    var text = (hours ? hours + ':' : '') + ('0' + minutes).slice(-2) + ':' + ('0' + seconds).slice(-2);
    $('timer-value').textContent = text;
    $('timer').setAttribute('data-running', timer.running ? 'true' : 'false');
  }
  setInterval(renderTimer, 500);

  $('timer-toggle').addEventListener('click', function () {
    if (!send({ t: 'timer', action: timer.running ? 'pause' : 'start' })) { CT.toast('还没有连接到服务器', 'warn'); }
  });
  $('timer-reset').addEventListener('click', function () {
    if (timerElapsed() < 1000) { return; }
    CT.confirm('把计时器清零？房间里所有人的计时都会归零。', '清零').then(function (yes) {
      if (yes) { send({ t: 'timer', action: 'reset' }); }
    });
  });

  // ---- problem panel ------------------------------------------------------------------------------------------------

  var problemMode = 'preview';
  var problemRenderTimer = null;

  function renderProblem() {
    problemRenderTimer = null;
    var text = problemEditor.value;
    var empty = !text.trim();
    $('problem-preview').innerHTML = empty ? '' : CT.markdown(text);
    $('problem-preview').hidden = problemMode !== 'preview' || empty;
    $('problem-empty').hidden = problemMode !== 'preview' || !empty;
    problemEditor.hidden = problemMode !== 'edit';
  }
  function scheduleProblemRender() {
    if (problemRenderTimer === null) { problemRenderTimer = setTimeout(renderProblem, 120); }
  }
  function setProblemMode(mode) {
    problemMode = mode;
    $('problem-mode').querySelectorAll('button').forEach(function (button) {
      button.classList.toggle('is-active', button.getAttribute('data-mode') === mode);
    });
    renderProblem();
    if (mode === 'edit') { problemEditor.focus(); }
  }
  $('problem-mode').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-mode]');
    if (button) { setProblemMode(button.getAttribute('data-mode')); }
  });
  $('problem-empty').addEventListener('click', function (event) {
    var button = event.target.closest('[data-problem-action]');
    if (!button) { return; }
    if (button.getAttribute('data-problem-action') === 'edit') { setProblemMode('edit'); } else { openPicker(); }
  });

  function setTextarea(key, element, text) {
    element.value = text;
    docs[key].localChange();
    if (key === 'problem') { renderProblem(); }
  }

  // ---- problem bank picker ---------------------------------------------------------------------------------------------

  var DIFFICULTY = { easy: '简单', medium: '中等', hard: '困难' };
  var pickerTimer = null;

  function openPicker() {
    CT.openModal('picker-modal');
    loadPicker();
  }
  $('pick-problem').addEventListener('click', openPicker);
  $('picker-search').addEventListener('input', function () {
    clearTimeout(pickerTimer);
    pickerTimer = setTimeout(loadPicker, 250);
  });
  $('picker-difficulty').addEventListener('change', loadPicker);

  function loadPicker() {
    var list = $('picker-list');
    var query = '?q=' + encodeURIComponent($('picker-search').value.trim()) +
      '&difficulty=' + encodeURIComponent($('picker-difficulty').value);
    CT.api('/api/problems/' + query).then(function (data) {
      list.textContent = '';
      if (!data.success) {
        list.appendChild(CT.el('p', { 'class': 'list-empty', text: data.error || '题库加载失败' }));
        return;
      }
      if (!data.problems.length) {
        list.appendChild(CT.el('p', { 'class': 'list-empty', text: '没有找到题目' }));
        return;
      }
      data.problems.forEach(function (problem) {
        var row = CT.el('button', { type: 'button', 'class': 'list-row' }, [
          CT.el('span', { 'class': 'badge badge-' + problem.difficulty, text: DIFFICULTY[problem.difficulty] || '' }),
          CT.el('span', { 'class': 'list-title', text: problem.title }),
          CT.el('span', { 'class': 'list-tags', text: problem.tags.slice(0, 3).join(' · ') })
        ]);
        row.addEventListener('click', function () { useProblem(problem.slug); });
        list.appendChild(row);
      });
    });
  }

  function useProblem(slug, quiet) {
    return CT.api('/api/problems/' + encodeURIComponent(slug) + '/').then(function (data) {
      if (!data.success) { CT.toast(data.error || '题目加载失败', 'error'); return; }
      var problem = data.problem;
      var apply = function () {
        setTextarea('problem', problemEditor, problem.markdown);
        setProblemMode('preview');
        var fill = quiet || $('picker-fill').checked;
        if (fill) {
          if (problem.sample_input && !stdinEditor.value.trim()) {
            setTextarea('stdin', stdinEditor, problem.sample_input);
          }
          var starter = problem.starter_code[language];
          if (starter && isUntouched(cm.getValue(), language)) { replaceCode(starter); }
        }
        CT.closeModal('picker-modal');
        showTab('problem');
        if (!quiet) { CT.toast('已把「' + problem.title + '」发到房间', 'ok'); }
      };
      if (problemEditor.value.trim() && !quiet) {
        CT.confirm('用「' + problem.title + '」替换当前的题目？', '替换').then(function (yes) { if (yes) { apply(); } });
      } else {
        apply();
      }
    });
  }

  // ---- the interviewer's own problems ---------------------------------------------------------------------------------------

  function openCustom() {
    $('custom-body').hidden = !config.user.authenticated;
    $('custom-login').hidden = config.user.authenticated;
    CT.openModal('custom-modal');
    if (config.user.authenticated) { loadCustom(); }
  }
  $('my-problems').addEventListener('click', openCustom);

  function loadCustom() {
    var list = $('custom-list');
    CT.api('/custom-problems/').then(function (data) {
      list.textContent = '';
      if (!data.success) {
        list.appendChild(CT.el('p', { 'class': 'list-empty', text: data.error || '加载失败' }));
        return;
      }
      if (!data.problems.length) {
        list.appendChild(CT.el('p', { 'class': 'list-empty', text: '还没有保存过题目' }));
        return;
      }
      data.problems.forEach(function (problem) {
        var remove = CT.el('button', { type: 'button', 'class': 'icon-btn icon-btn-sm', title: '删除', 'aria-label': '删除' },
          [CT.icon('trash')]);
        remove.addEventListener('click', function (event) {
          event.stopPropagation();
          CT.confirm('删除「' + problem.name + '」？', '删除').then(function (yes) {
            if (!yes) { return; }
            CT.api('/custom-problems/' + problem.id + '/delete/', { body: {} }).then(loadCustom);
          });
        });
        var row = CT.el('div', { 'class': 'list-row', role: 'button', tabindex: '0' }, [
          CT.el('span', { 'class': 'list-title', text: problem.name }),
          CT.el('span', { 'class': 'list-tags', text: (languages[problem.language] || { name: problem.language }).name +
            ' · ' + problem.updated_at }),
          remove
        ]);
        row.addEventListener('click', function () { useCustom(problem.id); });
        list.appendChild(row);
      });
    });
  }

  function useCustom(id) {
    CT.api('/custom-problems/' + id + '/load/').then(function (data) {
      if (!data.success) { CT.toast(data.error || '加载失败', 'error'); return; }
      var problem = data.problem;
      var parts = [];
      if (problem.title) { parts.push('# ' + problem.title); }
      if (problem.description) { parts.push(problem.description); }
      if (problem.input_format) { parts.push('## 输入格式\n\n' + problem.input_format); }
      if (problem.output_format) { parts.push('## 输出格式\n\n' + problem.output_format); }
      if (problem.constraints) { parts.push('## 数据范围\n\n' + problem.constraints); }
      var apply = function () {
        setTextarea('problem', problemEditor, parts.join('\n\n') + (parts.length ? '\n' : ''));
        setProblemMode('preview');
        if (languages[problem.language] && problem.language !== language) { setLanguage(problem.language, false); }
        if (problem.code) { replaceCode(problem.code); }
        setTextarea('stdin', stdinEditor, problem.input_text || '');
        CT.closeModal('custom-modal');
        CT.toast('已载入「' + problem.name + '」', 'ok');
      };
      var hasWork = problemEditor.value.trim() || !isUntouched(cm.getValue(), language);
      if (hasWork) {
        CT.confirm('载入「' + problem.name + '」会替换当前的题目和代码，继续吗？', '载入')
          .then(function (yes) { if (yes) { apply(); } });
      } else {
        apply();
      }
    });
  }

  $('custom-save-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var form = this;
    var name = form.elements.name.value.trim();
    if (!name) { return; }
    CT.api('/custom-problems/save/', { body: {
      name: name,
      description: problemEditor.value,
      code: cm.getValue(),
      language: language,
      input_text: stdinEditor.value
    } }).then(function (data) {
      if (data.success) {
        CT.toast(data.message || '已保存', 'ok');
        form.reset();
        loadCustom();
      } else {
        CT.toast(data.error || '保存失败', 'error');
      }
    });
  });

  // ---- pin (members keep a room for good) --------------------------------------------------------------------------------------

  var pinButton = $('pin-button');
  function renderPin() {
    pinButton.hidden = !config.user.authenticated && !isVip;
    pinButton.classList.toggle('is-on', isVip);
    pinButton.title = isVip ? '这个房间已长期保存' : '会员功能：长期保存这个房间';
  }
  pinButton.addEventListener('click', function () {
    if (isVip) { CT.toast('这个房间已经长期保存了', 'info'); return; }
    CT.confirm('长期保存这个房间？（包年会员不限次数，否则消耗 1 次房间额度）', '保存').then(function (yes) {
      if (!yes) { return; }
      CT.api('/api/rooms/' + config.room + '/pin/', { body: {} }).then(function (data) {
        if (data.success) {
          isVip = true;
          renderPin();
          CT.toast('房间已长期保存' + (data.membership ? '（会员状态：' + data.membership + '）' : ''), 'ok');
        } else if (data.need_membership) {
          CT.toast(data.error, 'warn', { duration: 8000, action: '查看会员', onAction: function () {
            window.open('/membership/', '_blank');
          } });
        } else {
          CT.toast(data.error || '保存失败', 'error');
        }
      });
    });
  });

  // ---- layout: splitters and mobile tabs ----------------------------------------------------------------------------------------

  var main = $('room-main');

  function showTab(tab) {
    main.setAttribute('data-tab', tab);
    $('room-tabs').querySelectorAll('button').forEach(function (button) {
      button.classList.toggle('is-active', button.getAttribute('data-tab') === tab);
    });
    if (tab === 'code') { cm.refresh(); }
  }
  $('room-tabs').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-tab]');
    if (button) { showTab(button.getAttribute('data-tab')); }
  });

  function makeSplitter(handle, container, property, storageKey, horizontal, minimum, maximum) {
    var stored = parseFloat(CT.store.get(storageKey, ''));
    if (stored >= minimum && stored <= maximum) { container.style.setProperty(property, stored + '%'); }
    function update(clientX, clientY) {
      var box = container.getBoundingClientRect();
      var ratio = horizontal
        ? (box.bottom - clientY) / box.height * 100
        : (clientX - box.left) / box.width * 100;
      ratio = Math.min(maximum, Math.max(minimum, ratio));
      container.style.setProperty(property, ratio + '%');
      CT.store.set(storageKey, ratio.toFixed(1));
      cm.refresh();
    }
    handle.addEventListener('pointerdown', function (event) {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add(horizontal ? 'is-resizing-h' : 'is-resizing-v');
      var move = function (moveEvent) { update(moveEvent.clientX, moveEvent.clientY); };
      var stop = function () {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', stop);
        handle.removeEventListener('pointercancel', stop);
        document.body.classList.remove('is-resizing-h', 'is-resizing-v');
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', stop);
      handle.addEventListener('pointercancel', stop);
    });
    handle.addEventListener('keydown', function (event) {
      var current = parseFloat(getComputedStyle(container).getPropertyValue(property)) || (horizontal ? 32 : 36);
      var step = 0;
      if (horizontal) {
        step = event.key === 'ArrowUp' ? 2 : (event.key === 'ArrowDown' ? -2 : 0);
      } else {
        step = event.key === 'ArrowRight' ? 2 : (event.key === 'ArrowLeft' ? -2 : 0);
      }
      if (!step) { return; }
      event.preventDefault();
      var next = Math.min(maximum, Math.max(minimum, current + step));
      container.style.setProperty(property, next + '%');
      CT.store.set(storageKey, next.toFixed(1));
      cm.refresh();
    });
  }
  makeSplitter($('split-main'), main, '--left', 'ct:split-left', false, 18, 60);
  makeSplitter($('split-work'), $('work'), '--bottom', 'ct:split-bottom', true, 14, 70);
  window.addEventListener('resize', function () { cm.refresh(); });

  document.addEventListener('ct:theme', function () { cm.refresh(); });

  // After signing in from the room, keep the page (and the unsaved state) and
  // just reconnect so that the server knows who we are.
  CT.auth.onSuccess = function (data) {
    config.user.authenticated = true;
    config.user.name = (data.user && data.user.display_name) || '';
    CT.closeModal('auth-modal');
    CT.toast('登录成功', 'ok');
    renderPin();
    document.querySelectorAll('.room-bar [data-auth-open]').forEach(function (button) {
      var link = CT.el('a', { 'class': 'btn btn-ghost btn-sm user-chip', href: '/profile/', target: '_blank', rel: 'noopener' },
        [CT.icon('user'), CT.el('span', { text: (data.user && data.user.username) || '' })]);
      button.parentNode.replaceChild(link, button);
    });
    if (!$('custom-modal').hidden) { openCustom(); }
    if (socket) { socket.close(4002); }
  };

  // ---- start -------------------------------------------------------------------------------------------------------------------------

  function afterFirstInit() {
    // /<room>/?problem=<slug>: preload a problem from the bank into an empty room.
    var match = /[?&]problem=([\w-]+)/.exec(location.search);
    if (location.search) { history.replaceState(null, '', location.pathname); }
    if (match && !problemEditor.value.trim()) { useProblem(match[1], true); }
    cm.focus();
  }

  renderProblem();
  renderTimer();
  connect();
}());
