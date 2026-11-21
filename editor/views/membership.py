"""Membership page and the purchase endpoints."""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .. import payment
from ..models import Membership
from .common import api_login_required, api_view, json_body, json_error, limited


@never_cache
@ensure_csrf_cookie
def membership_page(request):
    membership = None
    if request.user.is_authenticated:
        membership = Membership.objects.filter(user=request.user).first()
    return render(request, 'editor/membership.html', {
        'membership': membership,
        'count_packages': sorted(payment.COUNT_PACKAGES.items()),
        'yearly_packages': sorted(payment.YEARLY_PACKAGES.items()),
        'retention_days': settings.ROOM_RETENTION_DAYS,
        'signup_bonus_days': settings.SIGNUP_BONUS_DAYS,
        'payment_ttl': settings.PAYMENT_ORDER_TTL_SECONDS,
    })


@require_POST
@api_view
@api_login_required
@limited('auth_per_ip')
def get_payment_qr(request):
    """Create an order and return the collection QR code for its amount."""
    data = json_body(request)
    try:
        order = payment.create_order(
            request.user, data.get('type'), quantity=data.get('quantity'), duration=data.get('duration'))
    except payment.PaymentUnavailable as exc:
        return json_error(str(exc), 503)
    except payment.PaymentError as exc:
        return json_error(str(exc))
    return JsonResponse({
        'success': True,
        'payment_id': order.payment_id,
        'qr_code_url': static(payment.qr_static_path(order)),
        'amount': '%.2f' % order.amount,
        'expires_in': settings.PAYMENT_ORDER_TTL_SECONDS,
    })


@never_cache
@require_GET
@api_login_required
def check_payment_status(request):
    """Poll an order. The decision is made from data the server stored itself."""
    payment_id = request.GET.get('paymentId') or request.GET.get('payment_id') or ''
    if not payment_id:
        return json_error('缺少订单号')
    try:
        status = payment.check_order(request.user, payment_id)
    except payment.PaymentError as exc:
        return json_error(str(exc), 404)
    if status == 'paid':
        membership = Membership.objects.filter(user=request.user).first()
        return JsonResponse({'success': True, 'status': 'paid',
                             'membership': membership.get_status() if membership else ''})
    if status == 'pending':
        return JsonResponse({'success': False, 'status': 'pending'})
    return JsonResponse({'success': False, 'status': 'failed', 'message': '订单已过期或支付失败，请重新下单'})


def retired_purchase_endpoint(request):
    """Old URLs of the purchase flow; buying now happens on the membership page."""
    return redirect('membership')
