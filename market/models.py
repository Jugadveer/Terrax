"""Offers, negotiation, fractional shareholdings, and the transaction ledger."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class OfferStatus(models.TextChoices):
    PENDING = "pending", "Awaiting seller"
    COUNTERED = "countered", "Countered"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"
    EXPIRED = "expired", "Expired"


OFFER_TONE = {
    OfferStatus.PENDING: "caution",
    OfferStatus.COUNTERED: "info",
    OfferStatus.ACCEPTED: "gain",
    OfferStatus.DECLINED: "loss",
    OfferStatus.WITHDRAWN: "neutral",
    OfferStatus.EXPIRED: "neutral",
}

#: Statuses where nobody is waiting on anybody.
CLOSED_OFFER_STATUSES = (
    OfferStatus.ACCEPTED,
    OfferStatus.DECLINED,
    OfferStatus.WITHDRAWN,
    OfferStatus.EXPIRED,
)


class OfferQuerySet(models.QuerySet):
    def open(self) -> OfferQuerySet:
        return self.exclude(status__in=CLOSED_OFFER_STATUSES)

    def for_user(self, user) -> OfferQuerySet:
        """Offers this user either made or received."""
        return self.filter(models.Q(buyer=user) | models.Q(listing__owner=user))

    def with_context(self) -> OfferQuerySet:
        return self.select_related("listing", "buyer", "buyer__profile").prefetch_related(
            "listing__images"
        )


class Offer(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="offers"
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="offers_made"
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("1"))]
    )
    shares = models.PositiveIntegerField(
        default=0, help_text="Non-zero when the offer is for a fraction, not the whole."
    )
    message = models.CharField(max_length=400, blank=True)
    status = models.CharField(
        max_length=12, choices=OfferStatus.choices, default=OfferStatus.PENDING
    )
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OfferQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["listing", "status"]),
            models.Index(fields=["buyer", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Offer of {self.amount} on listing {self.listing_id}"

    @property
    def tone(self) -> str:
        return OFFER_TONE.get(self.status, "neutral")

    @property
    def is_open(self) -> bool:
        return self.status not in CLOSED_OFFER_STATUSES

    @property
    def is_expired(self) -> bool:
        return self.is_open and self.expires_at <= timezone.now()

    @property
    def gap_to_asking(self) -> Decimal | None:
        """How far the offer sits from the asking price, as a percentage."""
        asking = self.listing.asking_price
        if not asking:
            return None
        return ((self.amount - asking) / asking * 100).quantize(Decimal("0.1"))

    def can_be_answered_by(self, user) -> bool:
        return self.is_open and user == self.listing.owner

    def can_be_withdrawn_by(self, user) -> bool:
        return self.is_open and user == self.buyer


class OfferEvent(models.Model):
    """
    An append-only thread per offer. Every state change writes an event, so the
    negotiation reads as a conversation and the history cannot be rewritten.
    """

    class Kind(models.TextChoices):
        OPENED = "opened", "Offer made"
        COUNTER = "counter", "Counter offer"
        MESSAGE = "message", "Message"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"
        EXPIRED = "expired", "Expired"

    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    body = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} on offer {self.offer_id}"


class Holding(models.Model):
    """A user's position in one listing, in shares."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="holdings"
    )
    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="holdings"
    )
    shares = models.PositiveIntegerField(default=0)
    invested = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Total rupees paid in, used as the cost basis.",
    )
    first_bought_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "listing"], name="unique_holding_per_listing"
            )
        ]

    def __str__(self) -> str:
        return f"{self.shares} shares of listing {self.listing_id}"

    @property
    def average_cost(self) -> Decimal:
        if not self.shares:
            return Decimal("0")
        return (self.invested / self.shares).quantize(Decimal("0.01"))

    @property
    def current_value(self) -> Decimal:
        price = self.listing.share_price or Decimal("0")
        return (price * self.shares).quantize(Decimal("0.01"))

    @property
    def gain(self) -> Decimal:
        return self.current_value - self.invested

    @property
    def gain_percent(self) -> Decimal:
        if not self.invested:
            return Decimal("0")
        return (self.gain / self.invested * 100).quantize(Decimal("0.1"))

    @property
    def ownership_percent(self) -> Decimal:
        if not self.listing.total_shares:
            return Decimal("0")
        return (
            Decimal(self.shares) / self.listing.total_shares * 100
        ).quantize(Decimal("0.01"))


class Trade(models.Model):
    """One executed share transfer. The ledger is derived from these rows."""

    class Kind(models.TextChoices):
        PRIMARY = "primary", "Primary issue"
        SECONDARY = "secondary", "Secondary sale"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="trades"
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="purchases"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
        help_text="Empty on a primary issue, where shares come from the pool.",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PRIMARY)
    shares = models.PositiveIntegerField()
    price_per_share = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["listing", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.shares} shares at {self.price_per_share}"


class LedgerEntry(models.Model):
    """A user-facing money movement, one row per side of a trade."""

    class Kind(models.TextChoices):
        BUY = "buy", "Purchase"
        SELL = "sell", "Sale"
        PAYOUT = "payout", "Payout"
        FEE = "fee", "Platform fee"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger"
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, help_text="Negative when money leaves."
    )
    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.SET_NULL, null=True, blank=True
    )
    trade = models.ForeignKey(
        Trade, on_delete=models.SET_NULL, null=True, blank=True, related_name="entries"
    )
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "ledger entries"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.amount}"
