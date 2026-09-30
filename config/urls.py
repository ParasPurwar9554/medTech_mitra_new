"""Root URL configuration for the MedTech Mitra Dashboard project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
    path("accounts/", include("accounts.urls")),
    path("api/", include("core.api_urls")),
    path("", include("dashboard.urls")),
    path("favicon.ico", RedirectView.as_view(url="/static/favicon.ico", permanent=True)),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
