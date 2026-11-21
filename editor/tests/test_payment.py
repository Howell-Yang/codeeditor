"""Membership purchases. Alipay itself is replaced by a fake balance."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from editor import payment, ratelimit
from editor.models import Membership, Order

PASSWORD = 'correct horse battery'


def post_json(client, url, data=None):
    return client.post(url, data=json.dumps(data or {}), content_type='application/json')


class FakeAccount(object):
    def __init__(self, balance='100.00'):
        self.balance = Decimal(balance)

    def __call__(self):
        return self.balance


@override_settings(PAYMENT_ENABLED=True, PAYMENT_ORDER_TTL_SECONDS=120)
class PaymentTests(TestCase):

    def setUp(self):
        ratelimit.reset()
        self.alice = User.objects.create_user('alice', password=PASSWORD)
        self.bob = User.objects.create_user('bob', password=PASSWORD)
        self.account = FakeAccount()
        patcher = mock.patch('editor.payment.query_balance', self.account)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prices_are_fixed_on_the_server(self):
        self.assertEqual(payment.price_for('count', quantity='10'), (Decimal('1.00'), 10, None))
        self.assertEqual(payment.price_for('yearly', duration=1), (Decimal('4.99'), None, 1))
        for arguments in (('count', 0.1), ('count', 7), ('count', None), ('yearly', None, 5),
                          ('gift', 10), ('count', '10.0')):
            with self.assertRaises(payment.PaymentError):
                payment.price_for(*arguments)

    def test_order_remembers_the_balance_it_starts_from(self):
        order = payment.create_order(self.alice, 'count', quantity=20)
        self.assertEqual(order.amount, Decimal('2.00'))
        self.assertEqual(order.balance_before, Decimal('100.00'))
        self.assertEqual(order.status, 'pending')
        self.assertEqual(payment.qr_static_path(order), 'images/alipay_qir_2.00.png')

    def test_a_new_order_replaces_the_buyers_previous_one(self):
        first = payment.create_order(self.alice, 'count', quantity=10)
        second = payment.create_order(self.alice, 'yearly', duration=1)
        first.refresh_from_db()
        self.assertEqual(first.status, 'failed')
        self.assertEqual(second.status, 'pending')

    def test_paid_when_the_balance_grows_and_granted_exactly_once(self):
        Membership.objects.create(user=self.alice, remaining_rooms=0)
        order = payment.create_order(self.alice, 'count', quantity=10)
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'pending')
        self.account.balance += Decimal('0.99')
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'pending')
        self.account.balance += Decimal('0.01')
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'paid')
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'paid')
        self.assertEqual(Membership.objects.get(user=self.alice).remaining_rooms, 10)
        order.refresh_from_db()
        self.assertIsNotNone(order.paid_at)

    def test_yearly_plan_extends_from_the_current_end(self):
        end = timezone.now() + timedelta(days=10)
        Membership.objects.create(user=self.alice, end_date=end)
        order = payment.create_order(self.alice, 'yearly', duration=1)
        self.account.balance += Decimal('4.99')
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'paid')
        self.assertEqual(Membership.objects.get(user=self.alice).end_date, end + timedelta(days=365))

    def test_someone_elses_order_cannot_be_checked(self):
        order = payment.create_order(self.alice, 'count', quantity=10)
        self.account.balance += Decimal('5')
        with self.assertRaises(payment.PaymentError):
            payment.check_order(self.bob, order.payment_id)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    def test_orders_expire(self):
        order = payment.create_order(self.alice, 'count', quantity=10)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(seconds=121))
        self.account.balance += Decimal('5')
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'failed')
        self.assertFalse(Membership.objects.filter(user=self.alice).exists())

    def test_orders_from_before_the_upgrade_are_not_confirmed(self):
        order = Order.create_order_with_retry(
            user=self.alice, purchase_type='count', quantity=10, amount=Decimal('1.00'))
        self.assertIsNone(order.balance_before)
        self.assertEqual(payment.check_order(self.alice, order.payment_id), 'failed')

    # ---- HTTP -------------------------------------------------------------------

    def test_endpoints_need_a_login_and_post(self):
        self.assertEqual(post_json(self.client, '/get-payment-qr/', {'type': 'count', 'quantity': 10}).status_code, 401)
        self.assertEqual(self.client.get('/check-payment-status/?paymentId=x').status_code, 401)
        self.client.login(username='alice', password=PASSWORD)
        self.assertEqual(self.client.get('/get-payment-qr/?type=count&quantity=10').status_code, 405)
        self.assertEqual(Order.objects.count(), 0)

    def test_purchase_flow_over_http(self):
        self.client.login(username='alice', password=PASSWORD)
        created = post_json(self.client, '/get-payment-qr/', {'type': 'count', 'quantity': '10'}).json()
        self.assertTrue(created['success'], created)
        self.assertEqual(created['amount'], '1.00')
        self.assertTrue(created['qr_code_url'].endswith('images/alipay_qir_1.00.png'))
        self.assertNotIn('balance', created)                       # the account balance stays private

        url = '/check-payment-status/?paymentId=%s' % created['payment_id']
        # The status depends only on what is stored with the order; extra
        # request parameters (older pages sent some) are ignored.
        extra = self.client.get(url + '&balance=0').json()
        self.assertEqual(extra['status'], 'pending')
        self.account.balance += Decimal('1.00')
        self.assertTrue(self.client.get(url).json()['success'])
        self.assertEqual(Membership.objects.get(user=self.alice).remaining_rooms, 15)   # 5 default + 10

    def test_invalid_package(self):
        self.client.login(username='alice', password=PASSWORD)
        response = post_json(self.client, '/get-payment-qr/', {'type': 'count', 'quantity': 0.1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)


class PaymentDisabledTests(TestCase):

    @override_settings(PAYMENT_ENABLED=False)
    def test_purchase_is_refused_when_alipay_is_not_configured(self):
        User.objects.create_user('alice', password=PASSWORD)
        self.client.login(username='alice', password=PASSWORD)
        response = post_json(self.client, '/get-payment-qr/', {'type': 'count', 'quantity': 10})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(self.client.get('/membership/'), '还没有开通在线支付')


class MembershipModelTests(TestCase):

    def test_status_and_consumption(self):
        user = User.objects.create_user('alice', password=PASSWORD)
        membership = Membership.objects.create(user=user, remaining_rooms=1)
        self.assertTrue(membership.is_active())
        self.assertEqual(membership.get_status(), '1个房间')
        self.assertTrue(membership.consume_room())
        self.assertEqual(membership.remaining_rooms, 0)
        self.assertFalse(membership.consume_room())
        self.assertFalse(membership.is_active())
        self.assertEqual(membership.get_status(), '已过期')
        membership.end_date = timezone.now() + timedelta(days=3)
        membership.save()
        self.assertTrue(membership.consume_room())                 # free with a time plan
        self.assertEqual(membership.remaining_rooms, 0)
        self.assertIn('到期', membership.get_status())
