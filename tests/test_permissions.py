"""
Ownership boundaries.

Each of these reproduces a hole that existed in the previous build: anonymous
writes, cross-user access to a listing, cross-user access to a negotiation, and
logout over GET.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


def test_anonymous_cannot_open_the_wizard(client):
    response = client.get(reverse("properties:wizard"))

    assert response.status_code == 302
    assert "sign-in" in response["Location"]


def test_anonymous_cannot_submit_a_listing(client, listing):
    response = client.post(reverse("properties:submit", args=[listing.public_id]))

    assert response.status_code == 302
    assert "sign-in" in response["Location"]


def test_another_user_cannot_edit_a_listing(client, listing, buyer):
    client.force_login(buyer)

    response = client.post(
        reverse("properties:wizard_step", args=[listing.public_id, 4]),
        {"asking_price": "1"},
    )

    assert response.status_code == 403


def test_another_user_cannot_submit_a_listing(client, listing, buyer):
    client.force_login(buyer)

    response = client.post(reverse("properties:submit", args=[listing.public_id]))

    assert response.status_code == 403


def test_another_user_cannot_delete_a_listing(client, listing, buyer):
    client.force_login(buyer)

    response = client.post(reverse("properties:delete", args=[listing.public_id]))

    assert response.status_code == 403
    listing.refresh_from_db()


def test_a_draft_is_invisible_to_everyone_but_its_owner(client, seller, buyer):
    draft = make_listing(seller, status="draft", title="Unfinished")

    client.force_login(buyer)
    assert client.get(draft.get_absolute_url()).status_code == 404

    client.force_login(seller)
    assert client.get(draft.get_absolute_url()).status_code == 200


def test_logout_refuses_get(client, seller):
    client.force_login(seller)

    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).status_code == 302


def test_mutating_endpoints_refuse_get(client, seller, listing):
    client.force_login(seller)

    for name, args in [
        ("properties:submit", [listing.public_id]),
        ("properties:delete", [listing.public_id]),
        ("properties:watch", [listing.public_id]),
        ("accounts:read_notifications", []),
    ]:
        assert client.get(reverse(name, args=args)).status_code == 405, name


def test_a_listing_id_is_not_guessable(listing):
    """
    The public identifier is a UUID, so the catalogue cannot be walked by
    incrementing a number in the address bar.
    """
    assert str(listing.public_id) not in {"1", str(listing.pk)}
    assert len(str(listing.public_id)) == 36


def test_signup_enforces_the_password_validators(client):
    response = client.post(
        reverse("accounts:sign_up"),
        {
            "display_name": "Test Person",
            "username": "tester",
            "email": "tester@example.in",
            "password1": "password",
            "password2": "password",
        },
    )

    assert response.status_code == 200
    assert b"too common" in response.content or b"at least 10" in response.content
