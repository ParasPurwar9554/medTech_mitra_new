from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User,LoginLog


@admin.register(User)
class CustomUserAdmin(UserAdmin):

    fieldsets = UserAdmin.fieldsets + (
        ("Application Access", {
            "fields": (
                "role",
                "knowledge_partner",
                "applicant"
            ),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Application Access", {
            "fields": (
                "role",
                "knowledge_partner",
                "applicant"
            ),
        }),
    )
    autocomplete_fields = ("applicant",)

    list_display = ("username","email","first_name","last_name","role","knowledge_partner","is_active",)

    list_filter = ("role","is_active",)

    search_fields = ("username","email","first_name","last_name",)

    class Media:
        js = ("admin/user_role.js",)

@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "status", "ip_address", "message")
    list_filter = ("status", "created_at")
    search_fields = ("username", "ip_address", "message")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in LoginLog._meta.fields]

    # logs should not be added, edited or deleted from admin
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser        