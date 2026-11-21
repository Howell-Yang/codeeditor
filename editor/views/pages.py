"""Landing page, interview rooms and a few small site-level endpoints."""
import os
import re
import secrets

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .. import execution, problem_seeds, sandbox
from ..models import Membership, Problem, RoomState
from ..rooms import ROOM_NAME_RE, manager
from .common import api_login_required, api_view, json_error

ROOM_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # no look-alike characters


def new_room_name():
    return ''.join(secrets.choice(ROOM_ALPHABET) for _ in range(8))


@ensure_csrf_cookie
def home(request):
    problems = list(Problem.objects.filter(status='published')[:6])
    return render(request, 'editor/home.html', {
        'languages': sandbox.language_catalog(),
        'sample_problems': problems,
        'problem_count': Problem.objects.filter(status='published').count(),
    })


def new_room(request):
    target = reverse('room', kwargs={'room_name': new_room_name()})
    # /new/?problem=<slug> opens the room with that problem from the bank.
    problem = request.GET.get('problem', '')
    if problem and re.match(r'^[-\w]{1,255}$', problem, re.ASCII):
        target += '?problem=' + problem
    return redirect(target)


def legacy_main(request):
    return redirect('home')


@never_cache
@ensure_csrf_cookie
def room(request, room_name):
    if not ROOM_NAME_RE.match(room_name):
        raise Http404()
    display_name = ''
    if request.user.is_authenticated:
        display_name = request.user.first_name or request.user.get_username()
    return render(request, 'editor/room.html', {
        'room_name': room_name,
        'room_config': {
            'room': room_name,
            'wsPath': '/ws/room/%s/' % room_name,
            'languages': sandbox.language_catalog(),
            # Starter code of the built-in problems, so that switching the
            # language can swap an untouched starter for the right one.
            'starters': problem_seeds.STARTER_CODE,
            'user': {
                'authenticated': request.user.is_authenticated,
                'name': display_name,
            },
            'limits': {
                'cpuSeconds': settings.SANDBOX['RUN_CPU_SECONDS'],
                'memoryMb': settings.SANDBOX['RUN_MEMORY_MB'],
                'maxChars': settings.ROOM_MAX_DOC_CHARS,
            },
        },
    })


@require_POST
@api_view
@api_login_required
def pin_room(request, room_name):
    """A member keeps a room for good (it is never cleaned up)."""
    if not ROOM_NAME_RE.match(room_name):
        return json_error('房间不存在', 404)
    state, _ = RoomState.objects.get_or_create(room_name=room_name)
    if state.is_vip:
        return JsonResponse({'success': True, 'already': True, 'membership': _membership_text(request.user)})
    membership, _ = Membership.objects.get_or_create(user=request.user)
    if not membership.consume_room():
        return json_error('会员已过期或次数已用完', 402, need_membership=True)
    RoomState.objects.filter(pk=state.pk).update(is_vip=True, owner=request.user, updated_at=timezone.now())

    def apply(live_room):
        live_room.is_vip = True

    manager.notify_threadsafe(room_name, {'t': 'state', 'key': 'is_vip', 'value': True}, apply)
    return JsonResponse({'success': True, 'membership': _membership_text(request.user)})


def _membership_text(user):
    membership = Membership.objects.filter(user=user).first()
    return membership.get_status() if membership else ''


@never_cache
@require_GET
def job_result(request, job_id):
    """Poll a "run" started from a problem page."""
    owner = _job_owner(request)
    job = execution.get_job(job_id, owner)
    if job is None:
        return json_error('任务不存在或已过期', 404)
    return JsonResponse({'success': True, 'done': job['done'], 'result': job['result']})


def _job_owner(request):
    if request.user.is_authenticated:
        return 'user:%d' % request.user.pk
    if not request.session.session_key:
        request.session.save()
    return 'session:%s' % request.session.session_key


job_owner = _job_owner


@csrf_exempt
def legacy_execute(request, room_name=None):
    """Endpoint of the pre-upgrade pages (cached in browsers): ask for a reload."""
    return JsonResponse({'output': '网站已升级，请刷新页面（Ctrl+F5 / Cmd+Shift+R）后再运行。'})


@require_GET
def robots_txt(request):
    lines = [
        'User-agent: *',
        'Disallow: /admin/',
        'Disallow: /new/',
        'Disallow: /profile/',
        'Disallow: /my-submissions/',
        'Disallow: /my-bookmarks/',
        'Allow: /',
    ]
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')


def acme_challenge(request, token):
    """Serve Let's Encrypt HTTP-01 challenge files from ACME_CHALLENGE_DIR."""
    directory = settings.ACME_CHALLENGE_DIR
    if not directory or not token or token.startswith('.') or '/' in token or '\\' in token:
        raise Http404()
    path = os.path.join(directory, token)
    if not os.path.isfile(path):
        raise Http404()
    with open(path, 'r') as handle:
        return HttpResponse(handle.read(), content_type='text/plain')


def not_found(request, exception=None):
    if request.path.startswith('/api/') or 'application/json' in request.META.get('HTTP_ACCEPT', ''):
        return json_error('没有找到这个地址', 404)
    return render(request, 'editor/error.html', {
        'error_code': 404,
        'error_title': '页面不存在',
        'error_text': '链接可能写错了，或者这个页面已经被移走。',
    }, status=404)


def server_error(request):
    try:
        return render(request, 'editor/error.html', {
            'error_code': 500,
            'error_title': '服务器出错了',
            'error_text': '我们已经记录了这个问题，请稍后再试。',
        }, status=500)
    except Exception:
        return HttpResponseServerError('服务器出错了，请稍后再试。', content_type='text/plain; charset=utf-8')


@never_cache
def healthz(request):
    payload = {'ok': True}
    if request.user.is_staff:
        payload.update(manager.stats())
    return JsonResponse(payload)
