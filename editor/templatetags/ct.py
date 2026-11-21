"""Template helpers of the editor app."""
import hashlib
import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

_asset_cache = {}


@register.simple_tag
def asset(path):
    """URL of a static file with a content hash appended (cache busting).

    The hash is recomputed when the file changes, so a deploy is picked up
    without ``collectstatic`` and browsers can still cache aggressively.
    """
    url = static(path)
    absolute = finders.find(path)
    if not absolute:
        return url
    try:
        modified = os.path.getmtime(absolute)
    except OSError:
        return url
    cached = _asset_cache.get(absolute)
    if cached is None or cached[0] != modified:
        digest = hashlib.md5()
        with open(absolute, 'rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                digest.update(chunk)
        cached = (modified, digest.hexdigest()[:10])
        _asset_cache[absolute] = cached
    return '%s?v=%s' % (url, cached[1])


@register.simple_tag
def icon(name, extra_class=''):
    """Inline reference to an icon of the SVG sprite (``_icons.html``)."""
    classes = 'icon' + (' ' + extra_class if extra_class else '')
    return format_html(
        '<svg class="{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
        classes, name)


DIFFICULTY_LABELS = {'easy': '简单', 'medium': '中等', 'hard': '困难'}


@register.simple_tag
def difficulty_badge(difficulty):
    label = DIFFICULTY_LABELS.get(difficulty, difficulty)
    return format_html('<span class="badge badge-{}">{}</span>', difficulty, label)


STATUS_CLASSES = {
    'accepted': 'ok',
    'pending': 'wait',
    'judging': 'wait',
    'wrong_answer': 'bad',
    'runtime_error': 'bad',
    'compilation_error': 'warn',
    'time_limit_exceeded': 'warn',
    'memory_limit_exceeded': 'warn',
    'output_limit_exceeded': 'warn',
    'system_error': 'muted',
}


@register.simple_tag
def verdict_badge(submission):
    css = STATUS_CLASSES.get(submission.status, 'muted')
    return format_html('<span class="verdict verdict-{}">{}</span>', css, submission.get_status_display())


@register.filter
def kb_to_mb(value):
    """1536 -> '1.5'. Empty for missing values."""
    if value in (None, ''):
        return ''
    try:
        return '%.1f' % (float(value) / 1024.0)
    except (TypeError, ValueError):
        return ''


@register.simple_tag(takes_context=True)
def query_with(context, **changes):
    """Current query string with some parameters replaced (for pagination links)."""
    request = context.get('request')
    params = request.GET.copy() if request is not None else {}
    for key, value in changes.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode() if hasattr(params, 'urlencode') else ''
    # "?" alone links to the same page without any query (an empty href would
    # keep the current one).
    return mark_safe('?' + encoded.replace('&', '&amp;'))


@register.simple_tag
def debug_enabled():
    return settings.DEBUG
