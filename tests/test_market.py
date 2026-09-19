"""Offers, negotiation and fractional share trading."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone

from market import services
from market.models import Holding, LedgerEntry, OfferEvent, OfferStatus
from properties.constants import ListingStatus
from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


# --- Offers ---------------------------------------------------------------


def test_an_offer_opens_a_thread(listing, buyer):
    offer = services.make_offer(
        listing=listing, buyer=buyer, amount=Decimal("8500000"), message="Ready to move"
    )

    assert offer.status == OfferStatus.PENDING
    assert offer.events.count() == 1
    assert offer.events.first().kind == OfferEvent.Kind.OPENED


def test_an_owner_cannot_bid_on_their_own_listing(listing, seller):
    with pytest.raises(PermissionDenied):
        services.make_offer(listing=listing, buyer=seller, amount=Decimal("8500000"))


def test_only_one_open_offer_per_buyer(listing, buyer):
    services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))

    with pytest.raises(ValueError, match="already have an open offer"):
        services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8700000"))


def test_a_withdrawn_offer_frees_the_buyer_to_bid_again(listing, buyer):
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))
    services.withdraw_offer(offer=offer, actor=buyer)

    again = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8700000"))

    assert again.status == OfferStatus.PENDING


def test_a_listing_not_on_the_market_refuses_offers(listing, buyer):
    listing.status = ListingStatus.SOLD
    listing.save()

    with pytest.raises(ValueError, match="not currently accepting offers"):
        services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))


def test_a_counter_restarts_the_window(listing, seller, buyer):
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8000000"))
    offer.expires_at = timezone.now() + timedelta(hours=1)
    offer.save()

    services.counter_offer(offer=offer, actor=seller, amount=Decimal("8800000"))
    offer.refresh_from_db()

    assert offer.amount == Decimal("8800000")
    assert offer.status == OfferStatus.COUNTERED
    assert offer.expires_at > timezone.now() + timedelta(days=6)


def test_only_the_seller_can_accept(listing, seller, buyer):
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))

    with pytest.raises(PermissionDenied):
        services.accept_offer(offer=offer, actor=buyer)

    services.accept_offer(offer=offer, actor=seller)
    offer.refresh_from_db()
    assert offer.status == OfferStatus.ACCEPTED


def test_accepting_closes_the_other_offers(listing, seller, buyer, django_user_model):
    rival = django_user_model.objects.create_user("rival", password="TallRiverStone42")
    winner = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8900000"))
    loser = services.make_offer(listing=listing, buyer=rival, amount=Decimal("8600000"))

    services.accept_offer(offer=winner, actor=seller)

    loser.refresh_from_db()
    listing.refresh_from_db()
    assert loser.status == OfferStatus.DECLINED
    assert listing.status == ListingStatus.UNDER_OFFER
    assert rival.notifications.filter(kind="offer").exists()


def test_a_stranger_cannot_join_the_negotiation(listing, buyer, django_user_model):
    stranger = django_user_model.objects.create_user("stranger", password="TallRiverStone42")
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))

    with pytest.raises(PermissionDenied):
        services.post_message(offer=offer, actor=stranger, body="Hello")


def test_stale_offers_expire(listing, buyer):
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))
    offer.expires_at = timezone.now() - timedelta(days=1)
    offer.save()

    assert services.expire_stale_offers() == 1
    offer.refresh_from_db()
    assert offer.status == OfferStatus.EXPIRED


def test_the_gap_to_asking_is_reported(listing, buyer):
    offer = services.make_offer(listing=listing, buyer=buyer, amount=Decimal("8100000"))

    assert offer.gap_to_asking == Decimal("-10.0")


# --- Shares ---------------------------------------------------------------


@pytest.fixture
def fractional(seller):
    return make_listing(
        seller,
        title="Fractional flat",
        fractional_enabled=True,
        total_shares=1000,
        asking_price=Decimal("10000000"),
    )


def test_buying_shares_creates_a_holding_and_a_ledger(fractional, buyer):
    trade = services.buy_shares(listing=fractional, buyer=buyer, shares=50)

    holding = Holding.objects.get(user=buyer, listing=fractional)
    assert holding.shares == 50
    assert holding.invested == trade.total
    assert holding.ownership_percent == Decimal("5.00")

    kinds = set(LedgerEntry.objects.filter(trade=trade).values_list("kind", flat=True))
    assert kinds == {"buy", "fee", "payout"}


def test_shares_sold_is_tracked_on_the_listing(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=200)
    fractional.refresh_from_db()

    assert fractional.shares_sold == 200
    assert fractional.shares_available == 800
    assert fractional.fraction_sold_percent == 20.0


def test_cannot_buy_more_shares_than_remain(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=990)

    with pytest.raises(ValueError, match="still available"):
        services.buy_shares(listing=fractional, buyer=buyer, shares=50)


def test_a_whole_listing_cannot_be_bought_in_shares(listing, buyer):
    with pytest.raises(ValueError, match="sold whole"):
        services.buy_shares(listing=listing, buyer=buyer, shares=10)


def test_the_owner_cannot_buy_their_own_shares(fractional, seller):
    with pytest.raises(PermissionDenied):
        services.buy_shares(listing=fractional, buyer=seller, shares=10)


def test_selling_keeps_the_average_cost_unchanged(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=100)
    before = Holding.objects.get(user=buyer, listing=fractional).average_cost

    services.sell_shares(listing=fractional, seller=buyer, shares=40)
    after = Holding.objects.get(user=buyer, listing=fractional)

    assert after.shares == 60
    assert after.average_cost == before


def test_selling_everything_clears_the_holding(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=30)
    services.sell_shares(listing=fractional, seller=buyer, shares=30)

    assert not Holding.objects.filter(user=buyer, listing=fractional).exists()


def test_cannot_sell_shares_you_do_not_hold(fractional, buyer):
    with pytest.raises(ValueError, match="do not hold"):
        services.sell_shares(listing=fractional, seller=buyer, shares=5)


def _unmatched_rupees() -> Decimal:
    """
    What is left over when every ledger row is added together.

    Fees are excluded because they are the platform's revenue and have only one
    side by design. Everything else is a transfer, so the rest must cancel.
    """
    return LedgerEntry.objects.exclude(kind=LedgerEntry.Kind.FEE).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")


def test_a_purchase_leaves_no_unmatched_money(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=100)

    assert _unmatched_rupees() == 0


def test_a_sale_leaves_no_unmatched_money(fractional, buyer):
    """
    A sale credits the seller, so something has to be debited for it.

    Before this was fixed the owner took the shares back for free and every
    sale invented its own purchase price out of nothing.
    """
    services.buy_shares(listing=fractional, buyer=buyer, shares=100)
    services.sell_shares(listing=fractional, seller=buyer, shares=40)

    assert _unmatched_rupees() == 0


def test_a_sale_debits_the_owner_who_takes_the_shares_back(fractional, buyer):
    services.buy_shares(listing=fractional, buyer=buyer, shares=100)
    services.sell_shares(listing=fractional, seller=buyer, shares=100)

    owner_rows = LedgerEntry.objects.filter(user=fractional.owner)

    assert owner_rows.filter(kind=LedgerEntry.Kind.PAYOUT).count() == 1
    assert owner_rows.filter(kind=LedgerEntry.Kind.BUY).count() == 1
    assert owner_rows.aggregate(total=Sum("amount"))["total"] == 0


def test_a_holder_can_still_exit_a_listing_that_left_the_market(fractional, buyer):
    """Trading stops when a property sells; getting your money out does not."""
    services.buy_shares(listing=fractional, buyer=buyer, shares=10)
    fractional.status = ListingStatus.SOLD
    fractional.save(update_fields=["status"])

    services.sell_shares(listing=fractional, seller=buyer, shares=10)

    fractional.refresh_from_db()
    assert fractional.shares_sold == 0


# --- Portfolio ------------------------------------------------------------


def test_the_portfolio_adds_up(fractional, buyer, seller):
    second = make_listing(
        seller, title="Second fractional", city="Kochi",
        fractional_enabled=True, total_shares=500, asking_price=Decimal("5000000"),
    )
    services.buy_shares(listing=fractional, buyer=buyer, shares=100)
    services.buy_shares(listing=second, buyer=buyer, shares=50)

    data = services.portfolio(buyer)

    assert data["property_count"] == 2
    assert data["invested"] == Decimal("1500000")
    assert data["value"] == data["invested"]  # prices have not moved
    assert sum(row["percent"] for row in data["allocation_city"]) == pytest.approx(100, abs=0.1)
    assert all(row["colour"].startswith("#") for row in data["allocation_city"])


def test_the_portfolio_of_a_new_account_is_empty(buyer):
    data = services.portfolio(buyer)

    assert data["property_count"] == 0
    assert data["value"] == Decimal("0")
    assert data["allocation_city"] == []
