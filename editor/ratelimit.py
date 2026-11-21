"""A small in-memory sliding-window rate limiter.

The site runs as a single process (see the note on Channels in settings), so
process memory is the right place for these counters.
"""
import threading
import time
from collections import deque

from django.conf import settings

UNKNOWN_CLIENT = 'unknown'

_lock = threading.Lock()
_events = {}
_last_sweep = [0.0]


def parse_rate(value):
    """'30/60' -> (30, 60.0). An empty or zero rate disables the limit."""
    try:
        count, _, window = str(value).partition('/')
        count = int(count)
        window = float(window or 60)
    except (TypeError, ValueError):
        return 0, 0.0
    if count <= 0 or window <= 0:
        return 0, 0.0
    return count, window


def allow(scope, key, rate=None):
    """Record one event for ``(scope, key)``; False when the limit is exceeded.

    ``rate`` defaults to ``settings.RATE_LIMITS[scope]``.
    """
    if rate is None:
        rate = settings.RATE_LIMITS.get(scope, '')
    limit, window = parse_rate(rate)
    if not limit:
        return True
    if key in (None, '', UNKNOWN_CLIENT):
        # We cannot tell clients apart (no proxy headers and no peer address):
        # one shared counter would lock everybody out together, so skip it.
        return True
    now = time.monotonic()
    bucket_key = (scope, str(key))
    with _lock:
        if now - _last_sweep[0] > 300:
            _sweep(now)
        bucket = _events.get(bucket_key)
        if bucket is None:
            bucket = _events[bucket_key] = deque()
        while bucket and bucket[0][0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append((now, window))
        return True


def _sweep(now):
    """Drop buckets whose events have all expired (called with the lock held)."""
    _last_sweep[0] = now
    for bucket_key in list(_events.keys()):
        bucket = _events[bucket_key]
        while bucket and bucket[0][0] <= now - bucket[0][1]:
            bucket.popleft()
        if not bucket:
            del _events[bucket_key]


def reset():
    with _lock:
        _events.clear()


def client_ip_from_headers(headers, fallback=''):
    """Client address for rate limiting.

    ``headers`` maps lower-case header names to values. The proxy headers are
    only believed when TRUST_PROXY_HEADERS is on (the default, because the
    site is meant to sit behind nginx): ``X-Real-IP`` first, then the last
    address of ``X-Forwarded-For``.
    """
    if getattr(settings, 'TRUST_PROXY_HEADERS', True):
        real_ip = (headers.get('x-real-ip') or '').strip()
        if real_ip:
            return real_ip[:64]
        forwarded = headers.get('x-forwarded-for') or ''
        if forwarded:
            # The last entry is the one our own proxy appended; anything in
            # front of it was supplied by the client and cannot be trusted.
            last = forwarded.split(',')[-1].strip()
            if last:
                return last[:64]
    return (fallback or UNKNOWN_CLIENT)[:64]


def client_ip(request):
    headers = {
        'x-forwarded-for': request.META.get('HTTP_X_FORWARDED_FOR', ''),
        'x-real-ip': request.META.get('HTTP_X_REAL_IP', ''),
    }
    return client_ip_from_headers(headers, request.META.get('REMOTE_ADDR', ''))
