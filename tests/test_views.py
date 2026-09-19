"""Every page renders, and renders from the database rather than from markup."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from intelligence import services as intel
from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


PUBLIC_PAGES = [
    "core:home",
    "core:about",
    "core:how_it_works",
    "core:help",
    "core:contact",
    "core:privacy",
    "core:terms",
    "properties:index",
    "properties:map",
    "properties:compare",
    "intelligence:status",
    "accounts:login",
    "accounts:sign_up",
]

SIGNED_IN_PAGES = [
    "core:dashboard",
    "accounts:settings",
    "accounts:identity",
    "accounts:wallet",
    "accounts:my_listings",
    "accounts:watchlist",
    "accounts:saved_searches",
    "accounts:notifications",
    "market:offers",
    "market:portfolio",
    "market:activity",
    "properties:wizard",
]


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_public_pages_render(client, name, listing):
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", SIGNED_IN_PAGES)
def test_signed_in_pages_render(client, name, seller, listing):
    client.force_login(seller)

    assert client.get(reverse(name)).status_code == 200


def test_the_listing_page_renders_with_its_analysis(client, documented_listing, comparable_market):
    intel.check_listing_documents(documented_listing)
    intel.refresh_valuation(documented_listing, with_narrative=False)
    intel.refresh_risk(documented_listing)

    response = client.get(documented_listing.get_absolute_url())
    body = response.content.decode()

    assert response.status_code == 200
    assert "Independent valuation" in body
    assert "Risk assessment" in body
    assert "Title deed" in body
    assert documented_listing.title in body


def test_a_missing_listing_returns_the_branded_404(client):
    response = client.get("/properties/00000000-0000-0000-0000-000000000000/")

    assert response.status_code == 404
    assert b"That page is not here" in response.content


def test_the_landing_page_numbers_come_from_the_database(client, seller, photo):
    from properties.models import ListingImage

    # An empty market shows an empty market, not invented traction.
    empty = client.get(reverse("core:home")).content.decode()
    assert "Recently verified" not in empty

    for title, city in (("Kothrud two bed", "Pune"), ("Canal facing three bed", "Kochi")):
        listing = make_listing(seller, title=title, city=city)
        ListingImage.objects.create(listing=listing, image=photo)

    populated = client.get(reverse("core:home")).content.decode()

    assert "Recently verified" in populated
    assert "Kothrud two bed" in populated
    assert "Canal facing three bed" in populated


def test_the_marketplace_counts_what_it_shows(client, seller):
    for index in range(5):
        make_listing(seller, title=f"Listing {index}")

    response = client.get(reverse("properties:index"))

    assert b"<strong>5</strong>" in response.content


def test_the_results_fragment_is_returned_to_htmx(client, listing):
    response = client.get(reverse("properties:index"), HTTP_HX_REQUEST="true")
    body = response.content.decode()

    assert 'id="results"' in body
    assert "<html" not in body


def test_filters_survive_in_the_query_string(client, seller):
    make_listing(seller, title="Pune flat", city="Pune")
    make_listing(seller, title="Goa villa", city="Goa")

    response = client.get(reverse("properties:index"), {"city": "Goa"})
    body = response.content.decode()

    assert "Goa villa" in body
    assert "Pune flat" not in body


def test_the_dashboard_starts_empty_for_a_new_account(client, buyer):
    client.force_login(buyer)

    body = client.get(reverse("core:dashboard")).content.decode()

    assert "Nothing has happened yet" in body


def test_the_dashboard_shows_real_activity(client, seller, buyer, listing):
    from market import services as market

    market.make_offer(listing=listing, buyer=buyer, amount=Decimal("8500000"))
    client.force_login(seller)

    body = client.get(reverse("core:dashboard")).content.decode()

    assert "Nothing has happened yet" not in body
    assert listing.title in body


def test_watching_a_listing_swaps_only_the_button(client, buyer, listing):
    client.force_login(buyer)

    response = client.post(
        reverse("properties:watch", args=[listing.public_id]), HTTP_HX_REQUEST="true"
    )
    body = response.content.decode()

    assert "Remove from watchlist" in body
    assert "<html" not in body
    assert buyer.watchlist.count() == 1


def test_comparison_holds_at_most_four(client, seller):
    listings = [make_listing(seller, title=f"L{i}") for i in range(6)]
    ids = [str(listing.public_id) for listing in listings]

    response = client.get(reverse("properties:compare"), {"id": ids})
    body = response.content.decode()

    shown = [listing for listing in listings if listing.get_absolute_url() in body]
    assert len(shown) == 4
    assert shown == listings[:4]


def test_an_empty_comparison_explains_itself(client):
    response = client.get(reverse("properties:compare"))

    assert b"Nothing selected yet" in response.content


def test_the_map_serves_geojson_for_located_listings(client, seller):
    make_listing(seller, title="Located", latitude=Decimal("18.52"), longitude=Decimal("73.85"))
    make_listing(seller, title="Unlocated")

    # The GeoJSON is embedded in a data attribute, so Django escapes the quotes.
    body = client.get(reverse("properties:map")).content.decode()

    assert "FeatureCollection" in body
    assert "Located" in body
    assert body.count("&quot;Feature&quot;") == 1
    assert "Unlocated" not in body


def test_the_health_check_reports_the_database(client):
    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_the_status_page_names_the_active_provider(client, settings):
    settings.LLM_PROVIDER = "none"
    from intelligence.providers import get_provider

    get_provider.cache_clear()

    body = client.get(reverse("intelligence:status")).content.decode()

    assert "Not configured" in body
    assert "Always on" in body
