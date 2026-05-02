"""
Custom DRF permissions — API key scope checking.
"""
import hashlib
from django.utils import timezone
from rest_framework.permissions import BasePermission
from apps.accounts.models import APIKey


class HasAPIKeyScope(BasePermission):
    """
    Validates requests carrying an X-API-Key header.
    Checks that the key is active and has the required scope.
    """
    required_scope = None

    def has_permission(self, request, view):
        raw_key = request.META.get("HTTP_X_API_KEY", "")
        if not raw_key:
            return False

        prefix = raw_key[:8]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        try:
            api_key = APIKey.objects.get(
                key_prefix=prefix,
                key_hash=key_hash,
                status=APIKey.Status.ACTIVE,
            )
        except APIKey.DoesNotExist:
            return False

        if not api_key.is_active:
            return False

        if self.required_scope and self.required_scope not in api_key.scopes:
            return False

        # Attach user for downstream use
        request.user = api_key.user
        request._api_key = api_key

        # Update last used async
        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())

        return True


class IsOwnerOrAdmin(BasePermission):
    """Object-level permission: owner or admin only."""

    def has_object_permission(self, request, view, obj):
        if request.user.role == "admin":
            return True
        owner = getattr(obj, "user", None)
        return owner == request.user
