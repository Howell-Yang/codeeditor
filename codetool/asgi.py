"""ASGI entry point: HTTP through Django, WebSockets through Channels.

Run it with exactly one worker process, for example

    gunicorn codetool.asgi:application -k uvicorn.workers.UvicornWorker -w 1
    daphne codetool.asgi:application

because the live room state is kept in process memory.
"""
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'codetool.settings')

from django.core.asgi import get_asgi_application  # noqa: E402

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

import editor.routing  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(editor.routing.websocket_urlpatterns)
        )
    ),
})
