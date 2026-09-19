from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):

    fieldsets = UserAdmin.fieldsets + (
        ("Application Access", {
            "fields": (
                "role",
                "knowledge_partner",
            ),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Application Access", {
            "fields": (
                "role",
                "knowledge_partner",
            ),
        }),
    )

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "knowledge_partner",
        "is_active",
    )

    list_filter = (
        "role",
        "is_active",
    )

    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
    )

    class Media:
        js = ("admin/user_role.js",)