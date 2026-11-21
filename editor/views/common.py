"""Helpers shared by the view modules."""
import json
from functools import wraps

from django.http import JsonResponse

from .. import ratelimit


class BadRequest(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def json_body(request, max_bytes=512 * 1024):
    """The request body as a dict (JSON object or form data)."""
    content_type = (request.META.get('CONTENT_TYPE') or '').split(';')[0].strip().lower()
    if content_type == 'application/json' or request.body[:1] in (b'{', b'['):
        if len(request.body) > max_bytes:
            raise BadRequest('请求内容过大', 413)
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise BadRequest('请求格式不正确')
        if not isinstance(data, dict):
            raise BadRequest('请求格式不正确')
        return data
    return request.POST.dict()


def json_error(message, status=400, **extra):
    payload = {'success': False, 'error': message}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def api_view(view):
    """Turn BadRequest into a JSON error response."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except BadRequest as exc:
            return json_error(exc.message, exc.status)
    return wrapper


def api_login_required(view):
    """Like login_required, but answers with JSON 401 instead of a redirect."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return json_error('请先登录', 401, login_required=True)
        return view(request, *args, **kwargs)
    return wrapper


def limited(scope, key_function=None):
    """Rate limit a view; the key defaults to the client address."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            key = key_function(request) if key_function else ratelimit.client_ip(request)
            if not ratelimit.allow(scope, key):
                return json_error('操作太频繁了，请稍后再试', 429)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def text_field(data, name, max_length, default=''):
    value = data.get(name, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise BadRequest('字段 %s 格式不正确' % name)
    if len(value) > max_length:
        raise BadRequest('字段 %s 过长（最多 %d 个字符）' % (name, max_length))
    return value
