from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("polls/", include("polls.urls")),
    path("admin/", admin.site.urls),
]

if settings.ENABLE_DEBUG_TOOLBAR:
    urlpatterns = [*urlpatterns, path("__debug__/", include("debug_toolbar.urls"))]
