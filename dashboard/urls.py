from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("application/<int:pk>/", views.application_detail, name="application_detail"),
    path("api/chart/<str:chart_name>/", views.chart_data_api, name="chart_data_api"),
    path('ajax/trl-detail/', views.trl_detail_ajax, name='trl_detail_ajax'),
    path('api/trl-drilldown/<int:trl_level>/', views.trl_drilldown, name='trl-drilldown'),
    path("ajax/partners-for-trl/", views.partners_for_trl_ajax, name="partners_for_trl_ajax"),
    path("ajax/assign-trl-partner/", views.assign_trl_partner_ajax, name="assign_trl_partner_ajax"),
    path("ajax/application-milestones/", views.application_milestones_ajax, name="application_milestones_ajax"),
    path("ajax/update-assignment-milestones/", views.update_assignment_milestones, name="update_assignment_milestones"),
    path("ajax/assignment-history/", views.assignment_history_ajax, name="assignment_history_ajax"),
    path("ajax/trl-attachments/", views.trl_attachments, name="trl_attachments"),
    path("ajax/trl-attachments/upload/", views.upload_trl_attachment, name="upload_trl_attachment"),
    path("ajax/trl-attachments/delete/", views.delete_trl_attachment, name="delete_trl_attachment"),
    path("ajax/trl-attachments/download/<int:pk>/", views.download_trl_attachment, name="download_trl_attachment"),

]
