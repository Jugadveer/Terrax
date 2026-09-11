"""
Listing business rules.

Views call into here and stay thin. Anything that changes a listing's state,
touches more than one model, or has to be correct in a particular order lives
in this module.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import ExpressionWrapper, F, FloatField, Q, QuerySet
from django.db.models.functions import Cast
from django.utils import timezone

from accounts.services import notify
from properties import constants as C
from properties.models import Listing, PricePoint

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(filters: dict, *, base: QuerySet | None = None) -> QuerySet:
    """
    Apply the marketplace filters to the public catalogue.

    Filters arrive already cleaned by `SearchForm`, so this function does not
    validate; it translates. Ordering by `value` needs a computed column, which
    is why the annotation is conditional rather than always applied.
    """
    queryset = base if base is not None else Listing.objects.public()
    queryset = queryset.with_display_data()

    if text := filters.get("q"):
        queryset = queryset.filter(
            Q(title__icontains=text)
            | Q(summary__icontains=text)
            | Q(description__icontains=text)
            | Q(locality__icontains=text)
            | Q(city__icontains=text)
        )

    if city := filters.get("city"):
        queryset = queryset.filter(
            Q(city__icontains=city) | Q(locality__icontains=city)
        )

    if property_type := filters.get("property_type"):
        queryset = queryset.filter(property_type=property_type)

    if bedrooms := filters.get("bedrooms"):
        queryset = queryset.filter(bedrooms__gte=bedrooms)

    if price_min := filters.get("price_min"):
        queryset = queryset.filter(asking_price__gte=price_min)

    if price_max := filters.get("price_max"):
        queryset = queryset.filter(asking_price__lte=price_max)

    if area_min := filters.get("area_min"):
        queryset = queryset.filter(area_sqft__gte=area_min)

    if filters.get("fractional"):
        queryset = queryset.filter(fractional_enabled=True)

    if filters.get("verified_only"):
        queryset = queryset.exclude(status=C.ListingStatus.SUBMITTED)

    return _order(queryset, filters.get("sort") or "recent")


def _order(queryset: QuerySet, sort: str) -> QuerySet:
    if sort == "value":
        # How far below its own valuation a listing is priced. Negative is a
        # discount, so ascending order puts the best value first. The estimate
        # is denormalised onto the listing, which keeps this a single indexed
        # column rather than a join to the newest row of the history table.
        # Both operands are cast to float first. SQLite divides two integer
        # columns with integer division, and decimal prices stored without a
        # fractional part are integers to it, so every row came back as zero
        # and the ordering silently did nothing.
        asking = Cast("asking_price", FloatField())
        estimate = Cast("valuation_estimate", FloatField())

        return (
            queryset.filter(valuation_estimate__gt=0)
            .annotate(
                value_gap=ExpressionWrapper(
                    (asking - estimate) / estimate, output_field=FloatField()
                )
            )
            .order_by("value_gap")
        )

    _, field = C.SORT_OPTIONS.get(sort, C.SORT_OPTIONS["recent"])
    # Drafts have no published_at, so fall back to creation order.
    if field == "-published_at":
        return queryset.order_by(F("published_at").desc(nulls_last=True), "-created_at")
    return queryset.order_by(field)


def similar_to(listing, limit: int = 3) -> list[Listing]:
    """Nearby listings of the same kind, for the bottom of a detail page."""
    return list(
        Listing.objects.public()
        .with_display_data()
        .filter(property_type=listing.property_type)
        .filter(Q(city__iexact=listing.city) | Q(state__iexact=listing.state))
        .exclude(pk=listing.pk)
        .order_by("-published_at")[:limit]
    )


# ---------------------------------------------------------------------------
# Ownership and access
# ---------------------------------------------------------------------------


def owned_listing_or_403(user, public_id) -> Listing:
    """
    Fetch a listing the user owns, or refuse.

    Filtering by owner inside the query is what makes cross-user access
    impossible: there is no code path that loads someone else's row and then
    decides whether to allow it.
    """
    try:
        return Listing.objects.get(public_id=public_id, owner=user)
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise PermissionDenied("That listing does not belong to you.") from exc


def visible_listing_or_404(user, public_id) -> Listing:
    """Public listings for everyone; drafts only for their owner."""
    from django.http import Http404

    try:
        listing = Listing.objects.with_display_data().get(public_id=public_id)
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise Http404 from exc

    if listing.is_public or (user.is_authenticated and listing.owner_id == user.pk):
        return listing
    raise Http404


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start_draft(user) -> Listing:
    """
    Reuse an untouched draft rather than creating another one.

    The previous build created a row every time the wizard opened, which is
    where the empty listings in the old database came from.
    """
    existing = (
        Listing.objects.filter(owner=user, status=C.ListingStatus.DRAFT, title="")
        .order_by("-created_at")
        .first()
    )
    return existing or Listing.objects.create(owner=user, status=C.ListingStatus.DRAFT)


def record_price(listing, price: Decimal, note: str = "") -> None:
    """Append to the price history when the number actually moves."""
    last = listing.price_history.last()
    if last and last.price == price:
        return
    PricePoint.objects.create(listing=listing, price=price, note=note)


@transaction.atomic
def submit_for_review(listing) -> Listing:
    """
    Move a completed draft into the review queue.

    Refuses on an incomplete listing, so the submit button cannot be used to
    skip the wizard. The analysis runs here, once, rather than on every page
    view of the result.
    """
    if not listing.is_ready_to_submit:
        blockers = [name for name, done in listing.readiness().items() if not done]
        raise ValueError("Still to do: " + ", ".join(blockers))

    listing.status = C.ListingStatus.SUBMITTED
    listing.wizard_step = 5
    listing.save(update_fields=["status", "wizard_step", "updated_at"])

    from intelligence import services as intel

    intel.check_listing_documents(listing)
    intel.refresh_valuation(listing)
    intel.refresh_risk(listing)

    notify(
        listing.owner,
        kind="listing",
        title=f"{listing.title} submitted for review",
        body="Documents are being checked. This usually finishes within a day.",
        url=listing.get_absolute_url(),
    )
    return listing


@transaction.atomic
def publish(listing, *, reviewer=None) -> Listing:
    """
    Approve a submitted listing and put it on the market.

    Separated from `submit_for_review` because approval is a different actor's
    decision. In this build the reviewer is an administrator; the demo seeder
    calls it directly.
    """
    listing.status = C.ListingStatus.LISTED
    listing.published_at = listing.published_at or timezone.now()
    listing.save(update_fields=["status", "published_at", "updated_at"])

    if listing.asking_price:
        record_price(listing, listing.asking_price, "Listed")

    # Pinning is optional and never blocks publication. `publish_to_chain`
    # reports whether the evidence actually reached IPFS, so the listing page
    # can say so rather than implying it.
    from chain import services as chain

    result = chain.publish_to_chain(listing)

    notify(
        listing.owner,
        kind="listing",
        title=f"{listing.title} is live",
        body=(
            "Buyers can now find it. Its evidence is pinned to IPFS."
            if result["pinned"]
            else f"Buyers can now find it. IPFS pinning was skipped ({result['reason']})."
        ),
        url=listing.get_absolute_url(),
    )
    _alert_watchers_of_new_match(listing)
    return listing


def unpublish(listing) -> Listing:
    listing.status = C.ListingStatus.VERIFIED
    listing.save(update_fields=["status", "updated_at"])
    return listing


def register_view(listing, request) -> None:
    """
    Count a view once per session, so a refresh does not inflate the number.

    `F()` keeps the increment in the database rather than reading, adding and
    writing back, which would lose concurrent views.
    """
    seen = request.session.setdefault("viewed_listings", [])
    key = str(listing.public_id)
    if key in seen:
        return
    seen.append(key)
    request.session["viewed_listings"] = seen[-60:]
    Listing.objects.filter(pk=listing.pk).update(view_count=F("view_count") + 1)


def _alert_watchers_of_new_match(listing) -> None:
    """Tell anyone whose saved search this listing now satisfies."""
    from accounts.models import SavedSearch

    for saved in SavedSearch.objects.filter(alerts_enabled=True).select_related("user"):
        if saved.user_id == listing.owner_id:
            continue
        if search(saved.query, base=Listing.objects.filter(pk=listing.pk)).exists():
            notify(
                saved.user,
                kind="search",
                title=f"New match for {saved.name}",
                body=f"{listing.title} in {listing.location_label}",
                url=listing.get_absolute_url(),
            )
