"""Landing page, dashboard, content pages, and error handlers."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from accounts import services as accounts
from core import services
from market import services as market


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """
    The landing page.

    Every number on it comes from the database. Nothing here is a placeholder,
    so an empty deployment shows an empty market rather than invented figures.
    """
    return render(
        request,
        "core/home.html",
        {
            "stats": services.market_stats(),
            "featured": services.featured_listings(5),
            "cities": services.recent_cities(),
            "movers": services.market_movers(3),
            "watched": accounts.watched_ids(request.user),
        },
    )


@login_required
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """
    What changed since last time.

    Feed first, because that is the reason to come back on a day when you are
    not listing or buying anything.
    """
    profile = request.user.profile
    steps = accounts.onboarding_steps(request.user)

    return render(
        request,
        "core/dashboard.html",
        {
            "summary": services.dashboard_summary(request.user),
            "feed": services.dashboard_feed(request.user),
            "offer_counts": market.open_offer_counts(request.user),
            "onboarding": steps,
            "onboarding_done": all(step["done"] for step in steps),
            "show_onboarding": not profile.onboarding_dismissed
            and not all(step["done"] for step in steps),
            "movers": services.market_movers(3),
        },
    )


@login_required
@require_POST
def dismiss_onboarding(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    profile.onboarding_dismissed = True
    profile.save(update_fields=["onboarding_dismissed"])
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


@require_POST
def set_theme(request: HttpRequest) -> JsonResponse:
    """
    Persist the light/dark choice.

    Stored on the profile when signed in and in the session otherwise, so the
    preference survives a reload either way.
    """
    theme = request.POST.get("theme", "system")
    if theme not in {"system", "light", "dark"}:
        return JsonResponse({"error": "Unknown theme"}, status=400)

    request.session["theme"] = theme
    if request.user.is_authenticated:
        profile = request.user.profile
        profile.theme = theme
        profile.save(update_fields=["theme"])
    return JsonResponse({"theme": theme})


# ---------------------------------------------------------------------------
# Content pages
# ---------------------------------------------------------------------------


@require_GET
def about(request: HttpRequest) -> HttpResponse:
    return render(request, "core/pages/about.html", {"stats": services.market_stats()})


@require_GET
def how_it_works(request: HttpRequest) -> HttpResponse:
    return render(request, "core/pages/how_it_works.html")


@require_GET
def help_centre(request: HttpRequest) -> HttpResponse:
    return render(request, "core/pages/help.html", {"topics": HELP_TOPICS})


@require_GET
def privacy(request: HttpRequest) -> HttpResponse:
    return render(request, "core/pages/privacy.html")


@require_GET
def terms(request: HttpRequest) -> HttpResponse:
    return render(request, "core/pages/terms.html")


def contact(request: HttpRequest) -> HttpResponse:
    """
    Contact form.

    Writes a notification rather than sending mail, because this build has no
    outbound mail configured and a form that silently discards a message is
    worse than no form.
    """
    from core.forms import ContactForm

    form = ContactForm(request.POST or None)
    sent = False

    if request.method == "POST" and form.is_valid():
        form.record(request.user if request.user.is_authenticated else None)
        sent = True
        form = ContactForm()

    return render(request, "core/pages/contact.html", {"form": form, "sent": sent})


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Liveness probe for a deployment target."""
    from django.db import connection

    try:
        connection.ensure_connection()
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        database = f"error: {exc.__class__.__name__}"
    return JsonResponse({"status": "ok", "database": database})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "core/errors/500.html", status=500)


HELP_TOPICS = [
    {
        "title": "Listing a property",
        "items": [
            (
                "What do I need before I start?",
                "Photographs, the built-up area, and three documents: the title "
                "deed, a recent property tax receipt, and a utility bill. You can "
                "save a draft and come back to it.",
            ),
            (
                "How long does review take?",
                "Automated document checks run immediately. A person looks at "
                "anything the checks flag, which usually takes a working day.",
            ),
            (
                "Can I change the price after listing?",
                "Yes. Every change is recorded and shown as a price history on "
                "the listing, so buyers can see the movement.",
            ),
        ],
    },
    {
        "title": "Valuations",
        "items": [
            (
                "Where does the estimate come from?",
                "Comparable listings, weighted by how closely they match on size, "
                "location, configuration and age, then adjusted for the ways this "
                "property differs. The comparables and the adjustments are shown "
                "on every listing.",
            ),
            (
                "Why is confidence sometimes low?",
                "Either there were too few comparable properties nearby, or their "
                "prices were widely scattered. Both widen the range.",
            ),
            (
                "Is a language model deciding the price?",
                "No. The number comes from the comparable calculation. A language "
                "model, when one is configured, only writes the explanation.",
            ),
        ],
    },
    {
        "title": "Fractional ownership",
        "items": [
            (
                "What am I buying?",
                "A share of the listing, recorded against your account. The "
                "listing states how many shares exist and how many are unsold.",
            ),
            (
                "Can I sell shares again?",
                "Yes, back to the pool at the current share price. Your cost "
                "basis leaves proportionally, so your average price is unchanged.",
            ),
        ],
    },
    {
        "title": "Offers",
        "items": [
            (
                "How long does an offer stay open?",
                "Seven days. Either side can counter, which restarts the clock.",
            ),
            (
                "What happens when one is accepted?",
                "The listing moves to under offer and every other open offer on "
                "it is closed, with the buyers told why.",
            ),
        ],
    },
]
