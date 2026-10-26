from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/",auth_views.LogoutView.as_view(next_page="accounts:login"),name="logout",),
]

# urlpatterns = [
#     path("login/",auth_views.LoginView.as_view(template_name="accounts/login.html"),name="login",),
#     path("logout/",auth_views.LogoutView.as_view(next_page="accounts:login"),name="logout",),
# ]
