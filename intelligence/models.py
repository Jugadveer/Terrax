"""
Stored output of the analysis layer.

Everything here is a cache of work already done. Nothing is computed while a
page renders: a listing page reads the latest `Valuation` row and shows when it
was produced, rather than recomputing comparables on every request.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class Confidence(models.TextChoices):
    LOW = "low", "Low"
    MODERATE = "moderate", "Moderate"
    HIGH = "high", "High"


class Valuation(models.Model):
    """
    One run of the comparable-sales model against one listing.

    Rows are kept rather than overwritten so a price can be explained after the
    fact: "on 3 March we said 1.9 Cr, based on these six comparables".
    """

    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="valuations"
    )
    estimate = models.DecimalField(max_digits=14, decimal_places=2)
    low = models.DecimalField(max_digits=14, decimal_places=2)
    high = models.DecimalField(max_digits=14, decimal_places=2)
    price_per_sqft = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    confidence = models.CharField(
        max_length=10, choices=Confidence.choices, default=Confidence.LOW
    )
    comparable_count = models.PositiveSmallIntegerField(default=0)

    #: The comparables used, each with its weight and adjustments, so the panel
    #: can show the working rather than a bare number.
    comparables = models.JSONField(default=list, blank=True)
    #: Named adjustments applied to the base rate, as (label, percent) pairs.
    adjustments = models.JSONField(default=list, blank=True)

    narrative = models.TextField(
        blank=True, help_text="Plain-language explanation, written by the LLM layer."
    )
    provider = models.CharField(max_length=20, default="local")
    model_name = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        get_latest_by = "created_at"
        indexes = [models.Index(fields=["listing", "-created_at"])]

    def __str__(self) -> str:
        return f"Valuation {self.estimate} for listing {self.listing_id}"

    @property
    def gap_to_asking(self) -> Decimal | None:
        """Positive when the asking price sits above the estimate."""
        asking = self.listing.asking_price
        if not asking or not self.estimate:
            return None
        return ((asking - self.estimate) / self.estimate * 100).quantize(Decimal("0.1"))

    @property
    def confidence_tone(self) -> str:
        return {
            Confidence.HIGH: "gain",
            Confidence.MODERATE: "info",
        }.get(self.confidence, "caution")


class RiskAssessment(models.Model):
    class Band(models.TextChoices):
        LOW = "low", "Low risk"
        MODERATE = "moderate", "Moderate risk"
        ELEVATED = "elevated", "Elevated risk"
        HIGH = "high", "High risk"

    listing = models.OneToOneField(
        "properties.Listing", on_delete=models.CASCADE, related_name="risk"
    )
    score = models.PositiveSmallIntegerField(
        default=0, help_text="0 is clean, 100 is unacceptable."
    )
    band = models.CharField(max_length=10, choices=Band.choices, default=Band.MODERATE)
    #: Each contributing factor: label, weight, points, and why it fired.
    factors = models.JSONField(default=list, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Risk {self.score} for listing {self.listing_id}"

    @property
    def tone(self) -> str:
        return {
            self.Band.LOW: "gain",
            self.Band.MODERATE: "info",
            self.Band.ELEVATED: "caution",
            self.Band.HIGH: "loss",
        }.get(self.band, "neutral")


class AssistantExchange(models.Model):
    """A question asked about one listing, and the answer that was given."""

    listing = models.ForeignKey(
        "properties.Listing",
        on_delete=models.CASCADE,
        related_name="assistant_exchanges",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_exchanges",
    )
    question = models.CharField(max_length=400)
    answer = models.TextField()
    provider = models.CharField(max_length=20, default="local")
    model_name = models.CharField(max_length=60, blank=True)
    grounded = models.BooleanField(
        default=True,
        help_text="False when the answer had to fall back to generic guidance.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        return self.question[:60]


class MarketSnapshot(models.Model):
    """
    A daily roll-up per city. Written by a management command so the market
    pulse on the dashboard is a table read, not an aggregate over every listing.
    """

    date = models.DateField()
    city = models.CharField(max_length=80)
    listing_count = models.PositiveIntegerField(default=0)
    median_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    median_price_per_sqft = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    new_listings = models.PositiveIntegerField(default=0)
    trade_volume = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        ordering = ("-date", "city")
        constraints = [
            models.UniqueConstraint(
                fields=["date", "city"], name="unique_snapshot_per_city_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.city} on {self.date}"
