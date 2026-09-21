"""Values every template needs, resolved once per request."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

SITE = {
    "name": "Terrax",
    "tagline": "Tokenised property, with the paperwork attached",
    "description": (
        "Terrax turns verified Indian property into tradeable digital shares. "
        "Every listing carries its documents, an independent valuation, and a "
        "risk score you can audit."
    ),
}


def site(request: HttpRequest) -> dict:
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None

    return {
        "site": SITE,
        "profile": profile,
        "unread_notifications": _unread_count(request),
        "currency": getattr(profile, "currency", "INR"),
        "theme_preference": getattr(profile, "theme", "system"),
        "llm_provider": settings.LLM_PROVIDER,
    }


def _unread_count(request: HttpRequest) -> int:
    if not request.user.is_authenticated:
        return 0
    return request.user.notifications.filter(read_at__isnull=True).count()
