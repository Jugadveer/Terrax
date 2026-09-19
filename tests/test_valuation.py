"""The comparable-sales engine."""

from __future__ import annotations

from decimal import Decimal

import pytest

from intelligence import services as intel
from intelligence.engine import comparables
from properties.constants import Furnishing, OwnershipType, PropertyType
from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


def test_uses_comparables_when_the_market_has_them(listing, comparable_market):
    result = comparables.value_listing(listing)

    assert result.method == "comparable-sales"
    assert len(result.comparables) >= comparables.MIN_COMPARABLES


def test_falls_back_to_a_benchmark_with_no_comparables(listing):
    result = comparables.value_listing(listing)

    assert result.method == "regional-benchmark"
    assert result.confidence == "low"
    assert result.estimate > 0


def test_estimate_tracks_the_comparable_rate(listing, comparable_market):
    # Every comparable is priced at 8,000 a square foot, so 1,100 square feet
    # should land near 88 lakh before adjustments.
    result = comparables.value_listing(listing)

    assert Decimal("7_000_000") < result.estimate < Decimal("11_000_000")


def test_the_range_brackets_the_estimate(listing, comparable_market):
    result = comparables.value_listing(listing)

    assert result.low < result.estimate < result.high


def test_confidence_rises_with_a_tighter_market(listing, comparable_market):
    tight = comparables.value_listing(listing)
    assert tight.confidence in {"moderate", "high"}


def test_a_listing_with_no_area_cannot_be_valued(seller):
    listing = make_listing(seller, area_sqft=None)

    result = comparables.value_listing(listing)

    assert result.method == "unavailable"
    assert "No area recorded" in result.adjustments[0]["label"]


def test_power_of_attorney_title_reduces_the_estimate(seller, comparable_market):
    clean = make_listing(seller, ownership_type=OwnershipType.FREEHOLD)
    encumbered = make_listing(seller, ownership_type=OwnershipType.POWER_OF_ATTORNEY)

    assert (
        comparables.value_listing(encumbered).estimate
        < comparables.value_listing(clean).estimate
    )


def test_furnishing_raises_the_estimate(seller, comparable_market):
    bare = make_listing(seller, furnishing=Furnishing.UNFURNISHED)
    furnished = make_listing(seller, furnishing=Furnishing.FULL)

    assert (
        comparables.value_listing(furnished).estimate
        > comparables.value_listing(bare).estimate
    )


def test_adjustments_explain_themselves(seller, comparable_market):
    listing = make_listing(seller, year_built=1970, furnishing=Furnishing.FULL)

    result = comparables.value_listing(listing)
    labels = {a["label"] for a in result.adjustments}

    assert "Older construction" in labels
    assert "Fully furnished" in labels
    assert all(a["reason"] for a in result.adjustments)


def test_comparables_from_a_cheaper_city_are_rebased(seller):
    """
    A flat priced against another city should not inherit that city's rate.

    Without rebasing, a Mumbai flat compared to Pune flats comes out at a
    fraction of its value, which is the failure this adjustment exists for.
    """
    for index in range(6):
        make_listing(
            seller,
            title=f"Pune {index}",
            city="Pune",
            area_sqft=Decimal(1000 + index * 30),
            asking_price=Decimal((1000 + index * 30) * 7000),
        )
    for index in range(4):
        make_listing(
            seller,
            title=f"Mumbai {index}",
            city="Mumbai",
            state="Maharashtra",
            area_sqft=Decimal(700 + index * 30),
            asking_price=Decimal((700 + index * 30) * 35000),
        )

    subject = make_listing(
        seller, title="Mumbai subject", city="Mumbai", area_sqft=Decimal("750"),
        asking_price=Decimal("26000000"),
    )
    result = comparables.value_listing(subject)

    # A Mumbai flat of 750 sq ft is worth crores, not lakhs.
    assert result.estimate > Decimal("15_000_000")


def test_land_is_not_priced_like_a_flat(seller):
    for index in range(6):
        make_listing(
            seller, title=f"Flat {index}", city="Pune",
            area_sqft=Decimal(1000), asking_price=Decimal(8_000_000),
        )
    farm = make_listing(
        seller,
        title="Farmland",
        property_type=PropertyType.FARMLAND,
        city="Pune",
        area_sqft=Decimal("43560"),
        asking_price=Decimal("9_800_000"),
    )

    result = comparables.value_listing(farm)

    # An acre must not be valued at a flat's rate per square foot.
    assert result.estimate < Decimal("50_000_000")


def test_refresh_stores_the_result_and_denormalises_it(listing, comparable_market):
    valuation = intel.refresh_valuation(listing, with_narrative=False)
    listing.refresh_from_db()

    assert listing.valuation_estimate == valuation.estimate
    assert listing.valuation_confidence == valuation.confidence
    assert valuation.comparables
    assert sum(c["weight_percent"] for c in valuation.comparables) == pytest.approx(100, abs=0.5)


def test_narrative_is_written_locally_without_a_model(listing, comparable_market, settings):
    settings.LLM_PROVIDER = "none"
    from intelligence.providers import get_provider

    get_provider.cache_clear()

    valuation = intel.refresh_valuation(listing)

    assert valuation.provider == "local"
    assert len(valuation.narrative) > 60
    assert "—" not in valuation.narrative
