/*
 * Problem page: editor, "run" on custom input, "submit" for judging.
 */
(function () {
  'use strict';
  var CT = window.CodeTool;
  var config = JSON.parse(document.getElementById('problem-config').textContent);
  var $ = function (id) { return document.getElementById(id); };

  // The statement is delivered as plain text (readable without scripts) and
  // rendered as Markdown here.
  document.querySelectorAll('[data-markdown]').forEach(function (element) {
    element.innerHTML = CT.markdown(element.textContent);
  });

  var languages = {};
  config.languages.forEach(function (language) { languages[language.key] = language; });

  var VERDICTS = {
    accepted: ['通过', 'ok'],
    wrong_answer: ['答案错误', 'bad'],
    time_limit_exceeded: ['超时', 'warn'],
    memory_limit_exceeded: ['超内存', 'warn'],
    output_limit_exceeded: ['输出超限', 'warn'],
    runtime_error: ['运行错误', 'bad'],
    compilation_error: ['编译错误', 'warn'],
    system_error: ['系统错误', 'bad'],
    skipped: ['未运行', 'muted'],
    pending: ['等待判题', 'wait'],
    judging: ['判题中', 'wait']
  };
  var RUN_STATUS = {
    ok: ['运行成功', 'ok'],
    compile_error: ['编译失败', 'warn'],
    time_limit: ['运行超时', 'warn'],
    memory_limit: ['内存超限', 'warn'],
    output_limit: ['输出超限', 'warn'],
    runtime_error: ['运行出错', 'bad'],
    internal_error: ['系统错误', 'bad']
  };

  // ---- editor -----------------------------------------------------------------------

  var storedLanguage = CT.store.get('ct:lang', '');
  var language = languages[storedLanguage] ? storedLanguage
    : (languages[config.defaultLanguage] ? config.defaultLanguage : config.languages[0].key);

  function codeKey(key) { return 'ct:code:' + config.slug + ':' + key; }
  function starter(key) {
    return config.starterCode[key] || (languages[key] ? languages[key].template : '');
  }

  var cm = window.CodeMirror($('editor-host'), {
    value: CT.store.get(codeKey(language), null) || starter(language),
    mode: languages[language].mode,
    theme: 'ct',
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    autoCloseBrackets: true,
    matchBrackets: true,
    styleActiveLine: true,
    extraKeys: {
      'Ctrl-Enter': function () { run(); },
      'Cmd-Enter': function () { run(); },
      'Ctrl-/': 'toggleComment',
      'Cmd-/': 'toggleComment',
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
  var saveTimer = null;
  cm.on('change', function () {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { CT.store.set(codeKey(language), cm.getValue()); }, 400);
  });
  document.addEventListener('ct:theme', function () { cm.refresh(); });

  var languageSelect = $('language-select');
  languageSelect.value = language;
  languageSelect.addEventListener('change', function () {
    CT.store.set(codeKey(language), cm.getValue());
    language = this.value;
    CT.store.set('ct:lang', language);
    cm.setOption('mode', languages[language].mode);
    cm.setValue(CT.store.get(codeKey(language), null) || starter(language));
  });

  $('reset-button').addEventListener('click', function () {
    CT.confirm('恢复这道题的初始代码？当前代码会被替换。', '重置').then(function (yes) {
      if (yes) { cm.setValue(starter(language)); }
    });
  });

  // ---- console ------------------------------------------------------------------------

  var customInput = $('custom-input');
  customInput.value = config.samples.length ? config.samples[0].input : '';

  function showConsole(name) {
    $('console-tabs').querySelectorAll('button').forEach(function (button) {
      button.classList.toggle('is-active', button.getAttribute('data-console') === name);
    });
    customInput.hidden = name !== 'input';
    $('console-output').hidden = name !== 'output';
    $('console-verdict').hidden = name !== 'verdict';
    if (name === 'input') { $('console-meta').textContent = ''; }
  }
  $('console-tabs').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-console]');
    if (button) { showConsole(button.getAttribute('data-console')); }
  });
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-use-sample]');
    if (!button) { return; }
    customInput.value = config.samples[+button.getAttribute('data-use-sample')].input;
    showConsole('input');
    CT.toast('已填入自测输入，点「运行」试试', 'info');
  });

  function block(className, label, text) {
    var wrapper = CT.el('div', { 'class': 'out-block ' + className });
    if (label) { wrapper.appendChild(CT.el('div', { 'class': 'out-label', text: label })); }
    wrapper.appendChild(CT.el('pre', { text: text }));
    return wrapper;
  }
  function megabytes(kilobytes) { return ((kilobytes || 0) / 1024).toFixed(1) + ' MB'; }
  function waiting(text) {
    return CT.el('p', { 'class': 'io-placeholder' }, [CT.el('span', { 'class': 'spinner' }), ' ' + text]);
  }

  // ---- run ------------------------------------------------------------------------------

  var busy = false;
  function setBusy(value) {
    busy = value;
    $('run-button').disabled = value;
    $('submit-button').disabled = value;
  }

  function poll(url, isDone, onDone, attempt) {
    attempt = attempt || 0;
    CT.api(url).then(function (data) {
      if (!data.success) { onDone(data); return; }
      if (isDone(data) || attempt > 240) { onDone(data); return; }
      setTimeout(function () { poll(url, isDone, onDone, attempt + 1); }, attempt < 10 ? 300 : 1000);
    });
  }

  function run() {
    if (busy) { return; }
    var output = $('console-output');
    setBusy(true);
    showConsole('output');
    output.textContent = '';
    output.appendChild(waiting('正在运行…'));
    $('console-meta').textContent = '';
    CT.api('/problem/' + config.slug + '/run/', {
      body: { language: language, code: cm.getValue(), input: customInput.value }
    }).then(function (data) {
      if (!data.success) { setBusy(false); showRunError(data.error); return; }
      poll('/api/jobs/' + data.job_id + '/', function (job) { return job.done; }, function (job) {
        setBusy(false);
        if (!job.success || !job.done) { showRunError(job.error || '运行超时，请重试'); return; }
        renderRun(job.result);
      });
    });
  }

  function showRunError(message) {
    var output = $('console-output');
    output.textContent = '';
    output.appendChild(CT.el('p', { 'class': 'out-message', text: message || '运行失败' }));
  }

  function renderRun(result) {
    var output = $('console-output');
    var info = RUN_STATUS[result.status] || ['运行结束', 'muted'];
    output.textContent = '';
    output.appendChild(CT.el('div', { 'class': 'verdict-head' }, [
      CT.el('span', { 'class': 'chip chip-' + info[1], text: info[0] }),
      result.message && result.status !== 'ok' ? CT.el('span', { 'class': 'verdict-stats', text: result.message }) : null
    ]));
    if (result.status === 'compile_error') {
      output.appendChild(block('out-stderr', '编译器输出', result.compile_output || result.stderr || ''));
    } else {
      if (result.stdout) {
        output.appendChild(block('out-stdout', '标准输出', result.stdout));
      } else if (result.status === 'ok') {
        output.appendChild(CT.el('p', { 'class': 'io-placeholder', text: '（程序没有输出任何内容）' }));
      }
      if (result.stderr) { output.appendChild(block('out-stderr', '标准错误', result.stderr)); }
    }
    if (result.truncated) {
      output.appendChild(CT.el('p', { 'class': 'out-note', text: '输出过长，只显示了前面一部分。' }));
    }
    $('console-meta').textContent = (result.status === 'compile_error' || result.status === 'internal_error')
      ? '' : (result.cpu_ms || 0) + ' ms · ' + megabytes(result.memory_kb);
  }

  // ---- submit -----------------------------------------------------------------------------

  function submit() {
    if (busy) { return; }
    if (!config.authenticated) {
      CT.toast('登录后才能提交判题（运行不需要登录）', 'info');
      CT.auth.open('login');
      return;
    }
    var panel = $('console-verdict');
    setBusy(true);
    showConsole('verdict');
    panel.textContent = '';
    panel.appendChild(waiting('已提交，等待判题…'));
    $('console-meta').textContent = '';
    CT.api('/problem/' + config.slug + '/submit/', {
      body: { language: language, code: cm.getValue() }
    }).then(function (data) {
      if (!data.success) {
        setBusy(false);
        panel.textContent = '';
        panel.appendChild(CT.el('p', { 'class': 'out-message', text: data.error || '提交失败' }));
        if (data.login_required) { CT.auth.open('login'); }
        return;
      }
      poll('/submission/' + data.submission_id + '/', function (submission) { return submission.done; },
        function (submission) {
          setBusy(false);
          if (!submission.success) {
            panel.textContent = '';
            panel.appendChild(CT.el('p', { 'class': 'out-message', text: submission.error || '判题失败' }));
            return;
          }
          renderVerdict(submission);
          addHistory(submission);
        });
    });
  }

  function renderVerdict(submission) {
    var panel = $('console-verdict');
    var info = VERDICTS[submission.status] || [submission.status_display || submission.status, 'muted'];
    panel.textContent = '';
    var stats = [];
    if (submission.total_score) { stats.push('得分 ' + submission.score + ' / ' + submission.total_score); }
    if (submission.execution_time !== null && submission.execution_time !== undefined) {
      stats.push('最慢用例 ' + submission.execution_time + ' ms');
    }
    if (submission.memory_used) { stats.push('内存峰值 ' + megabytes(submission.memory_used)); }
    panel.appendChild(CT.el('div', { 'class': 'verdict-head' }, [
      CT.el('span', { 'class': 'verdict-title is-' + info[1], text: info[0] }),
      CT.el('span', { 'class': 'verdict-stats', text: stats.join(' · ') })
    ]));
    if (submission.status === 'compilation_error') {
      panel.appendChild(block('out-stderr', '编译器输出', submission.error_message || ''));
      return;
    }
    if (submission.error_message && submission.status !== 'accepted') {
      panel.appendChild(CT.el('p', { 'class': 'out-message', text: submission.error_message }));
    }
    var results = submission.test_results || [];
    if (!results.length) { return; }
    var chips = CT.el('div', { 'class': 'case-chips' });
    var detail = CT.el('div', { 'class': 'case-detail' });
    function showCase(result, chip) {
      chips.querySelectorAll('.case-chip').forEach(function (other) { other.classList.remove('is-selected'); });
      chip.classList.add('is-selected');
      detail.textContent = '';
      var verdict = VERDICTS[result.verdict] || [result.verdict, 'muted'];
      var line = (result.is_sample ? '示例 ' : '用例 ') + result.index + '：' + verdict[0];
      if (result.time_ms !== null && result.time_ms !== undefined) {
        line += ' · ' + result.time_ms + ' ms · ' + megabytes(result.memory_kb);
      }
      if (result.note) { line += ' · ' + result.note; }
      detail.appendChild(CT.el('div', { 'class': 'out-label', text: line }));
      if (result.hidden) {
        detail.appendChild(CT.el('p', { 'class': 'io-placeholder', text: '这是隐藏用例，不显示输入和输出。' }));
        return;
      }
      [['输入', result.input], ['期望输出', result.expected], ['你的输出', result.actual],
        ['标准错误', result.stderr]].forEach(function (pair) {
        if (pair[1] === undefined || (pair[0] === '标准错误' && !pair[1])) { return; }
        detail.appendChild(CT.el('div', {}, [
          CT.el('div', { 'class': 'out-label', text: pair[0] }),
          CT.el('pre', { text: pair[1] === '' ? '（空）' : pair[1] })
        ]));
      });
    }
    var firstFailed = null;
    results.forEach(function (result) {
      var css = result.verdict === 'skipped' ? 'is-skip' : (result.passed ? 'is-pass' : 'is-fail');
      var chip = CT.el('button', { type: 'button', 'class': 'case-chip ' + css,
        text: (result.passed ? '✓ ' : (result.verdict === 'skipped' ? '– ' : '✗ ')) +
          (result.is_sample ? '示例 ' : '用例 ') + result.index });
      chip.addEventListener('click', function () { showCase(result, chip); });
      chips.appendChild(chip);
      if (!result.passed && firstFailed === null) { firstFailed = [result, chip]; }
    });
    panel.appendChild(chips);
    panel.appendChild(detail);
    if (firstFailed) { showCase(firstFailed[0], firstFailed[1]); }
  }

  function addHistory(submission) {
    var history = $('history');
    var empty = $('history-empty');
    if (empty) { empty.parentNode.removeChild(empty); }
    var info = VERDICTS[submission.status] || [submission.status, 'muted'];
    var row = CT.el('div', { 'class': 'history-row' }, [
      CT.el('span', { 'class': 'verdict verdict-' + info[1], text: info[0] }),
      CT.el('span', { text: (languages[submission.language] || { name: submission.language }).name }),
      CT.el('span', { 'class': 'num', text: (submission.execution_time !== null && submission.execution_time !== undefined
        ? submission.execution_time + ' ms · ' : '') + '刚刚' })
    ]);
    history.insertBefore(row, history.firstChild);
  }

  $('run-button').addEventListener('click', run);
  $('submit-button').addEventListener('click', submit);
  customInput.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); run(); }
  });

  // ---- bookmark ---------------------------------------------------------------------------------

  $('bookmark-button').addEventListener('click', function () {
    var button = this;
    if (!config.authenticated) { CT.auth.open('login'); return; }
    CT.api('/problem/' + config.slug + '/bookmark/', { body: {} }).then(function (data) {
      if (!data.success) { CT.toast(data.error || '操作失败', 'error'); return; }
      button.classList.toggle('is-on', data.bookmarked);
      button.title = data.bookmarked ? '取消收藏' : '收藏';
      CT.toast(data.bookmarked ? '已收藏' : '已取消收藏', 'ok');
    });
  });
}());
