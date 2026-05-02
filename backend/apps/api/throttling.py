"""
Custom throttle classes.
"""
from rest_framework.throttling import UserRateThrottle


class QRGenerationThrottle(UserRateThrottle):
    scope = "qr_generation"


class APIKeyThrottle(UserRateThrottle):
    scope = "api_key"
