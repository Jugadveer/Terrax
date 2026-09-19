"""Marketplace filtering, plain-language search, and wizard validation."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from intelligence import services as intel
from properties import services
from properties.constants import ListingStatus, PropertyType
from properties.forms import DetailsForm, MediaForm, PricingForm, SearchForm
from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


# --- Filtering ------------------------------------------------------------


@pytest.fixture
def market(seller):
    return [
        make_listing(seller, title="Pune two bed", city="Pune", bedrooms=2,
                     asking_price=Decimal("8000000"), area_sqft=Decimal("1000")),
        make_listing(seller, title="Pune three bed", city="Pune", bedrooms=3,
                     asking_price=Decimal("15000000"), area_sqft=Decimal("1600")),
        make_listing(seller, title="Goa villa", city="Goa", bedrooms=4,
                     property_type=PropertyType.VILLA,
                     asking_price=Decimal("40000000"), area_sqft=Decimal("3000")),
        make_listing(seller, title="Draft flat", city="Pune", status=ListingStatus.DRAFT),
    ]


def test_drafts_never_appear_in_the_marketplace(market):
    titles = {listing.title for listing in services.search({})}

    assert "Draft flat" not in titles
    assert len(titles) == 3


def test_filtering_by_city(market):
    assert services.search({"city": "Pune"}).count() == 2


def test_filtering_by_bedrooms_is_a_minimum(market):
    assert services.search({"bedrooms": 3}).count() == 2


def test_filtering_by_price_range(market):
    results = services.search({"price_min": 10_000_000, "price_max": 20_000_000})

    assert [listing.title for listing in results] == ["Pune three bed"]


def test_free_text_searches_the_description_and_locality(market):
    assert services.search({"q": "kothrud"}).count() == 3
    assert services.search({"q": "villa"}).count() == 1


def test_sorting_by_price(market):
    ascending = [listing.asking_price for listing in services.search({"sort": "price_low"})]

    assert ascending == sorted(ascending)


def test_sorting_by_value_needs_a_valuation(market):
    assert services.search({"sort": "value"}).count() == 0

    for listing in services.search({}):
        intel.refresh_valuation(listing, with_narrative=False)

    results = list(services.search({"sort": "value"}))
    gaps = [listing.valuation_gap for listing in results]
    assert gaps == sorted(gaps)


def test_sorting_by_value_uses_real_arithmetic(seller):
    """
    A regression guard.

    SQLite divides two integer columns with integer division, and a decimal
    price with no fractional part is an integer to it. The first version of
    this annotation produced zero for every row, so the ordering compiled,
    ran, and did nothing.
    """
    bargain = make_listing(seller, title="Bargain", asking_price=Decimal("8000000"))
    fair = make_listing(seller, title="Fair", asking_price=Decimal("10000000"))
    steep = make_listing(seller, title="Steep", asking_price=Decimal("14000000"))

    for listing in (bargain, fair, steep):
        listing.valuation_estimate = Decimal("10000000")
        listing.save(update_fields=["valuation_estimate"])

    ordered = list(services.search({"sort": "value"}))

    assert [listing.title for listing in ordered] == ["Bargain", "Fair", "Steep"]
    assert ordered[0].value_gap == pytest.approx(-0.2)
    assert ordered[2].value_gap == pytest.approx(0.4)


def test_the_search_form_swaps_a_reversed_price_range():
    form = SearchForm({"price_min": "20000000", "price_max": "5000000"})

    assert form.is_valid()
    assert form.cleaned_data["price_min"] == Decimal("5000000")


def test_active_filters_are_listed_for_the_chips():
    form = SearchForm({"city": "Pune", "bedrooms": "3", "sort": "recent"})
    form.is_valid()

    labels = {label for _, label, _ in form.active_filters}

    assert "Bedrooms" in labels
    assert "Sort" not in labels


# --- Plain language search ------------------------------------------------


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("3 bhk in Pune under 90 lakh",
         {"bedrooms": 3, "city": "Pune", "price_max": 9_000_000}),
        ("2 bedroom flat under 1.5 cr",
         {"bedrooms": 2, "property_type": "apartment", "price_max": 15_000_000}),
        ("villa in Goa over 2 crore",
         {"property_type": "villa", "city": "Goa", "price_min": 20_000_000}),
        ("warehouse near Ahmedabad",
         {"property_type": "warehouse", "city": "Ahmedabad"}),
        ("fractional apartments in Bengaluru",
         {"property_type": "apartment", "fractional": "1", "city": "Bengaluru"}),
        ("plot under 50 lakhs", {"property_type": "plot", "price_max": 5_000_000}),
    ],
)
def test_plain_language_compiles_to_filters(phrase, expected, settings):
    settings.LLM_PROVIDER = "none"
    from intelligence.providers import get_provider

    get_provider.cache_clear()

    parsed = intel.parse_search(phrase)

    for key, value in expected.items():
        assert parsed.get(key) == value, f"{phrase!r} -> {parsed}"


def test_an_empty_search_produces_no_filters():
    assert intel.parse_search("") == {}


def test_the_parsed_filters_actually_run(market, settings):
    settings.LLM_PROVIDER = "none"
    from intelligence.providers import get_provider

    get_provider.cache_clear()

    parsed = intel.parse_search("2 bhk in Pune under 1 crore")

    assert services.search(parsed).count() == 1


# --- Wizard validation ----------------------------------------------------


def _png() -> SimpleUploadedFile:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), (30, 70, 50)).save(buffer, "PNG")
    return SimpleUploadedFile("front.png", buffer.getvalue(), content_type="image/png")


def test_step_one_needs_at_least_one_photograph(listing):
    form = MediaForm({}, {}, listing=listing)

    assert not form.is_valid()
    assert "at least one photograph" in str(form.errors)


def test_step_one_rejects_a_renamed_executable(listing):
    payload = SimpleUploadedFile("front.png", b"MZ\x90\x00" + b"\x00" * 5000, content_type="image/png")

    form = MediaForm({}, {"images": [payload]}, listing=listing)

    assert not form.is_valid()
    assert "JPEG, PNG or WebP" in str(form.errors)


def test_step_one_enforces_the_photograph_limit(listing, settings):
    settings.MAX_IMAGES_PER_LISTING = 2

    form = MediaForm({}, {"images": [_png(), _png(), _png()]}, listing=listing)

    assert not form.is_valid()
    assert "limit is 2" in str(form.errors)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("description", "Too short.", "couple of sentences"),
        ("pincode", "12345", "six digits"),
        ("year_built", 1700, "between 1850"),
    ],
)
def test_step_two_rejects_bad_values(listing, field, value, message):
    data = {
        "title": "A flat", "summary": "A flat in Pune",
        "description": "A two bedroom flat on the third floor with a lift and covered parking.",
        "property_type": "apartment", "ownership_type": "freehold",
        "city": "Pune", "area_sqft": "1100",
    }
    data[field] = value

    form = DetailsForm(data, instance=listing)

    assert not form.is_valid()
    assert message in str(form.errors)


def test_step_four_rejects_a_price_entered_in_lakh(listing):
    form = PricingForm({"asking_price": "90"}, instance=listing)

    assert not form.is_valid()
    assert "full price in rupees" in str(form.errors)


def test_fractional_shares_are_bounded(listing):
    form = PricingForm(
        {"asking_price": "9000000", "fractional_enabled": "on", "total_shares": "12"},
        instance=listing,
    )

    assert not form.is_valid()
    assert "between 100 and 100,000" in str(form.errors)


def test_disabling_fractional_clears_the_share_count(listing):
    form = PricingForm({"asking_price": "9000000", "total_shares": "500"}, instance=listing)

    assert form.is_valid()
    assert form.cleaned_data["total_shares"] == 0


# --- Lifecycle ------------------------------------------------------------


def test_an_incomplete_listing_cannot_be_submitted(listing):
    with pytest.raises(ValueError, match="Still to do"):
        services.submit_for_review(listing)


def test_submitting_runs_the_whole_analysis(documented_listing, photo):
    from properties.models import ListingImage

    ListingImage.objects.create(listing=documented_listing, image=photo)
    documented_listing.status = ListingStatus.DRAFT
    documented_listing.save()

    services.submit_for_review(documented_listing)
    documented_listing.refresh_from_db()

    assert documented_listing.status == ListingStatus.SUBMITTED
    assert documented_listing.valuations.exists()
    assert hasattr(documented_listing, "risk")
    assert all(d.verification != "pending" for d in documented_listing.documents.all())


def test_starting_a_draft_reuses_an_untouched_one(seller):
    first = services.start_draft(seller)
    second = services.start_draft(seller)

    assert first.pk == second.pk


def test_a_price_change_is_recorded_once(listing):
    services.record_price(listing, Decimal("9000000"), "Listed")
    services.record_price(listing, Decimal("9000000"), "Unchanged")
    services.record_price(listing, Decimal("8500000"), "Reduced")

    assert listing.price_history.count() == 2
