"""Root URL configuration for the MedTech Mitra Dashboard project."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/", include("core.api_urls")),
    path("", include("dashboard.urls")),
    path("favicon.ico", RedirectView.as_view(url="/static/favicon.ico", permanent=True)),
]
