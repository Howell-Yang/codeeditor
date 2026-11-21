"""URL configuration of the codetool project."""
from django.contrib import admin
from django.urls import include, path

from editor.views import pages

handler404 = 'editor.views.pages.not_found'
handler500 = 'editor.views.pages.server_error'

urlpatterns = [
    # Must stay first: certificate renewal depends on it.
    path('.well-known/acme-challenge/<str:token>', pages.acme_challenge, name='acme-challenge'),
    path('admin/', admin.site.urls),
    path('', include('editor.urls')),
]
