"""Account-side operations: notifications, watchlist, identity, wallet."""

from __future__ import annotations

from django.db import IntegrityError
from django.utils import timezone

from accounts.models import KycStatus, Notification, WatchlistItem


def notify(user, *, kind: str, title: str, body: str = "", url: str = "") -> Notification:
    """
    Create one notification.

    A single entry point means every feature writes notifications the same way,
    and preferences are honoured in one place rather than at each call site.
    """
    profile = getattr(user, "profile", None)
    if profile:
        muted = {
            "offer": not profile.notify_offers,
            "watchlist": not profile.notify_watchlist,
        }
        if muted.get(kind):
            return Notification(user=user, kind=kind, title=title)  # unsaved

    return Notification.objects.create(
        user=user, kind=kind, title=title[:140], body=body[:300], url=url[:300]
    )


def notify_many(users, **kwargs) -> None:
    for user in users:
        notify(user, **kwargs)


def mark_notifications_read(user, ids: list[int] | None = None) -> int:
    queryset = user.notifications.filter(read_at__isnull=True)
    if ids:
        queryset = queryset.filter(id__in=ids)
    return queryset.update(read_at=timezone.now())


def toggle_watchlist(user, listing) -> bool:
    """Add or remove, returning whether the listing is now being watched."""
    try:
        WatchlistItem.objects.create(
            user=user, listing=listing, price_at_add=listing.asking_price
        )
        return True
    except IntegrityError:
        WatchlistItem.objects.filter(user=user, listing=listing).delete()
        return False


def is_watching(user, listing) -> bool:
    if not user.is_authenticated:
        return False
    return WatchlistItem.objects.filter(user=user, listing=listing).exists()


def watched_ids(user) -> set[int]:
    """One query for a whole page of cards, instead of one per card."""
    if not user.is_authenticated:
        return set()
    return set(user.watchlist.values_list("listing_id", flat=True))


def submit_identity(profile) -> None:
    profile.kyc_status = KycStatus.SUBMITTED
    profile.kyc_submitted_at = timezone.now()
    profile.save(update_fields=["kyc_status", "kyc_submitted_at"])
    notify(
        profile.user,
        kind="kyc",
        title="Identity documents received",
        body="Verification usually completes within one working day.",
    )


def set_identity_status(profile, status: str, notes: str = "") -> None:
    profile.kyc_status = status
    profile.kyc_notes = notes[:300]
    profile.kyc_reviewed_at = timezone.now()
    profile.save(
        update_fields=["kyc_status", "kyc_notes", "kyc_reviewed_at"]
    )
    notify(
        profile.user,
        kind="kyc",
        title=(
            "Identity verified"
            if status == KycStatus.VERIFIED
            else "Identity check needs attention"
        ),
        body=notes or "",
        url="/account/identity/",
    )


def connect_wallet(profile, address: str) -> None:
    """
    Store a wallet address.

    Ownership is asserted, not proved. Proving it needs a signed challenge,
    which is why `wallet_verified_at` stays empty and the interface labels the
    address as unverified rather than claiming otherwise.
    """
    profile.wallet_address = address.strip()
    profile.wallet_verified_at = None
    profile.save(update_fields=["wallet_address", "wallet_verified_at"])


def onboarding_steps(user) -> list[dict]:
    """
    The checklist a new account sees until it is finished.

    Each step is a real precondition for using the platform, not a tour.
    """
    profile = user.profile
    return [
        {
            "label": "Confirm your contact details",
            "done": bool(profile.phone and profile.display_name),
            "url": "/account/settings/",
        },
        {
            "label": "Verify your identity",
            "done": profile.is_kyc_verified,
            "url": "/account/identity/",
            "note": "Required before you can list or buy.",
        },
        {
            "label": "Connect a wallet",
            "done": bool(profile.wallet_address),
            "url": "/account/wallet/",
            "note": "Optional, used for settlement.",
        },
        {
            "label": "Save a search or watch a property",
            "done": user.watchlist.exists() or user.saved_searches.exists(),
            "url": "/properties/",
        },
    ]
