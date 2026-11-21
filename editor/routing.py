from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path('ws/room/<str:room_name>/', consumers.RoomConsumer.as_asgi()),
    # Endpoint of the pre-upgrade pages, kept only to tell them to reload.
    path('ws/editor/<str:room_name>/', consumers.LegacyEditorConsumer.as_asgi()),
]
