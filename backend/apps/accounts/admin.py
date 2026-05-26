from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from apps.accounts.models import (
    User, UserProfile, APIKey, AuditLog, MembershipOrder,
    BusinessVerification, BusinessVerificationAttempt,
)


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


@admin.register(BusinessVerification)
class BusinessVerificationAdmin(admin.ModelAdmin):
    list_display = ["business_name", "domain", "workspace", "status", "method", "verified_at", "updated_at"]
    list_filter = ["status", "method", "verified_at", "created_at"]
    search_fields = ["business_name", "domain", "workspace__email"]
    readonly_fields = ["verification_token", "verified_at", "revoked_at", "created_at", "updated_at"]
    actions = ["revoke_verification"]

    @admin.action(description="Revoke selected verifications")
    def revoke_verification(self, request, queryset):
        queryset.update(
            status=BusinessVerification.Status.REVOKED,
            revoked_at=timezone.now(),
            verified_at=None,
        )


@admin.register(BusinessVerificationAttempt)
class BusinessVerificationAttemptAdmin(admin.ModelAdmin):
    list_display = ["verification", "method", "success", "checked_by", "ip_address", "checked_at"]
    list_filter = ["success", "method", "checked_at"]
    search_fields = ["verification__business_name", "verification__domain", "checked_by__email", "message"]
    readonly_fields = ["verification", "method", "success", "message", "checked_by", "ip_address", "checked_at"]

    def has_add_permission(self, request):
        return False
