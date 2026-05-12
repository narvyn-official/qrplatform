from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from apps.accounts.models import User, UserProfile, APIKey, AuditLog, MembershipOrder


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["email", "full_name", "role", "plan", "plan_expires_at", "is_active", "is_email_verified", "date_joined"]
    list_filter = ["role", "plan", "is_active", "is_email_verified"]
    search_fields = ["email", "full_name"]
    ordering = ["-date_joined"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        ("Membership", {"fields": ("role", "plan", "plan_started_at", "plan_expires_at")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "is_email_verified", "groups", "user_permissions")}),
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


@admin.register(MembershipOrder)
class MembershipOrderAdmin(admin.ModelAdmin):
    list_display = ["user", "plan_code", "billing_cycle", "status", "amount_paise", "provider_order_id", "paid_at"]
    list_filter = ["status", "plan_code", "billing_cycle", "provider"]
    search_fields = ["user__email", "provider_order_id", "provider_payment_id", "receipt"]
    readonly_fields = [
        "user", "plan_code", "billing_cycle", "status", "amount_paise", "currency",
        "provider", "provider_order_id", "provider_payment_id", "provider_signature",
        "receipt", "membership_started_at", "membership_expires_at", "raw_payload",
        "created_at", "paid_at", "updated_at",
    ]
