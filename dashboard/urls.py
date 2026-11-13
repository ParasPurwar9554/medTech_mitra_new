from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("applications/", views.applications, name="applications"),
    path("application/<int:pk>/", views.application_detail, name="application_detail"),
    path('ajax/trl-detail/', views.trl_detail_ajax, name='trl_detail_ajax'),
    path("ajax/trl-attachments/download/<int:pk>/", views.download_trl_attachment, name="download_trl_attachment"),
    path("export/", views.export_applications, name="export_applications"),

]
