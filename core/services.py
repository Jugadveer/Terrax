"""
Cross-app reads: the landing page numbers and the dashboard feed.

These are the only two places that need data from every app at once, which is
why they live in `core` rather than being duplicated in each one.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from market.models import Holding, LedgerEntry, Offer, Trade
from properties.constants import ListingStatus
from properties.models import Listing

STATS_CACHE_SECONDS = 120


def market_stats() -> dict:
    """
    Headline numbers for the landing page, read from the database.

    Cached briefly because it runs on the busiest page and the numbers do not
    need to be accurate to the second.
    """
    cached = cache.get("market-stats")
    if cached is not None:
        return cached

    public = Listing.objects.public()
    aggregate = public.aggregate(
        count=Count("id"),
        total=Sum("asking_price"),
        average=Avg("asking_price"),
    )
    traded = Trade.objects.aggregate(total=Sum("total"), count=Count("id"))

    stats = {
        "listing_count": aggregate["count"] or 0,
        "total_value": aggregate["total"] or Decimal("0"),
        "average_price": aggregate["average"] or Decimal("0"),
        "city_count": public.exclude(city="").values("city").distinct().count(),
        "fractional_count": public.filter(fractional_enabled=True).count(),
        "traded_value": traded["total"] or Decimal("0"),
        "trade_count": traded["count"] or 0,
    }
    cache.set("market-stats", stats, STATS_CACHE_SECONDS)
    return stats


def featured_listings(limit: int = 5) -> list[Listing]:
    """
    Verified listings with a photograph, newest first.

    Deliberately not random: a landing page that reshuffles on every reload
    makes it impossible to point someone at what you just saw.
    """
    return list(
        Listing.objects.public()
        .with_display_data()
        .filter(images__isnull=False)
        .distinct()
        .order_by("-published_at", "-created_at")[:limit]
    )


def dashboard_feed(user, limit: int = 12) -> list[dict]:
    """
    What changed since the user last looked.

    This is the reason to open the site on a day when you are not buying
    anything: offers moved, prices moved, reviews finished. Each entry carries
    its own icon, tone and link so the template renders one loop.
    """
    entries: list[dict] = []

    for offer in (
        Offer.objects.for_user(user).open().with_context().order_by("-updated_at")[:6]
    ):
        seller_side = offer.listing.owner_id == user.pk
        entries.append(
            {
                "when": offer.updated_at,
                "icon": "handshake",
                "tone": offer.tone,
                "title": (
                    f"Offer on {offer.listing.title}"
                    if seller_side
                    else f"Your offer on {offer.listing.title}"
                ),
                "detail": offer.get_status_display(),
                "amount": offer.amount,
                "url": f"/market/offers/{offer.public_id}/",
            }
        )

    for item in (
        user.watchlist.select_related("listing")
        .prefetch_related("listing__images")
        .order_by("-created_at")[:6]
    ):
        listing = item.listing
        if not (item.price_at_add and listing.asking_price):
            continue
        change = (listing.asking_price - item.price_at_add) / item.price_at_add * 100
        if abs(change) < Decimal("0.5"):
            continue
        entries.append(
            {
                "when": listing.updated_at,
                "icon": "trend-down" if change < 0 else "trend-up",
                "tone": "gain" if change < 0 else "caution",
                "title": f"{listing.title} moved {change:+.1f}%",
                "detail": "Since you added it to your watchlist",
                "amount": listing.asking_price,
                "url": listing.get_absolute_url(),
            }
        )

    for listing in Listing.objects.owned_by(user).exclude(
        status=ListingStatus.DRAFT
    ).order_by("-updated_at")[:6]:
        entries.append(
            {
                "when": listing.updated_at,
                "icon": "house-line",
                "tone": listing.status_tone,
                "title": f"{listing.title} is {listing.get_status_display().lower()}",
                "detail": f"{listing.view_count} views so far",
                "amount": listing.asking_price,
                "url": listing.get_absolute_url(),
            }
        )

    for trade in (
        Trade.objects.filter(buyer=user).select_related("listing").order_by("-created_at")[:4]
    ):
        entries.append(
            {
                "when": trade.created_at,
                "icon": "coins",
                "tone": "accent",
                "title": f"{trade.shares:,} shares in {trade.listing.title}",
                "detail": "Added to your portfolio",
                "amount": trade.total,
                "url": "/market/portfolio/",
            }
        )

    entries.sort(key=lambda entry: entry["when"], reverse=True)
    return entries[:limit]


def dashboard_summary(user) -> dict:
    """The four numbers at the top of the dashboard, all from real rows."""
    holdings = Holding.objects.filter(user=user).select_related("listing")
    portfolio_value = sum((h.current_value for h in holdings), Decimal("0"))
    invested = sum((h.invested for h in holdings), Decimal("0"))

    listings = Listing.objects.owned_by(user)
    live = listings.filter(
        status__in=(ListingStatus.LISTED, ListingStatus.TOKENIZED, ListingStatus.VERIFIED)
    ).count()

    payouts = LedgerEntry.objects.filter(
        user=user, kind=LedgerEntry.Kind.PAYOUT
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    return {
        "portfolio_value": portfolio_value,
        "portfolio_gain": portfolio_value - invested,
        "portfolio_gain_percent": (
            (portfolio_value - invested) / invested * 100 if invested else Decimal("0")
        ),
        "live_listings": live,
        "draft_listings": listings.filter(status=ListingStatus.DRAFT).count(),
        "watching": user.watchlist.count(),
        "payouts": payouts,
        "open_offers": Offer.objects.for_user(user).open().count(),
    }


def market_movers(limit: int = 4) -> list[dict]:
    """
    Listings priced furthest below their own valuation.

    A real signal rather than a decorative one, and a single indexed query,
    because the estimate is denormalised onto the listing.
    """
    from properties.services import search

    listings = search({"sort": "value"}).filter(
        status__in=(ListingStatus.LISTED, ListingStatus.TOKENIZED)
    )[:limit]

    return [
        {"listing": listing, "gap": float(listing.valuation_gap)}
        for listing in listings
        if listing.valuation_gap is not None
    ]


def recent_cities(limit: int = 6) -> list[dict]:
    """Cities with the most listings, for the landing page and the filter menu."""
    return list(
        Listing.objects.public()
        .exclude(city="")
        .values("city")
        .annotate(count=Count("id"), value=Sum("asking_price"))
        .order_by("-count")[:limit]
    )


def stale_drafts_cutoff():
    return timezone.now() - timezone.timedelta(days=30)
