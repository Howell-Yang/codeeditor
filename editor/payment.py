"""Membership purchases confirmed by watching the Alipay account balance.

The buyer scans a fixed-amount collection QR code; the server remembers the
account balance at that moment and treats the order as paid once the balance
has grown by the order amount. This is a lightweight scheme for a small site:
it works from the account balance alone and does not attribute a payment to
a payer. For anything bigger, use an API with payment notifications (for
example Alipay's "当面付").

The Alipay SDK is imported lazily so the rest of the site runs without it.
"""
import logging
import os
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Membership, Order

logger = logging.getLogger(__name__)

# quantity of room credits -> price
COUNT_PACKAGES = {10: Decimal('1.00'), 20: Decimal('2.00'), 30: Decimal('3.00'), 40: Decimal('4.00')}
# years -> price
YEARLY_PACKAGES = {1: Decimal('4.99')}


class PaymentError(Exception):
    """Something the buyer should be told about."""


class PaymentUnavailable(PaymentError):
    pass


# --------------------------------------------------------------------------
# Alipay
# --------------------------------------------------------------------------

def _read_key(path):
    if not os.path.isabs(path):
        path = os.path.join(str(settings.BASE_DIR), path)
    with open(path, 'r') as handle:
        return handle.read().strip()


class AlipayBalanceQuery(object):
    def __init__(self):
        from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
        from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
        from alipay.aop.api.domain.AlipayFundTransAuctionBalanceQueryModel import (
            AlipayFundTransAuctionBalanceQueryModel,
        )
        from alipay.aop.api.request.AlipayDataBillBalanceQueryRequest import (
            AlipayDataBillBalanceQueryRequest,
        )

        config = AlipayClientConfig()
        config.server_url = settings.ALIPAY['GATEWAY']
        config.app_id = settings.ALIPAY['APP_ID']
        config.app_private_key = _read_key(settings.ALIPAY['PRIVATE_KEY_FILE'])
        config.alipay_public_key = _read_key(settings.ALIPAY['PUBLIC_KEY_FILE'])
        # No logger for the SDK: it would write every signed request URL
        # (application id and signature included) to the service log.
        self.client = DefaultAlipayClient(alipay_client_config=config)
        self.request = AlipayDataBillBalanceQueryRequest(AlipayFundTransAuctionBalanceQueryModel())

    def query_balance(self):
        """Available balance of the collecting account as a Decimal."""
        import requests

        url = self.client.page_execute(self.request, http_method="GET")
        response = requests.get(url, timeout=10)
        data = response.json()
        # TODO: verify data["sign"] with the Alipay public key.
        amount = data["alipay_data_bill_balance_query_response"]["available_amount"]
        return Decimal(str(amount))


_querier = None
_querier_lock = threading.Lock()


def query_balance():
    """Current account balance; raises PaymentUnavailable when it cannot be read."""
    global _querier
    if not settings.PAYMENT_ENABLED:
        raise PaymentUnavailable('支付功能暂未开启')
    try:
        with _querier_lock:
            if _querier is None:
                _querier = AlipayBalanceQuery()
        return _querier.query_balance()
    except PaymentError:
        raise
    except Exception:
        logger.exception('Alipay balance query failed')
        raise PaymentUnavailable('暂时无法连接支付宝，请稍后再试')


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

def price_for(purchase_type, quantity=None, duration=None):
    """Validated ``(amount, quantity, duration_years)`` for a purchase."""
    try:
        if purchase_type == 'count':
            quantity = int(quantity)
            return COUNT_PACKAGES[quantity], quantity, None
        if purchase_type == 'yearly':
            duration = int(duration)
            return YEARLY_PACKAGES[duration], None, duration
    except (KeyError, TypeError, ValueError):
        pass
    raise PaymentError('不支持的购买选项')


def expire_stale_orders():
    cutoff = timezone.now() - timedelta(seconds=settings.PAYMENT_ORDER_TTL_SECONDS)
    Order.objects.filter(status='pending', created_at__lt=cutoff).update(status='failed')


def create_order(user, purchase_type, quantity=None, duration=None):
    """Create a pending order and remember the balance it starts from."""
    amount, quantity, duration = price_for(purchase_type, quantity, duration)
    balance = query_balance()
    expire_stale_orders()
    # One open order per buyer: a new attempt replaces the previous one.
    Order.objects.filter(user=user, status='pending').update(status='failed')
    return Order.create_order_with_retry(
        user=user,
        purchase_type=purchase_type,
        quantity=quantity,
        duration_years=duration,
        amount=amount,
        status='pending',
        balance_before=balance,
    )


def qr_static_path(order):
    return '%s%.2f.png' % (settings.ALIPAY['QR_STATIC_PREFIX'], order.amount)


def grant_membership(user, purchase_type, quantity=None, duration_years=None):
    membership, _ = Membership.objects.get_or_create(user=user)
    if purchase_type == 'count':
        membership.remaining_rooms = (membership.remaining_rooms or 0) + int(quantity or 0)
    elif purchase_type == 'yearly':
        extension = timedelta(days=365 * int(duration_years or 0))
        now = timezone.now()
        if membership.end_date is None or membership.end_date < now:
            membership.end_date = now + extension
        else:
            membership.end_date += extension
    membership.save()
    return membership


def check_order(user, payment_id):
    """Status of the buyer's order: 'paid', 'pending' or 'failed'.

    Marks the order paid (and grants the membership exactly once) when the
    account balance has grown by the order amount since the order was made.
    """
    try:
        order = Order.objects.get(payment_id=payment_id, user=user)
    except Order.DoesNotExist:
        raise PaymentError('订单不存在')
    if order.status != 'pending':
        return order.status
    age = (timezone.now() - order.created_at).total_seconds()
    if order.balance_before is None or age > settings.PAYMENT_ORDER_TTL_SECONDS:
        Order.objects.filter(pk=order.pk, status='pending').update(status='failed')
        return 'failed'
    try:
        balance = query_balance()
    except (PaymentUnavailable, InvalidOperation):
        return 'pending'
    if balance < order.balance_before + order.amount:
        return 'pending'
    with transaction.atomic():
        # Only the request that flips the status grants the membership.
        updated = Order.objects.filter(pk=order.pk, status='pending').update(
            status='paid', paid_at=timezone.now())
        if updated:
            grant_membership(order.user, order.purchase_type, order.quantity, order.duration_years)
    return 'paid'
