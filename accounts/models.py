"""User profile, identity verification, saved searches, and notifications."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse


class KycStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class Currency(models.TextChoices):
    INR = "INR", "Indian rupee"
    USD = "USD", "US dollar"


class Theme(models.TextChoices):
    SYSTEM = "system", "Match system"
    LIGHT = "light", "Light"
    DARK = "dark", "Dark"


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    display_name = models.CharField(max_length=80, blank=True)
    headline = models.CharField(max_length=120, blank=True)
    bio = models.TextField(max_length=600, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    phone = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=80, blank=True)

    wallet_address = models.CharField(max_length=64, blank=True)
    wallet_verified_at = models.DateTimeField(null=True, blank=True)

    kyc_status = models.CharField(
        max_length=16, choices=KycStatus.choices, default=KycStatus.NOT_STARTED
    )
    kyc_submitted_at = models.DateTimeField(null=True, blank=True)
    kyc_reviewed_at = models.DateTimeField(null=True, blank=True)
    kyc_notes = models.CharField(max_length=300, blank=True)

    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.INR
    )
    theme = models.CharField(
        max_length=8, choices=Theme.choices, default=Theme.SYSTEM
    )
    notify_offers = models.BooleanField(default=True)
    notify_watchlist = models.BooleanField(default=True)
    notify_digest = models.BooleanField(default=True)

    onboarding_dismissed = models.BooleanField(default=False)
    last_seen_feed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.display_name or self.user.username

    def get_absolute_url(self) -> str:
        return reverse("accounts:public_profile", args=[self.public_id])

    @property
    def name(self) -> str:
        return self.display_name or self.user.get_full_name() or self.user.username

    @property
    def is_kyc_verified(self) -> bool:
        return self.kyc_status == KycStatus.VERIFIED

    @property
    def kyc_tone(self) -> str:
        return {
            KycStatus.VERIFIED: "gain",
            KycStatus.REJECTED: "loss",
            KycStatus.IN_REVIEW: "caution",
            KycStatus.SUBMITTED: "info",
        }.get(self.kyc_status, "neutral")

    @property
    def wallet_short(self) -> str:
        if not self.wallet_address:
            return ""
        return f"{self.wallet_address[:6]}...{self.wallet_address[-4:]}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs) -> None:
    """Every user has exactly one profile, created with the user."""
    if created:
        Profile.objects.get_or_create(user=instance)


class IdentityDocument(models.Model):
    class Kind(models.TextChoices):
        PAN = "pan", "PAN card"
        AADHAAR = "aadhaar", "Aadhaar"
        PASSPORT = "passport", "Passport"
        DRIVING_LICENCE = "dl", "Driving licence"

    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="identity_documents"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    file = models.FileField(upload_to="kyc/")
    # Only the last four characters are stored. The full number is never
    # persisted, so a database dump does not leak a national identifier.
    number_last4 = models.CharField(max_length=4, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-uploaded_at",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for {self.profile}"


class SavedSearch(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_searches"
    )
    name = models.CharField(max_length=80)
    query = models.JSONField(default=dict, help_text="The filter set, as submitted.")
    alerts_enabled = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_match_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"], name="unique_saved_search_name_per_user"
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def query_string(self) -> str:
        from urllib.parse import urlencode

        return urlencode({k: v for k, v in self.query.items() if v})


class WatchlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist"
    )
    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="watchers"
    )
    price_at_add = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "listing"], name="unique_watchlist_entry"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} watching {self.listing_id}"


class Notification(models.Model):
    class Kind(models.TextChoices):
        OFFER = "offer", "Offer"
        LISTING = "listing", "Listing"
        WATCHLIST = "watchlist", "Watchlist"
        SEARCH = "search", "Saved search"
        KYC = "kyc", "Identity"
        PORTFOLIO = "portfolio", "Portfolio"
        SYSTEM = "system", "System"

    ICONS = {
        Kind.OFFER: "handshake",
        Kind.LISTING: "house-line",
        Kind.WATCHLIST: "heart",
        Kind.SEARCH: "magnifying-glass",
        Kind.KYC: "identification-card",
        Kind.PORTFOLIO: "chart-line-up",
        Kind.SYSTEM: "info",
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=140)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self) -> str:
        return self.title

    @property
    def icon(self) -> str:
        return self.ICONS.get(self.kind, "info")

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
