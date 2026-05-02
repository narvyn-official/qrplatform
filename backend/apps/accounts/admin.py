from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from apps.accounts.models import User, UserProfile, APIKey, AuditLog


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "full_name", "role", "is_active", "is_email_verified", "date_joined"]
    list_filter = ["role", "is_active", "is_email_verified"]
    search_fields = ["email", "full_name"]
    ordering = ["-date_joined"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        ("Permissions", {"fields": ("role", "is_active", "is_staff", "is_superuser", "is_email_verified", "groups", "user_permissions")}),
        ("2FA", {"fields": ("is_2fa_enabled",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2", "role")}),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "company", "timezone"]
    search_fields = ["user__email", "company"]


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "status", "key_prefix", "last_used_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["name", "user__email"]
    readonly_fields = ["key_prefix", "key_hash"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["action", "user", "ip_address", "created_at"]
    list_filter = ["action"]
    search_fields = ["user__email", "ip_address"]
    readonly_fields = ["user", "action", "ip_address", "user_agent", "metadata", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
