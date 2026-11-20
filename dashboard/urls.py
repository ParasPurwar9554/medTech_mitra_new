from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("applications/", views.applications, name="applications"),
    path("application/<int:pk>/", views.application_detail, name="application_detail"),
    path("export/", views.export_applications, name="export_applications"),
]
