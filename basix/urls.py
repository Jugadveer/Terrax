from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("account/", include("accounts.urls")),
    path("properties/", include("properties.urls")),
    path("market/", include("market.urls")),
    path("intelligence/", include("intelligence.urls")),
    path("chain/", include("chain.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = "core.views.not_found"
handler500 = "core.views.server_error"
