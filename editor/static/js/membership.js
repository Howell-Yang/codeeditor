/*
 * Membership page: create an order, show the collection QR code and poll
 * until the server confirms the payment.
 */
(function () {
  'use strict';
  var CT = window.CodeTool;
  var ttl = JSON.parse(document.getElementById('payment-ttl').textContent) || 120;
  var modal = document.getElementById('pay-modal');
  var status = document.getElementById('pay-status');
  var pollTimer = null;
  var deadline = 0;

  function stop() {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  modal.addEventListener('modal:closed', stop);

  function setStatus(kind, text, spinning) {
    status.className = 'chip chip-' + kind;
    status.textContent = '';
    if (spinning) { status.appendChild(CT.el('span', { 'class': 'spinner' })); }
    status.appendChild(document.createTextNode(' ' + text));
  }

  function poll(paymentId) {
    if (Date.now() > deadline) {
      setStatus('warn', '等待超时。如果已经付款，请联系站长处理。');
      return;
    }
    CT.api('/check-payment-status/?paymentId=' + encodeURIComponent(paymentId)).then(function (data) {
      if (modal.hidden) { return; }
      if (data.success) {
        setStatus('ok', '支付成功' + (data.membership ? '：' + data.membership : ''));
        CT.toast('支付成功，会员已生效', 'ok');
        setTimeout(function () { location.reload(); }, 1500);
      } else if (data.status === 'pending') {
        pollTimer = setTimeout(function () { poll(paymentId); }, 4000);
      } else {
        setStatus('bad', data.message || data.error || '支付未完成，请重新下单');
      }
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-buy]');
    if (!button) { return; }
    var type = button.getAttribute('data-buy');
    var body = { type: type };
    if (type === 'count') {
      body.quantity = document.getElementById('count-quantity').value;
    } else {
      body.duration = document.getElementById('yearly-duration').value;
    }
    button.disabled = true;
    CT.api('/get-payment-qr/', { body: body }).then(function (data) {
      button.disabled = false;
      if (data.login_required) {
        CT.toast('请先登录，再购买会员', 'info');
        CT.auth.open('login');
        return;
      }
      if (!data.success) { CT.toast(data.error || '下单失败', 'error'); return; }
      stop();
      document.getElementById('pay-amount').textContent = '¥' + data.amount;
      document.getElementById('pay-qr').src = data.qr_code_url;
      setStatus('wait', '等待支付…', true);
      CT.openModal(modal);
      deadline = Date.now() + (data.expires_in || ttl) * 1000;
      pollTimer = setTimeout(function () { poll(data.payment_id); }, 4000);
    });
  });
}());
