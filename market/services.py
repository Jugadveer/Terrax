"""
Trading rules.

Two things happen here: offers on a whole property, and purchases of shares in
a fractional one. Both write an audit trail, and both refuse rather than guess
when the state is wrong.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.services import notify
from market.models import (
    Holding,
    LedgerEntry,
    Offer,
    OfferEvent,
    OfferStatus,
    Trade,
)
from properties.constants import ListingStatus

OFFER_WINDOW = timedelta(days=7)
PLATFORM_FEE_PERCENT = Decimal("0.5")


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


@transaction.atomic
def make_offer(*, listing, buyer, amount: Decimal, message: str = "") -> Offer:
    """Open an offer on a listing that is actually open to them."""
    if listing.owner_id == buyer.pk:
        raise PermissionDenied("You cannot bid on your own listing.")
    if not listing.is_tradeable:
        raise ValueError("This listing is not currently accepting offers.")
    if Offer.objects.filter(listing=listing, buyer=buyer).open().exists():
        raise ValueError("You already have an open offer on this listing. Withdraw it first.")

    offer = Offer.objects.create(
        listing=listing,
        buyer=buyer,
        amount=amount,
        message=message[:400],
        expires_at=timezone.now() + OFFER_WINDOW,
    )
    OfferEvent.objects.create(
        offer=offer, actor=buyer, kind=OfferEvent.Kind.OPENED, amount=amount, body=message[:400]
    )

    from core.formatting import money

    notify(
        listing.owner,
        kind="offer",
        title=f"Offer of {money(amount)} on {listing.title}",
        body=message[:200] or "No message left.",
        url=f"/market/offers/{offer.public_id}/",
    )
    return offer


@transaction.atomic
def counter_offer(*, offer: Offer, actor, amount: Decimal, message: str = "") -> Offer:
    """
    Either side can counter, and the thread keeps going.

    The offer row carries the latest number; the events carry the history, so
    who moved and by how much is always recoverable.
    """
    _require_participant(offer, actor)
    if not offer.is_open:
        raise ValueError("This offer is closed.")

    offer.amount = amount
    offer.status = OfferStatus.COUNTERED
    offer.expires_at = timezone.now() + OFFER_WINDOW
    offer.save(update_fields=["amount", "status", "expires_at", "updated_at"])

    OfferEvent.objects.create(
        offer=offer, actor=actor, kind=OfferEvent.Kind.COUNTER, amount=amount, body=message[:400]
    )

    from core.formatting import money

    other = offer.buyer if actor == offer.listing.owner else offer.listing.owner
    notify(
        other,
        kind="offer",
        title=f"Counter offer of {money(amount)}",
        body=f"On {offer.listing.title}.",
        url=f"/market/offers/{offer.public_id}/",
    )
    return offer


@transaction.atomic
def accept_offer(*, offer: Offer, actor) -> Offer:
    """Seller accepts. The listing goes under offer and every other one closes."""
    if actor != offer.listing.owner:
        raise PermissionDenied("Only the seller can accept an offer.")
    if not offer.is_open:
        raise ValueError("This offer is closed.")

    offer.status = OfferStatus.ACCEPTED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at", "updated_at"])
    OfferEvent.objects.create(
        offer=offer, actor=actor, kind=OfferEvent.Kind.ACCEPTED, amount=offer.amount
    )

    listing = offer.listing
    listing.status = ListingStatus.UNDER_OFFER
    listing.save(update_fields=["status", "updated_at"])

    # Everyone else needs to know their offer is no longer live.
    for other in Offer.objects.filter(listing=listing).open().exclude(pk=offer.pk):
        other.status = OfferStatus.DECLINED
        other.responded_at = timezone.now()
        other.save(update_fields=["status", "responded_at"])
        OfferEvent.objects.create(
            offer=other, kind=OfferEvent.Kind.DECLINED, body="Another offer was accepted."
        )
        notify(
            other.buyer,
            kind="offer",
            title=f"Offer closed on {listing.title}",
            body="The seller accepted a different offer.",
            url=listing.get_absolute_url(),
        )

    from core.formatting import money

    notify(
        offer.buyer,
        kind="offer",
        title=f"Your offer of {money(offer.amount)} was accepted",
        body=f"{listing.title} is now under offer to you.",
        url=f"/market/offers/{offer.public_id}/",
    )
    return offer


@transaction.atomic
def decline_offer(*, offer: Offer, actor, reason: str = "") -> Offer:
    if actor != offer.listing.owner:
        raise PermissionDenied("Only the seller can decline an offer.")
    return _close(offer, actor, OfferStatus.DECLINED, OfferEvent.Kind.DECLINED, reason,
                  offer.buyer, "Your offer was declined")


@transaction.atomic
def withdraw_offer(*, offer: Offer, actor, reason: str = "") -> Offer:
    if actor != offer.buyer:
        raise PermissionDenied("Only the buyer can withdraw an offer.")
    return _close(offer, actor, OfferStatus.WITHDRAWN, OfferEvent.Kind.WITHDRAWN, reason,
                  offer.listing.owner, "An offer was withdrawn")


def _close(offer, actor, status, event_kind, reason, recipient, title) -> Offer:
    if not offer.is_open:
        raise ValueError("This offer is already closed.")
    offer.status = status
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at", "updated_at"])
    OfferEvent.objects.create(offer=offer, actor=actor, kind=event_kind, body=reason[:400])
    notify(
        recipient,
        kind="offer",
        title=f"{title}: {offer.listing.title}",
        body=reason[:200],
        url=f"/market/offers/{offer.public_id}/",
    )
    return offer


def post_message(*, offer: Offer, actor, body: str) -> OfferEvent:
    _require_participant(offer, actor)
    event = OfferEvent.objects.create(
        offer=offer, actor=actor, kind=OfferEvent.Kind.MESSAGE, body=body[:400]
    )
    other = offer.buyer if actor == offer.listing.owner else offer.listing.owner
    notify(
        other,
        kind="offer",
        title=f"New message about {offer.listing.title}",
        body=body[:200],
        url=f"/market/offers/{offer.public_id}/",
    )
    return event


def expire_stale_offers() -> int:
    """Run from a scheduled command. Closes offers nobody answered."""
    stale = Offer.objects.open().filter(expires_at__lte=timezone.now())
    count = 0
    for offer in stale:
        offer.status = OfferStatus.EXPIRED
        offer.save(update_fields=["status"])
        OfferEvent.objects.create(offer=offer, kind=OfferEvent.Kind.EXPIRED)
        count += 1
    return count


def _require_participant(offer: Offer, user) -> None:
    if user.pk not in (offer.buyer_id, offer.listing.owner_id):
        raise PermissionDenied("You are not part of this negotiation.")


# ---------------------------------------------------------------------------
# Fractional shares
# ---------------------------------------------------------------------------


@transaction.atomic
def buy_shares(*, listing, buyer, shares: int) -> Trade:
    """
    Buy from the unsold pool.

    `select_for_update` holds the listing row for the duration, so two people
    buying the last shares at the same moment cannot both succeed.
    """
    from properties.models import Listing

    listing = Listing.objects.select_for_update().get(pk=listing.pk)

    if not listing.fractional_enabled:
        raise ValueError("This property is sold whole, not in shares.")
    if not listing.is_tradeable:
        raise ValueError("This listing is not currently trading.")
    if listing.owner_id == buyer.pk:
        raise PermissionDenied("You already own this property.")
    if shares < 1:
        raise ValueError("Buy at least one share.")
    if shares > listing.shares_available:
        raise ValueError(f"Only {listing.shares_available:,} shares are still available.")

    price = listing.share_price
    total = (price * shares).quantize(Decimal("0.01"))
    fee = (total * PLATFORM_FEE_PERCENT / 100).quantize(Decimal("0.01"))

    trade = Trade.objects.create(
        listing=listing,
        buyer=buyer,
        seller=None,
        kind=Trade.Kind.PRIMARY,
        shares=shares,
        price_per_share=price,
        total=total,
    )

    listing.shares_sold += shares
    listing.save(update_fields=["shares_sold", "updated_at"])

    holding, _ = Holding.objects.get_or_create(user=buyer, listing=listing)
    holding.shares += shares
    holding.invested += total
    holding.save(update_fields=["shares", "invested", "updated_at"])

    LedgerEntry.objects.create(
        user=buyer, kind=LedgerEntry.Kind.BUY, amount=-total, listing=listing,
        trade=trade, description=f"{shares:,} shares of {listing.title}",
    )
    LedgerEntry.objects.create(
        user=buyer, kind=LedgerEntry.Kind.FEE, amount=-fee, listing=listing,
        trade=trade, description=f"Platform fee, {PLATFORM_FEE_PERCENT}%",
    )
    LedgerEntry.objects.create(
        user=listing.owner, kind=LedgerEntry.Kind.PAYOUT, amount=total, listing=listing,
        trade=trade, description=f"{shares:,} shares sold",
    )

    from core.formatting import money

    notify(
        listing.owner,
        kind="portfolio",
        title=f"{shares:,} shares sold in {listing.title}",
        body=f"{money(total)} credited. {listing.shares_available:,} shares remain.",
        url=listing.get_absolute_url(),
    )
    return trade


@transaction.atomic
def sell_shares(*, listing, seller, shares: int) -> Trade:
    """
    Return shares to the pool at the current price.

    A real secondary market needs a counterparty. This build settles against
    the pool: the owner takes the shares back and pays for them, which is the
    mirror image of the primary issue, so every rupee still has two sides.

    The listing is re-read under a row lock rather than trusting the instance
    passed in. A caller that bought shares a moment ago holds a copy whose
    `shares_sold` is already stale, and subtracting from that copy drives the
    pool negative.
    """
    from properties.models import Listing

    listing = Listing.objects.select_for_update().get(pk=listing.pk)

    if shares < 1:
        raise ValueError("Sell at least one share.")

    holding = Holding.objects.select_for_update().filter(user=seller, listing=listing).first()
    if not holding or holding.shares < shares:
        raise ValueError("You do not hold that many shares.")

    price = listing.share_price
    total = (price * shares).quantize(Decimal("0.01"))
    fee = (total * PLATFORM_FEE_PERCENT / 100).quantize(Decimal("0.01"))
    # Cost basis leaves proportionally, so the remaining average is unchanged.
    basis = (holding.invested * shares / holding.shares).quantize(Decimal("0.01"))

    trade = Trade.objects.create(
        listing=listing, buyer=listing.owner, seller=seller,
        kind=Trade.Kind.SECONDARY, shares=shares, price_per_share=price, total=total,
    )

    holding.shares -= shares
    holding.invested -= basis
    if holding.shares == 0:
        holding.delete()
    else:
        holding.save(update_fields=["shares", "invested", "updated_at"])

    listing.shares_sold = max(listing.shares_sold - shares, 0)
    listing.save(update_fields=["shares_sold", "updated_at"])

    LedgerEntry.objects.create(
        user=seller, kind=LedgerEntry.Kind.SELL, amount=total, listing=listing,
        trade=trade, description=f"{shares:,} shares of {listing.title}",
    )
    LedgerEntry.objects.create(
        user=seller, kind=LedgerEntry.Kind.FEE, amount=-fee, listing=listing,
        trade=trade, description=f"Platform fee, {PLATFORM_FEE_PERCENT}%",
    )
    LedgerEntry.objects.create(
        user=listing.owner, kind=LedgerEntry.Kind.BUY, amount=-total, listing=listing,
        trade=trade, description=f"{shares:,} shares bought back",
    )
    return trade


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


def portfolio(user) -> dict:
    """
    Everything the portfolio page shows, in one place.

    Computed rather than stored, because it is small and always has to be
    current. If it grows, the aggregate moves to a nightly snapshot.
    """
    holdings = list(
        Holding.objects.filter(user=user)
        .select_related("listing")
        .prefetch_related("listing__images")
    )

    invested = sum((h.invested for h in holdings), Decimal("0"))
    value = sum((h.current_value for h in holdings), Decimal("0"))
    gain = value - invested
    gain_percent = (gain / invested * 100) if invested else Decimal("0")

    by_city: dict[str, Decimal] = {}
    by_type: dict[str, Decimal] = {}
    for holding in holdings:
        city = holding.listing.city or "Unspecified"
        kind = holding.listing.get_property_type_display() or "Other"
        by_city[city] = by_city.get(city, Decimal("0")) + holding.current_value
        by_type[kind] = by_type.get(kind, Decimal("0")) + holding.current_value

    return {
        "holdings": sorted(holdings, key=lambda h: h.current_value, reverse=True),
        "invested": invested,
        "value": value,
        "gain": gain,
        "gain_percent": gain_percent.quantize(Decimal("0.1")) if invested else Decimal("0"),
        "property_count": len(holdings),
        "allocation_city": _allocation(by_city, value),
        "allocation_type": _allocation(by_type, value),
        "ledger": LedgerEntry.objects.filter(user=user).select_related("listing")[:25],
        "payouts": LedgerEntry.objects.filter(
            user=user, kind=LedgerEntry.Kind.PAYOUT
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
    }


#: Tints of the brand green, so the allocation bar stays inside one accent
#: rather than turning into a pie chart of unrelated colours.
ALLOCATION_COLOURS = [
    "#17492f", "#2f7250", "#4f8d70", "#7fb098", "#b0d0bf", "#d8e9df",
]


def _allocation(buckets: dict[str, Decimal], total: Decimal) -> list[dict]:
    """Largest first, with each bucket's share of the total and its colour."""
    if not total:
        return []
    rows = sorted(
        (
            {"label": label, "value": amount, "percent": float(amount / total * 100)}
            for label, amount in buckets.items()
        ),
        key=lambda row: row["percent"],
        reverse=True,
    )
    for index, row in enumerate(rows):
        row["colour"] = ALLOCATION_COLOURS[index % len(ALLOCATION_COLOURS)]
    return rows


def open_offer_counts(user) -> dict[str, int]:
    """Split by who is waiting on whom, which is what the tab labels show."""
    offers = Offer.objects.for_user(user).open().select_related("listing")
    received = sum(1 for offer in offers if offer.listing.owner_id == user.pk)
    return {"received": received, "made": len(offers) - received, "total": len(offers)}
