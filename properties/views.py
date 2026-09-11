"""
Marketplace, listing detail, and the listing wizard.

Views resolve the request, delegate to `properties.services`, and render.
Anything that changes state is POST only and scoped to the signed-in owner.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Min
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts import services as accounts
from intelligence import services as intel
from properties import constants as C
from properties import services
from properties.forms import DetailsForm, DocumentForm, MediaForm, PricingForm, SearchForm
from properties.models import Listing, ListingDocument, ListingImage

PAGE_SIZE = 12

#: Wizard step number to (label, form class).
WIZARD_STEPS = {
    1: ("Photographs", MediaForm),
    2: ("The property", DetailsForm),
    3: ("Documents", DocumentForm),
    4: ("Price", PricingForm),
    5: ("Review", None),
}


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """
    The marketplace.

    Filters live in the query string, so a search is a shareable link and the
    back button works. When the request comes from the filter controls, only
    the results fragment is rendered and swapped in, which is why changing a
    filter does not reload the page, the fonts, or the stylesheet.
    """
    form = SearchForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}

    listings = services.search(filters)
    paginator = Paginator(listings, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "form": form,
        "page": page,
        "total": paginator.count,
        "watched": accounts.watched_ids(request.user),
        "cities": _city_options(),
        "sort_options": C.SORT_OPTIONS,
        "active_sort": filters.get("sort") or "recent",
        "view_mode": request.GET.get("view", "grid"),
    }

    if request.headers.get("HX-Request"):
        return render(request, "properties/partials/results.html", context)
    return render(request, "properties/index.html", context)


@require_GET
def detail(request: HttpRequest, public_id) -> HttpResponse:
    listing = services.visible_listing_or_404(request.user, public_id)
    services.register_view(listing, request)

    valuation = intel.latest_valuation(listing)
    history = list(listing.price_history.all())

    return render(
        request,
        "properties/detail.html",
        {
            "listing": listing,
            "valuation": valuation,
            "risk": getattr(listing, "risk", None),
            "documents": listing.documents.all(),
            "price_history": history,
            "price_chart": _price_series(history, listing),
            "range_marks": _range_marks(valuation, listing),
            "similar": services.similar_to(listing),
            "is_watching": accounts.is_watching(request.user, listing),
            "is_owner": request.user.is_authenticated and listing.owner_id == request.user.pk,
            "offers": _visible_offers(request.user, listing),
            "exchanges": listing.assistant_exchanges.order_by("-created_at")[:4],
            "token": getattr(listing, "token", None),
            "pins": listing.pins.all(),
        },
    )


@require_GET
def compare(request: HttpRequest) -> HttpResponse:
    """
    Up to four listings side by side.

    The selection lives in the query string rather than the session, so a
    comparison can be sent to someone else.
    """
    ids = [i for i in request.GET.getlist("id") if i][:4]
    listings = list(Listing.objects.public().with_display_data().filter(public_id__in=ids)) if ids else []
    listings.sort(key=lambda listing: ids.index(str(listing.public_id)))

    rows = _comparison_rows(listings) if listings else []
    return render(
        request,
        "properties/compare.html",
        {"listings": listings, "rows": rows, "slots": range(4 - len(listings))},
    )


@require_GET
def map_view(request: HttpRequest) -> HttpResponse:
    """Everything with coordinates, as GeoJSON the map script reads once."""
    form = SearchForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    listings = services.search(filters).exclude(latitude__isnull=True)[:200]

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(listing.longitude), float(listing.latitude)],
            },
            "properties": {
                "title": listing.title,
                "location": listing.location_label,
                "price": float(listing.asking_price or 0),
                "spec": listing.headline_spec,
                "url": listing.get_absolute_url(),
                "image": listing.cover_image.display_url if listing.cover_image else "",
            },
        }
        for listing in listings
    ]

    return render(
        request,
        "properties/map.html",
        {
            "form": form,
            "geojson": json.dumps({"type": "FeatureCollection", "features": features}),
            "count": len(features),
        },
    )


# ---------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------


@login_required
def wizard(request: HttpRequest, public_id=None, step: int = 1) -> HttpResponse:
    """
    Create or continue a listing.

    One view handles every step. The step's form is the authority on whether the
    step is done, so there is no way to advance past a step that does not
    validate, and no draft row is written before step 1 passes.
    """
    step = max(1, min(int(step), 5))
    listing = services.owned_listing_or_403(request.user, public_id) if public_id else None

    if listing and not listing.is_editable:
        messages.info(request, "This listing has been submitted and can no longer be edited.")
        return redirect(listing.get_absolute_url())

    handler = {
        1: _wizard_media,
        2: _wizard_details,
        3: _wizard_documents,
        4: _wizard_pricing,
        5: _wizard_review,
    }[step]

    result = handler(request, listing)
    if isinstance(result, HttpResponse):
        return result

    form, extra = result
    return render(
        request,
        "properties/wizard.html",
        {
            "form": form,
            "listing": listing,
            "step": step,
            "step_label": WIZARD_STEPS[step][0],
            "steps": WIZARD_STEPS,
            "progress": int(step / len(WIZARD_STEPS) * 100),
            **extra,
        },
    )


def _wizard_media(request, listing):
    if request.method == "POST":
        if listing is None:
            listing = services.start_draft(request.user)
        form = MediaForm(request.POST, request.FILES, listing=listing)
        if form.is_valid():
            form.save()
            listing.wizard_step = max(listing.wizard_step, 2)
            listing.save(update_fields=["wizard_step"])
            return redirect("properties:wizard_step", public_id=listing.public_id, step=2)
    else:
        form = MediaForm(listing=listing)
    return form, {"images": listing.images.all() if listing else []}


def _wizard_details(request, listing):
    if listing is None:
        return redirect("properties:wizard")
    if request.method == "POST":
        form = DetailsForm(request.POST, instance=listing)
        if form.is_valid():
            form.save()
            listing.wizard_step = max(listing.wizard_step, 3)
            listing.save(update_fields=["wizard_step"])
            return redirect("properties:wizard_step", public_id=listing.public_id, step=3)
    else:
        form = DetailsForm(instance=listing)
    return form, {}


def _wizard_documents(request, listing):
    if listing is None:
        return redirect("properties:wizard")
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.listing = listing
            document.save()
            intel.check_document(document)
            messages.success(request, f"{document.get_kind_display()} uploaded and checked.")
            return redirect("properties:wizard_step", public_id=listing.public_id, step=3)
    else:
        form = DocumentForm()
    return form, {
        "documents": listing.documents.all(),
        "missing": listing.missing_documents(),
    }


def _wizard_pricing(request, listing):
    if listing is None:
        return redirect("properties:wizard")
    if request.method == "POST":
        form = PricingForm(request.POST, instance=listing)
        if form.is_valid():
            form.save()
            services.record_price(listing, listing.asking_price, "Asking price set")
            listing.wizard_step = max(listing.wizard_step, 5)
            listing.save(update_fields=["wizard_step"])
            return redirect("properties:wizard_step", public_id=listing.public_id, step=5)
    else:
        form = PricingForm(instance=listing)

    # A provisional valuation, so the seller prices against evidence rather
    # than a hunch. Computed here rather than stored, because the listing is
    # not public yet.
    from intelligence.engine import comparables

    return form, {"guide": comparables.value_listing(listing) if listing.area_sqft else None}


def _wizard_review(request, listing):
    if listing is None:
        return redirect("properties:wizard")
    return None, {
        "readiness": listing.readiness(),
        "ready": listing.is_ready_to_submit,
        "documents": listing.documents.all(),
        "images": listing.images.all(),
    }


@login_required
@require_POST
def submit(request: HttpRequest, public_id) -> HttpResponse:
    listing = services.owned_listing_or_403(request.user, public_id)
    try:
        services.submit_for_review(listing)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("properties:wizard_step", public_id=listing.public_id, step=5)

    messages.success(request, "Submitted. You will be told when the review finishes.")
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def delete(request: HttpRequest, public_id) -> HttpResponse:
    listing = services.owned_listing_or_403(request.user, public_id)
    if not listing.is_editable:
        messages.error(request, "A listing that is live cannot be deleted. Unpublish it first.")
        return redirect(listing.get_absolute_url())
    title = listing.title or "Draft listing"
    listing.delete()
    messages.success(request, f"{title} deleted.")
    return redirect("accounts:my_listings")


@login_required
@require_POST
def delete_image(request: HttpRequest, public_id, image_id: int) -> HttpResponse:
    listing = services.owned_listing_or_403(request.user, public_id)
    get_object_or_404(ListingImage, pk=image_id, listing=listing).delete()
    if not listing.images.filter(is_cover=True).exists():
        first = listing.images.first()
        if first:
            first.is_cover = True
            first.save(update_fields=["is_cover"])
    return redirect("properties:wizard_step", public_id=listing.public_id, step=1)


@login_required
@require_POST
def set_cover(request: HttpRequest, public_id, image_id: int) -> HttpResponse:
    listing = services.owned_listing_or_403(request.user, public_id)
    listing.images.update(is_cover=False)
    ListingImage.objects.filter(pk=image_id, listing=listing).update(is_cover=True)
    return redirect("properties:wizard_step", public_id=listing.public_id, step=1)


@login_required
@require_POST
def delete_document(request: HttpRequest, public_id, document_id: int) -> HttpResponse:
    listing = services.owned_listing_or_403(request.user, public_id)
    get_object_or_404(ListingDocument, pk=document_id, listing=listing).delete()
    return redirect("properties:wizard_step", public_id=listing.public_id, step=3)


# ---------------------------------------------------------------------------
# Actions on a listing
# ---------------------------------------------------------------------------


@login_required
@require_POST
def toggle_watch(request: HttpRequest, public_id) -> HttpResponse:
    """
    Add or remove from the watchlist.

    Answers with the button's new markup so the page swaps one element instead
    of reloading. A non-HTMX caller gets a redirect, so it works without
    JavaScript too.
    """
    listing = get_object_or_404(Listing.objects.public(), public_id=public_id)
    watching = accounts.toggle_watchlist(request.user, listing)

    if request.headers.get("HX-Request"):
        return render(
            request,
            "properties/partials/watch_button.html",
            {"listing": listing, "is_watching": watching},
        )
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def draft_description(request: HttpRequest, public_id) -> JsonResponse:
    """Generate listing copy from the record the seller has already filled in."""
    listing = services.owned_listing_or_403(request.user, public_id)
    text, provider = intel.draft_description(listing)
    return JsonResponse({"text": text, "provider": provider})


@require_POST
def ask(request: HttpRequest, public_id) -> HttpResponse:
    """Answer a question about this listing, grounded on its own record."""
    listing = services.visible_listing_or_404(request.user, public_id)
    question = (request.POST.get("question") or "").strip()
    if not question:
        return render(request, "properties/partials/answer.html", {"error": "Ask a question first."})

    exchange = intel.answer_question(listing, question, user=request.user)
    return render(request, "properties/partials/answer.html", {"exchange": exchange})


@login_required
@require_POST
def revalue(request: HttpRequest, public_id) -> HttpResponse:
    """Recompute the valuation on demand. Owner only, because it costs work."""
    listing = services.owned_listing_or_403(request.user, public_id)
    intel.refresh_valuation(listing)
    intel.refresh_risk(listing)
    messages.success(request, "Valuation refreshed.")
    return redirect(listing.get_absolute_url())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _city_options() -> list[dict]:
    """Cities that actually have listings, with counts, for the filter menu."""
    from django.core.cache import cache

    cached = cache.get("city-options")
    if cached is not None:
        return cached

    rows = list(
        Listing.objects.public()
        .exclude(city="")
        .values("city")
        .annotate(count=Count("id"), cheapest=Min("asking_price"))
        .order_by("-count")[:24]
    )
    cache.set("city-options", rows, 600)
    return rows


def _visible_offers(user, listing) -> list:
    """Owners see every offer. A buyer sees only their own."""
    if not user.is_authenticated:
        return []
    queryset = listing.offers.select_related("buyer", "buyer__profile")
    if listing.owner_id == user.pk:
        return list(queryset.order_by("-created_at")[:10])
    return list(queryset.filter(buyer=user).order_by("-created_at")[:10])


def _price_series(history, listing) -> dict:
    """
    Points for the inline price chart, normalised to 0-100 so the template can
    draw an SVG polyline without any client-side charting library.
    """
    points = [(p.recorded_at, float(p.price)) for p in history]
    if len(points) < 2:
        return {}

    values = [value for _, value in points]
    low, high = min(values), max(values)
    span = (high - low) or 1

    coordinates = [
        (index / (len(points) - 1) * 100, 100 - (value - low) / span * 100)
        for index, (_, value) in enumerate(points)
    ]
    change = (values[-1] - values[0]) / values[0] * 100 if values[0] else 0

    return {
        "polyline": " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates),
        "area": (
            "0,100 " + " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates) + " 100,100"
        ),
        "low": low,
        "high": high,
        "change": round(change, 1),
        "first_date": points[0][0],
        "last_date": points[-1][0],
    }


def _range_marks(valuation, listing) -> dict:
    """
    Where to draw the estimate band and the asking-price marker.

    The axis spans a little either side of the valuation range so that an
    asking price outside the range still lands on the chart rather than off it.
    """
    if not valuation:
        return {}

    low, high = float(valuation.low), float(valuation.high)
    asking = float(listing.asking_price) if listing.asking_price else None

    axis_low = min(low, asking or low) * 0.94
    axis_high = max(high, asking or high) * 1.06
    span = (axis_high - axis_low) or 1

    def position(value: float) -> float:
        return max(0.0, min(100.0, (value - axis_low) / span * 100))

    band_left = position(low)
    return {
        "band_left": round(band_left, 2),
        "band_right": round(100 - position(high), 2),
        "estimate": round(position(float(valuation.estimate)), 2),
        "asking": round(position(asking), 2) if asking is not None else None,
    }


#: The comparison table, as data rather than markup. Adding a row is one line
#: here instead of four cells in the template.
_COMPARE_ROWS = [
    ("Asking price", lambda listing: ("money", listing.asking_price)),
    ("Valuation", lambda listing: ("money", _valuation_of(listing))),
    ("Against valuation", lambda listing: ("gap", _valuation_gap(listing))),
    ("Price per sq ft", lambda listing: ("money", listing.price_per_sqft)),
    ("Built-up area", lambda listing: ("area", listing.area_sqft)),
    ("Carpet area", lambda listing: ("area", listing.carpet_area_sqft)),
    ("Bedrooms", lambda listing: ("text", listing.bedrooms)),
    ("Bathrooms", lambda listing: ("text", listing.bathrooms)),
    ("Year built", lambda listing: ("text", listing.year_built)),
    ("Ownership", lambda listing: ("text", listing.get_ownership_type_display())),
    ("Furnishing", lambda listing: ("text", listing.get_furnishing_display())),
    ("Risk score", lambda listing: ("risk", getattr(listing, "risk", None))),
    ("Documents on file", lambda listing: ("text", listing.documents.count())),
    ("Fractional", lambda listing: ("bool", listing.fractional_enabled)),
]


def _comparison_rows(listings: list[Listing]) -> list[dict]:
    """
    Build the table. Empty values are resolved to a ``blank`` cell here, so the
    template never has to test for None, which Django templates cannot do
    cleanly anyway.
    """
    rows = []
    for label, accessor in _COMPARE_ROWS:
        cells = []
        for listing in listings:
            kind, value = accessor(listing)
            if value is None or value == "":
                cells.append({"kind": "blank", "value": ""})
            else:
                cells.append({"kind": kind, "value": value})
        rows.append({"label": label, "cells": cells})
    return rows


def _valuation_of(listing):
    valuation = listing.valuations.first()
    return valuation.estimate if valuation else None


def _valuation_gap(listing):
    valuation = listing.valuations.first()
    return valuation.gap_to_asking if valuation else None
