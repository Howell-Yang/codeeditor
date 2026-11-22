"""Django settings for the codetool project.

Every deployment-specific value comes from the environment. For convenience a
``.env`` file next to ``manage.py`` is loaded on startup (real environment
variables win). ``.env.example`` documents every variable.

The code base targets Python 3.6+ / Django 3.2 because that is what the
production host runs; keep it that way when editing.
"""
import os
import sys
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------

def _load_env_file(path):
    """Tiny ``.env`` reader: ``KEY=VALUE`` lines, ``#`` comments, optional quotes."""
    try:
        with open(str(path), encoding='utf-8') as handle:
            lines = handle.read().splitlines()
    except (IOError, OSError):
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        if key.startswith('export '):
            key = key[len('export '):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_env_file(os.environ.get('CODETOOL_ENV_FILE') or (BASE_DIR / '.env'))


def env(name, default=''):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_int(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default
    try:
        return int(value)
    except ValueError:
        raise ImproperlyConfigured('%s must be an integer, got %r' % (name, value))


def env_float(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default
    try:
        return float(value)
    except ValueError:
        raise ImproperlyConfigured('%s must be a number, got %r' % (name, value))


def env_list(name, default=''):
    return [item.strip() for item in env(name, default).split(',') if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

DEBUG = env_bool('DJANGO_DEBUG', False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'

SECRET_KEY = env('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = 'insecure-development-key-never-use-in-production'
    else:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY is not set. Copy .env.example to .env and fill it in '
            '(or set DJANGO_DEBUG=1 for local development).'
        )

# "*" keeps the site reachable whatever Host header the reverse proxy forwards.
# Tighten it to your domains once you know the proxy passes the Host header.
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', '*')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'editor',
]

MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'codetool.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'editor.context_processors.site',
            ],
        },
    },
]

WSGI_APPLICATION = 'codetool.wsgi.application'
ASGI_APPLICATION = 'codetool.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': env('DJANGO_DB_PATH') or str(BASE_DIR / 'db.sqlite3'),
        'OPTIONS': {'timeout': 20},
        # Tests judge submissions on background threads. A file behaves like
        # production there; Django's default in-memory test database shares
        # one cache between threads and fails with "table is locked" instead
        # of waiting.
        'TEST': {'NAME': os.path.join(tempfile.gettempdir(), 'codetool-test-%d.sqlite3' % os.getpid())},
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = env('DJANGO_TIME_ZONE', 'Asia/Shanghai')
USE_I18N = True
USE_L10N = False
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
#
# WhiteNoise serves the files straight from the app's ``static`` folders
# (USE_FINDERS), so a deploy never ends up with stale collected copies.
# Templates add a content hash (``?v=...``) to every CSS/JS URL, which is what
# makes the long max-age safe. ``collectstatic`` still works if you prefer to
# let nginx serve ``STATIC_ROOT``.
# --------------------------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = env('DJANGO_STATIC_ROOT') or str(BASE_DIR / 'staticfiles')
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
WHITENOISE_MAX_AGE = 0 if DEBUG else 86400

# --------------------------------------------------------------------------
# Sessions, cookies, security
# --------------------------------------------------------------------------

SESSION_COOKIE_AGE = 14 * 24 * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

COOKIE_SECURE = env_bool('DJANGO_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_HTTPONLY = False  # the page scripts read it to send X-CSRFToken
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')  # Django 3.2: bare hosts

# Set DJANGO_BEHIND_HTTPS_PROXY=1 when the reverse proxy terminates TLS *and*
# sends ``X-Forwarded-Proto``; Django then knows the request was secure.
if env_bool('DJANGO_BEHIND_HTTPS_PROXY', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# --------------------------------------------------------------------------
# Channels
#
# Room state (documents, revision history, presence) lives in the memory of
# the one ASGI process, so run exactly ONE worker. The channel layer is only
# kept for Channels' own machinery.
# --------------------------------------------------------------------------

CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'},
}

# --------------------------------------------------------------------------
# Site identity (footer, SEO). All optional.
# --------------------------------------------------------------------------

SITE_NAME = env('SITE_NAME', 'CodeEditor')
SITE_TAGLINE = env('SITE_TAGLINE', '在线代码面试')
SITE_URL = env('SITE_URL', '').rstrip('/')
SITE_COPYRIGHT = env('SITE_COPYRIGHT', '')
SITE_ICP_NUMBER = env('SITE_ICP_NUMBER', '')        # e.g. 京ICP备XXXXXXXX号-1
SITE_POLICE_NUMBER = env('SITE_POLICE_NUMBER', '')  # e.g. 京公网安备XXXXXXXXXXXXXX号
SITE_POLICE_CODE = env('SITE_POLICE_CODE', '')      # digits used in the beian.mps.gov.cn link
BAIDU_SITE_VERIFICATION = env('BAIDU_SITE_VERIFICATION', '')
BAIDU_UNION_SCRIPT = env_bool('BAIDU_UNION_SCRIPT', False)
SITE_REPO_URL = env('SITE_REPO_URL', '')

# Let's Encrypt HTTP-01 challenge files, if the web server does not serve them.
ACME_CHALLENGE_DIR = env('ACME_CHALLENGE_DIR', '')

# --------------------------------------------------------------------------
# Rooms
# --------------------------------------------------------------------------

ROOM_MAX_PEERS = env_int('ROOM_MAX_PEERS', 12)
ROOM_MAX_DOC_CHARS = env_int('ROOM_MAX_DOC_CHARS', 200000)
ROOM_HISTORY_LENGTH = env_int('ROOM_HISTORY_LENGTH', 2000)
ROOM_SAVE_DELAY_SECONDS = env_float('ROOM_SAVE_DELAY_SECONDS', 1.5)
ROOM_IDLE_EVICT_SECONDS = env_int('ROOM_IDLE_EVICT_SECONDS', 120)
# ``manage.py cleanup_rooms`` deletes rooms that were idle for longer than
# this and were not pinned by a member.
ROOM_RETENTION_DAYS = env_int('ROOM_RETENTION_DAYS', 30)

# --------------------------------------------------------------------------
# Code execution sandbox (see editor/sandbox/ and the README)
# --------------------------------------------------------------------------

SANDBOX = {
    # Where per-run working directories are created ("" = system temp dir).
    'ROOT': env('SANDBOX_ROOT', ''),
    # Maximum number of programs running at the same time.
    'MAX_CONCURRENCY': env_int('SANDBOX_MAX_CONCURRENCY', 2),
    # When the server runs as root every run is switched to an unprivileged
    # uid taken from [UID_BASE, UID_BASE + MAX_CONCURRENCY).
    'UID_BASE': env_int('SANDBOX_UID_BASE', 61000),
    # Leave network access to user programs (only relevant when the server
    # runs as root; otherwise the network cannot be cut off).
    'NETWORK': env_bool('SANDBOX_NETWORK', False),
    # Limits for "run" in a room.
    'RUN_CPU_SECONDS': env_int('SANDBOX_RUN_CPU_SECONDS', 5),
    'RUN_WALL_SECONDS': env_int('SANDBOX_RUN_WALL_SECONDS', 10),
    'RUN_MEMORY_MB': env_int('SANDBOX_RUN_MEMORY_MB', 256),
    'COMPILE_WALL_SECONDS': env_int('SANDBOX_COMPILE_WALL_SECONDS', 15),
    'OUTPUT_KB': env_int('SANDBOX_OUTPUT_KB', 256),
    'MAX_PROCESSES': env_int('SANDBOX_MAX_PROCESSES', 32),
    # Interpreters / compilers. Empty = look them up on PATH.
    'PYTHON': env('SANDBOX_PYTHON', ''),
    'GCC': env('SANDBOX_GCC', ''),
    'GXX': env('SANDBOX_GXX', ''),
    'NODE': env('SANDBOX_NODE', ''),
    'C_STD': env('SANDBOX_C_STD', 'gnu99'),
    'CXX_STD': env('SANDBOX_CXX_STD', 'c++11'),
}
if TESTING:
    # A test run as root must not share uids with a server running on the same
    # host: when a run is over, everything left under its uid is killed.
    SANDBOX['UID_BASE'] += 200

# Requests per window, written as "count/seconds".
RATE_LIMITS = {
    'run_per_ip': env('RATE_RUN_PER_IP', '30/60'),
    'run_per_room': env('RATE_RUN_PER_ROOM', '20/60'),
    'submit_per_user': env('RATE_SUBMIT_PER_USER', '10/60'),
    'auth_per_ip': env('RATE_AUTH_PER_IP', '20/300'),
    'signup_per_ip': env('RATE_SIGNUP_PER_IP', '10/3600'),
}
# Trust X-Forwarded-For / X-Real-IP (set by your reverse proxy) for rate limits.
TRUST_PROXY_HEADERS = env_bool('TRUST_PROXY_HEADERS', True)

# --------------------------------------------------------------------------
# Membership / Alipay (optional; the site works without it)
# --------------------------------------------------------------------------

ALIPAY = {
    'APP_ID': env('ALIPAY_APP_ID', ''),
    'PRIVATE_KEY_FILE': env('ALIPAY_PRIVATE_KEY_FILE', ''),
    'PUBLIC_KEY_FILE': env('ALIPAY_PUBLIC_KEY_FILE', ''),
    'GATEWAY': env('ALIPAY_GATEWAY', 'https://openapi.alipay.com/gateway.do'),
    # Static path prefix of the fixed-amount collection QR codes
    # (``<prefix><amount with two decimals>.png``). They are personal payment
    # codes, so they are not part of the repository.
    'QR_STATIC_PREFIX': env('ALIPAY_QR_STATIC_PREFIX', 'images/alipay_qir_'),
}
PAYMENT_ENABLED = bool(ALIPAY['APP_ID'] and ALIPAY['PRIVATE_KEY_FILE'] and ALIPAY['PUBLIC_KEY_FILE'])
PAYMENT_ORDER_TTL_SECONDS = env_int('PAYMENT_ORDER_TTL_SECONDS', 600)
SIGNUP_BONUS_DAYS = env_int('SIGNUP_BONUS_DAYS', 30)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'root': {'handlers': ['console'], 'level': env('DJANGO_LOG_LEVEL', 'INFO')},
}
