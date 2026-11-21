/*
 * Shared page helpers: fetch with CSRF, toasts, modals, theme, sign-in dialog.
 * Everything lives under the CodeTool namespace.
 */
(function () {
  'use strict';
  var CodeTool = window.CodeTool = window.CodeTool || {};

  // ---- small utilities ---------------------------------------------------

  CodeTool.cookie = function (name) {
    var parts = document.cookie ? document.cookie.split(';') : [];
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].trim();
      if (part.substring(0, name.length + 1) === name + '=') {
        return decodeURIComponent(part.substring(name.length + 1));
      }
    }
    return null;
  };

  CodeTool.escapeHtml = function (text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

  CodeTool.el = function (tag, attributes, children) {
    var node = document.createElement(tag);
    Object.keys(attributes || {}).forEach(function (key) {
      var value = attributes[key];
      if (value == null || value === false) { return; }
      if (key === 'class') { node.className = value; }
      else if (key === 'text') { node.textContent = value; }
      else if (key.slice(0, 2) === 'on') { node.addEventListener(key.slice(2), value); }
      else { node.setAttribute(key, value === true ? '' : value); }
    });
    (children || []).forEach(function (child) {
      if (child == null) { return; }
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  };

  CodeTool.icon = function (name, extraClass) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon' + (extraClass ? ' ' + extraClass : ''));
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-' + name);
    svg.appendChild(use);
    return svg;
  };

  CodeTool.store = {
    get: function (key, fallback) {
      try {
        var value = localStorage.getItem(key);
        return value === null ? fallback : value;
      } catch (error) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(key, value); } catch (error) { /* private mode */ }
    },
    remove: function (key) {
      try { localStorage.removeItem(key); } catch (error) { /* private mode */ }
    }
  };

  // ---- HTTP ----------------------------------------------------------------

  // Resolves with the parsed JSON body ({success:false, error} on failures).
  CodeTool.api = function (url, options) {
    options = options || {};
    var init = {
      method: options.method || (options.body !== undefined ? 'POST' : 'GET'),
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
    };
    if (init.method !== 'GET') {
      init.headers['X-CSRFToken'] = CodeTool.cookie('csrftoken') || '';
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body === undefined ? {} : options.body);
    }
    return fetch(url, init).then(function (response) {
      return response.text().then(function (text) {
        var data;
        try { data = JSON.parse(text); } catch (error) { data = null; }
        if (data === null || typeof data !== 'object') {
          data = { success: false, error: response.status === 403
            ? '登录状态已过期，请刷新页面后重试' : '服务器开小差了（' + response.status + '），请稍后再试' };
        }
        data.httpStatus = response.status;
        if (!response.ok && data.success === undefined) { data.success = false; }
        return data;
      });
    }).catch(function () {
      return { success: false, error: '网络连接失败，请检查网络后重试', httpStatus: 0 };
    });
  };

  // ---- toasts ----------------------------------------------------------------

  CodeTool.toast = function (message, kind, options) {
    options = options || {};
    var host = document.getElementById('toasts');
    if (!host) { return null; }
    var toast = CodeTool.el('div', { 'class': 'toast toast-' + (kind || 'info'), role: 'status' }, [
      CodeTool.el('span', { 'class': 'toast-text', text: message })
    ]);
    var timer = null;
    function close() {
      if (timer) { clearTimeout(timer); }
      toast.classList.add('is-leaving');
      setTimeout(function () { if (toast.parentNode) { toast.parentNode.removeChild(toast); } }, 200);
    }
    if (options.action) {
      toast.appendChild(CodeTool.el('button', {
        type: 'button', 'class': 'toast-action', text: options.action,
        onclick: function () { close(); options.onAction(); }
      }));
    }
    toast.appendChild(CodeTool.el('button', {
      type: 'button', 'class': 'toast-close', 'aria-label': '关闭', onclick: close
    }, [CodeTool.icon('x')]));
    host.appendChild(toast);
    var duration = options.duration === undefined ? (kind === 'error' ? 6000 : 3200) : options.duration;
    if (duration > 0) { timer = setTimeout(close, duration); }
    return { close: close };
  };

  // ---- modals ----------------------------------------------------------------

  var openModals = [];

  CodeTool.openModal = function (modal) {
    if (typeof modal === 'string') { modal = document.getElementById(modal); }
    if (!modal || !modal.hidden) { return; }
    modal.hidden = false;
    openModals.push(modal);
    document.body.classList.add('has-modal');
    var focusable = modal.querySelector('input:not([type=hidden]):not([disabled]), textarea, select, button.btn');
    if (focusable) { setTimeout(function () { focusable.focus(); }, 30); }
  };

  CodeTool.closeModal = function (modal) {
    if (typeof modal === 'string') { modal = document.getElementById(modal); }
    if (!modal || modal.hidden) { return; }
    modal.hidden = true;
    openModals = openModals.filter(function (item) { return item !== modal; });
    if (!openModals.length) { document.body.classList.remove('has-modal'); }
    modal.dispatchEvent(new CustomEvent('modal:closed'));
  };

  document.addEventListener('click', function (event) {
    var closer = event.target.closest('[data-close]');
    if (closer) {
      CodeTool.closeModal(closer.closest('.modal'));
      return;
    }
    if (event.target.classList && event.target.classList.contains('modal')) {
      CodeTool.closeModal(event.target);
    }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && openModals.length) {
      CodeTool.closeModal(openModals[openModals.length - 1]);
    }
  });

  // Promise<boolean>: a small "are you sure?" dialog.
  CodeTool.confirm = function (message, okLabel) {
    return new Promise(function (resolve) {
      var settled = false;
      function finish(value) {
        if (settled) { return; }
        settled = true;
        if (modal.parentNode) { modal.parentNode.removeChild(modal); }
        openModals = openModals.filter(function (item) { return item !== modal; });
        if (!openModals.length) { document.body.classList.remove('has-modal'); }
        resolve(value);
      }
      var ok = CodeTool.el('button', { type: 'button', 'class': 'btn btn-primary', text: okLabel || '确定',
        onclick: function () { finish(true); } });
      var modal = CodeTool.el('div', { 'class': 'modal' }, [
        CodeTool.el('div', { 'class': 'modal-card modal-narrow', role: 'alertdialog', 'aria-modal': 'true' }, [
          CodeTool.el('p', { 'class': 'confirm-text', text: message }),
          CodeTool.el('div', { 'class': 'modal-actions' }, [
            CodeTool.el('button', { type: 'button', 'class': 'btn btn-ghost', text: '取消',
              onclick: function () { finish(false); } }),
            ok
          ])
        ])
      ]);
      modal.addEventListener('click', function (event) { if (event.target === modal) { finish(false); } });
      modal.addEventListener('modal:closed', function () { finish(false); });
      document.body.appendChild(modal);
      openModals.push(modal);
      document.body.classList.add('has-modal');
      ok.focus();
    });
  };

  // ---- theme -----------------------------------------------------------------

  CodeTool.theme = function () {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  };
  CodeTool.setTheme = function (theme) {
    document.documentElement.setAttribute('data-theme', theme);
    CodeTool.store.set('ct:theme', theme);
    document.dispatchEvent(new CustomEvent('ct:theme', { detail: theme }));
  };
  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-theme-toggle]')) {
      CodeTool.setTheme(CodeTool.theme() === 'dark' ? 'light' : 'dark');
    }
  });

  // ---- clipboard ---------------------------------------------------------------

  CodeTool.copy = function (text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () {
        return legacyCopy(text);
      });
    }
    return Promise.resolve(legacyCopy(text));
  };
  function legacyCopy(text) {
    var area = CodeTool.el('textarea', { 'class': 'offscreen', readonly: true });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (error) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  // ---- sign in / sign up ---------------------------------------------------------

  var auth = CodeTool.auth = {
    // Called after a successful sign in; pages may override it.
    onSuccess: function (data) {
      if (data.next && data.next !== '/') { location.href = data.next; } else { location.reload(); }
    },
    open: function (mode) {
      var modal = document.getElementById('auth-modal');
      if (!modal) { location.href = '/' + (mode === 'signup' ? 'signup' : 'login') + '/'; return; }
      auth.show(modal, mode || 'login');
      CodeTool.openModal(modal);
    },
    show: function (container, mode) {
      container.querySelectorAll('[data-auth-tab]').forEach(function (tab) {
        tab.classList.toggle('is-active', tab.getAttribute('data-auth-tab') === mode);
      });
      container.querySelectorAll('[data-auth-form]').forEach(function (form) {
        form.hidden = form.getAttribute('data-auth-form') !== mode;
      });
    }
  };

  var FIELD_NAMES = { username: '用户名', password: '密码', password1: '密码', password2: '确认密码', email: '邮箱' };

  function describeErrors(data) {
    if (data.errors) {
      var lines = [];
      Object.keys(data.errors).forEach(function (field) {
        (data.errors[field] || []).forEach(function (message) {
          lines.push(FIELD_NAMES[field] && field !== '__all__' ? FIELD_NAMES[field] + '：' + message : message);
        });
      });
      if (lines.length) { return lines.join('\n'); }
    }
    return data.error || '操作失败，请稍后再试';
  }

  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-auth-open]');
    if (opener) {
      event.preventDefault();
      auth.open(opener.getAttribute('data-auth-open'));
      return;
    }
    var tab = event.target.closest('[data-auth-tab]');
    if (tab) {
      auth.show(tab.closest('.modal-card, .auth-card'), tab.getAttribute('data-auth-tab'));
      return;
    }
    if (event.target.closest('[data-logout]')) {
      CodeTool.api('/logout/', { body: {} }).then(function () { location.reload(); });
    }
  });

  document.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-auth-form]');
    if (!form) { return; }
    event.preventDefault();
    var mode = form.getAttribute('data-auth-form');
    var errorBox = form.querySelector('[data-error]');
    var button = form.querySelector('button[type=submit]');
    var body = {};
    Array.prototype.forEach.call(form.elements, function (field) {
      if (field.name) { body[field.name] = field.value; }
    });
    var next = form.getAttribute('data-next') || '';
    errorBox.hidden = true;
    button.disabled = true;
    CodeTool.api('/' + mode + '/' + (next ? '?next=' + encodeURIComponent(next) : ''), { body: body })
      .then(function (data) {
        button.disabled = false;
        if (data.success) {
          auth.onSuccess(data);
        } else {
          errorBox.textContent = describeErrors(data);
          errorBox.hidden = false;
        }
      });
  });

  // ---- site header (mobile menu) ---------------------------------------------------

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-nav-toggle]')) {
      var nav = document.querySelector('[data-nav]');
      if (nav) { nav.classList.toggle('is-open'); }
    }
  });
}());
