"""Sign in, sign up, profile."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from ..forms import SignUpForm, UserProfileForm
from ..models import Membership, Submission
from .common import api_view, json_body, json_error, limited


def _safe_next(request, fallback='/'):
    target = request.GET.get('next') or request.POST.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


def _form_errors(form):
    """{'field': ['message', ...]} with the non-field errors under '__all__'."""
    return dict((field, [str(error) for error in errors]) for field, errors in form.errors.items())


def _first_error(errors):
    for messages in errors.values():
        if messages:
            return messages[0]
    return '请检查填写的内容'


def _user_payload(user):
    membership = Membership.objects.filter(user=user).first()
    return {
        'username': user.get_username(),
        'display_name': user.first_name or user.get_username(),
        'membership_status': ('Active' if membership.is_active() else 'Expired') if membership else 'None',
        'membership_expiry_date': membership.get_status() if membership else '',
        'membership_active': bool(membership and membership.is_active()),
        'remaining_rooms': membership.remaining_rooms if membership else 0,
    }


@never_cache
@ensure_csrf_cookie
def login_view(request):
    if request.method == 'GET':
        if request.user.is_authenticated:
            return redirect(_safe_next(request))
        return render(request, 'editor/auth.html', {'mode': 'login', 'next': _safe_next(request, '')})
    if request.method != 'POST':
        return json_error('只支持 POST 请求', 405)
    return _login_post(request)


@limited('auth_per_ip')
@api_view
def _login_post(request):
    data = json_body(request)
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    errors = {}
    if not username:
        errors['username'] = ['请输入用户名']
    if not password:
        errors['password'] = ['请输入密码']
    user = None
    if not errors:
        user = authenticate(request, username=username, password=password)
        if user is None:
            errors['password'] = ['用户名或密码不正确']
    if errors:
        return JsonResponse({'success': False, 'errors': errors, 'error': _first_error(errors)})
    login(request, user)
    return JsonResponse({'success': True, 'user': _user_payload(user), 'next': _safe_next(request)})


@require_POST
def logout_view(request):
    logout(request)
    return JsonResponse({'success': True})


@never_cache
@ensure_csrf_cookie
def signup(request):
    if request.method == 'GET':
        if request.user.is_authenticated:
            return redirect(_safe_next(request))
        return render(request, 'editor/auth.html', {'mode': 'signup', 'next': _safe_next(request, '')})
    if request.method != 'POST':
        return json_error('只支持 POST 请求', 405)
    return _signup_post(request)


@limited('signup_per_ip')
@api_view
def _signup_post(request):
    form = SignUpForm(json_body(request))
    if not form.is_valid():
        errors = _form_errors(form)
        return JsonResponse({'success': False, 'errors': errors, 'error': _first_error(errors)})
    user = form.save()
    # Welcome gift: a few weeks of membership (plus the default room credits).
    Membership.objects.create(
        user=user, end_date=timezone.now() + timedelta(days=settings.SIGNUP_BONUS_DAYS))
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return JsonResponse({'success': True, 'user': _user_payload(user), 'next': _safe_next(request)})


@never_cache
def get_user_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({'user': None})
    return JsonResponse({'user': _user_payload(request.user)})


@never_cache
@login_required
def user_profile(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            return JsonResponse({'success': True, 'message': '资料已保存'})
        errors = _form_errors(form)
        return JsonResponse({'success': False, 'errors': errors, 'error': _first_error(errors)}, status=400)

    submissions = Submission.objects.filter(user=request.user)
    solved = submissions.filter(status='accepted').values('problem_id').distinct().count()
    membership = Membership.objects.filter(user=request.user).first()
    return render(request, 'editor/profile.html', {
        'form': UserProfileForm(instance=request.user),
        'total_submissions': submissions.count(),
        'accepted_submissions': submissions.filter(status='accepted').count(),
        'solved_problems': solved,
        'recent_submissions': submissions.select_related('problem')[:10],
        'membership': membership,
        'pinned_rooms': request.user.pinned_rooms.order_by('-updated_at')[:20],
    })
