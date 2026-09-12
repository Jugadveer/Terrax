"""Endpoints for the analysis layer that are not tied to a single listing."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from intelligence import services
from intelligence.models import MarketSnapshot
from intelligence.providers import get_provider


@require_POST
def natural_search(request: HttpRequest) -> HttpResponse:
    """
    Take a sentence, turn it into filters, and hand off to the marketplace.

    Redirecting rather than rendering means the result is a normal, shareable,
    bookmarkable marketplace URL with the filters visible in it.
    """
    from urllib.parse import urlencode

    text = (request.POST.get("query") or "").strip()
    filters = services.parse_search(text)
    query = urlencode({k: v for k, v in filters.items() if v not in (None, "")})
    return redirect(f"{reverse('properties:index')}?{query}" if query else "properties:index")


@require_GET
def status(request: HttpRequest) -> HttpResponse:
    """
    What the analysis layer is currently running on.

    Worth showing: the site behaves differently with and without a model
    configured, and a visitor should be able to see which one they are getting.
    """
    provider = get_provider()
    return render(
        request,
        "intelligence/status.html",
        {
            "provider": provider.name,
            "model": provider.model,
            "available": provider.is_available,
            "snapshots": MarketSnapshot.objects.all()[:12],
        },
    )
