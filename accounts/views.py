import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from .models import LoginLog

logger = logging.getLogger("login")


def get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

def save_login_log(request,username,status,message="",user=None):
    try:
        LoginLog.objects.create(
            user=user,
            username=username[:150],
            status=status,
            ip_address = get_client_ip(request),
            user_agent = request.META.get("HTTP_USER_AGENT", ""),
            message = message
        )

    except Exception:
        logger.exception("could not save LoginLog to database") 

def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    ip = get_client_ip(request)

    if request.method == "POST":
        username = request.POST.get("username", "")
        try:
            form = AuthenticationForm(request, data=request.POST)

            if form.is_valid():
                # login successful
                user = form.get_user()
                login(request, user)
                logger.info(f"Login success | username: {user.get_username()} | IP: {ip}")
                save_login_log(request, user.get_username(), LoginLog.Status.SUCCESS, user=user)

                # go to ?next= page if it is safe, else default page
                next_url = request.POST.get("next") or request.GET.get("next")
                if next_url and url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}
                ):
                    return redirect(next_url)
                return redirect(settings.LOGIN_REDIRECT_URL)

            errors = form.errors.get_json_data()
            error_text = "; ".join(
                e["message"] for field_errors in errors.values() for e in field_errors
            )
            logger.warning(f"Login failed | username: {username} | IP: {ip} | errors: {errors}")
            save_login_log(request, username, LoginLog.Status.FAILED, message=error_text)

        except Exception as e:
            # unexpected crash (database down, code error, etc.)
            logger.exception(f"Login error | username: {username} | IP: {ip}")
            save_login_log(request, username, LoginLog.Status.ERROR, message=str(e))
            raise

    else:
        form = AuthenticationForm(request)

    return render(request, "accounts/login.html", {"form": form,"next": request.GET.get("next", ""),})