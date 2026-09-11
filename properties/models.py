"""Listings, their media, and their documents."""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageOps

from properties import constants as C

THUMBNAIL_SIZE = (640, 420)


class Amenity(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    icon = models.CharField(
        max_length=40,
        default="check",
        help_text="Symbol id in static/icons/sprite.svg, without the i- prefix.",
    )

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "amenities"

    def __str__(self) -> str:
        return self.name


class ListingQuerySet(models.QuerySet):
    def public(self) -> ListingQuerySet:
        """Only listings a visitor is allowed to see."""
        return self.filter(status__in=C.PUBLIC_STATUSES)

    def tradeable(self) -> ListingQuerySet:
        return self.filter(status__in=C.TRADEABLE_STATUSES)

    def owned_by(self, user) -> ListingQuerySet:
        return self.filter(owner=user)

    def with_display_data(self) -> ListingQuerySet:
        """Everything a card or detail page renders, in one round trip."""
        return self.select_related("owner", "owner__profile").prefetch_related(
            "images", "amenities"
        )


class Listing(models.Model):
    # Public identifier. Sequential integers let anyone enumerate the whole
    # table from the URL bar, so the primary key never leaves the server.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.SlugField(max_length=180, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listings"
    )

    title = models.CharField(max_length=140, blank=True)
    summary = models.CharField(max_length=220, blank=True)
    description = models.TextField(blank=True)

    property_type = models.CharField(
        max_length=20, choices=C.PropertyType.choices, blank=True
    )
    ownership_type = models.CharField(
        max_length=20, choices=C.OwnershipType.choices, blank=True
    )
    furnishing = models.CharField(
        max_length=20, choices=C.Furnishing.choices, blank=True
    )
    facing = models.CharField(max_length=2, choices=C.Facing.choices, blank=True)

    address_line = models.CharField(max_length=200, blank=True)
    locality = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=80, blank=True, db_index=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    area_sqft = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("1"))],
    )
    carpet_area_sqft = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    floor = models.SmallIntegerField(null=True, blank=True)
    total_floors = models.PositiveSmallIntegerField(null=True, blank=True)
    year_built = models.PositiveSmallIntegerField(null=True, blank=True)

    amenities = models.ManyToManyField(Amenity, blank=True, related_name="listings")

    asking_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("1"))],
        help_text="Rupees.",
    )
    price_negotiable = models.BooleanField(default=True)

    fractional_enabled = models.BooleanField(default=False)
    total_shares = models.PositiveIntegerField(
        default=0, help_text="Shares minted when fractional ownership is enabled."
    )
    shares_sold = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=C.ListingStatus.choices,
        default=C.ListingStatus.DRAFT,
        db_index=True,
    )
    rejection_reason = models.CharField(max_length=300, blank=True)

    wizard_step = models.PositiveSmallIntegerField(
        default=1, help_text="Furthest step completed, so a draft can be resumed."
    )

    view_count = models.PositiveIntegerField(default=0)

    # The latest valuation, copied here by `intelligence.services.refresh_valuation`.
    # Sorting and filtering the marketplace by how a price compares to its own
    # estimate needs one indexed column, not a join to the newest row of a
    # history table on every query.
    valuation_estimate = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False
    )
    valuation_confidence = models.CharField(max_length=10, blank=True, editable=False)

    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ListingQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["city", "property_type"]),
            models.Index(fields=["asking_price"]),
            models.Index(fields=["valuation_estimate"]),
        ]

    def __str__(self) -> str:
        return self.title or f"Untitled listing {self.public_id.hex[:8]}"

    def save(self, *args, **kwargs) -> None:
        if self.title:
            self.slug = slugify(self.title)[:180]
        if self.fractional_enabled and not self.total_shares:
            self.total_shares = 1000
        super().save(*args, **kwargs)

    # --- Presentation ----------------------------------------------------

    def get_absolute_url(self) -> str:
        return reverse("properties:detail", args=[self.public_id])

    @property
    def status_tone(self) -> str:
        return C.STATUS_TONE.get(self.status, "neutral")

    @property
    def location_label(self) -> str:
        parts = [p for p in (self.locality, self.city) if p]
        return ", ".join(parts) or self.state or "Location pending"

    @property
    def cover_image(self):
        for image in self.images.all():
            if image.is_cover:
                return image
        return next(iter(self.images.all()), None)

    @property
    def headline_spec(self) -> str:
        """The one-line spec shown on cards: ``3 BHK - 1,450 sq ft``."""
        bits = []
        if self.bedrooms:
            bits.append(f"{self.bedrooms} BHK")
        if self.area_sqft:
            bits.append(f"{self.area_sqft:,.0f} sq ft")
        if not bits and self.property_type:
            bits.append(self.get_property_type_display())
        return " · ".join(bits)

    # --- Derived numbers -------------------------------------------------

    @property
    def valuation_gap(self) -> Decimal | None:
        """How far the asking price sits above its own estimate, as a percentage."""
        if not (self.asking_price and self.valuation_estimate):
            return None
        return (
            (self.asking_price - self.valuation_estimate)
            / self.valuation_estimate
            * 100
        ).quantize(Decimal("0.1"))

    @property
    def price_per_sqft(self) -> Decimal | None:
        if not (self.asking_price and self.area_sqft):
            return None
        return (self.asking_price / self.area_sqft).quantize(Decimal("0.01"))

    @property
    def share_price(self) -> Decimal | None:
        if not (self.fractional_enabled and self.total_shares and self.asking_price):
            return None
        return (self.asking_price / self.total_shares).quantize(Decimal("0.01"))

    @property
    def shares_available(self) -> int:
        return max(self.total_shares - self.shares_sold, 0)

    @property
    def fraction_sold_percent(self) -> float:
        if not self.total_shares:
            return 0.0
        return round(self.shares_sold / self.total_shares * 100, 1)

    @property
    def age_years(self) -> int | None:
        if not self.year_built:
            return None
        return max(timezone.now().year - self.year_built, 0)

    # --- Pipeline --------------------------------------------------------

    @property
    def is_public(self) -> bool:
        return self.status in C.PUBLIC_STATUSES

    @property
    def is_tradeable(self) -> bool:
        return self.status in C.TRADEABLE_STATUSES

    @property
    def is_editable(self) -> bool:
        return self.status in (
            C.ListingStatus.DRAFT,
            C.ListingStatus.REJECTED,
            C.ListingStatus.SUBMITTED,
        )

    def missing_documents(self) -> list[str]:
        present = {doc.kind for doc in self.documents.all()}
        return [
            C.DocumentKind(kind).label
            for kind in C.REQUIRED_DOCUMENTS
            if kind not in present
        ]

    def readiness(self) -> dict[str, bool]:
        """
        What still blocks submission. Drives the checklist in the wizard,
        so the user is never told "something is wrong" without being told what.
        """
        return {
            "At least one photo": self.images.exists(),
            "Title, summary and description": bool(
                self.title and self.summary and self.description
            ),
            "Location and size": bool(self.city and self.area_sqft),
            "Required documents": not self.missing_documents(),
            "Asking price": self.asking_price is not None,
        }

    @property
    def is_ready_to_submit(self) -> bool:
        return all(self.readiness().values())


class ListingImage(models.Model):
    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="listings/photos/")
    thumbnail = models.ImageField(
        upload_to="listings/thumbs/", blank=True, editable=False
    )
    alt_text = models.CharField(max_length=160, blank=True)
    width = models.PositiveIntegerField(default=0, editable=False)
    height = models.PositiveIntegerField(default=0, editable=False)
    order = models.PositiveSmallIntegerField(default=0)
    is_cover = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("order", "id")

    def __str__(self) -> str:
        return f"Photo {self.pk} of listing {self.listing_id}"

    def save(self, *args, **kwargs) -> None:
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and self.image:
            self._build_thumbnail()

    def _build_thumbnail(self) -> None:
        """
        Downscale once at upload time. Cards then load a 640px file instead of
        a 4MB original, which is most of the difference in marketplace load
        time. Failures are non-fatal: the original is still usable.
        """
        try:
            self.image.open()
            with Image.open(self.image) as source:
                source = ImageOps.exif_transpose(source)
                self.width, self.height = source.size
                thumb = source.convert("RGB")
                thumb.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
                buffer = BytesIO()
                thumb.save(buffer, format="WEBP", quality=82, method=4)
            name = f"{self.pk}.webp"
            self.thumbnail.save(name, ContentFile(buffer.getvalue()), save=False)
            super().save(update_fields=["thumbnail", "width", "height"])
        except Exception:  # noqa: BLE001 - a bad upload must not break the request
            pass

    @property
    def display_url(self) -> str:
        return self.thumbnail.url if self.thumbnail else self.image.url


class ListingDocument(models.Model):
    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="documents"
    )
    kind = models.CharField(max_length=20, choices=C.DocumentKind.choices)
    file = models.FileField(upload_to="listings/documents/")
    original_name = models.CharField(max_length=180, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    checksum = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="SHA-256 of the file, used to spot the same document reused "
                  "across listings.",
    )
    verification = models.CharField(
        max_length=12,
        choices=C.VerificationState.choices,
        default=C.VerificationState.PENDING,
    )
    verification_notes = models.JSONField(default=list, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("kind", "-uploaded_at")

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for listing {self.listing_id}"

    def save(self, *args, **kwargs) -> None:
        if self.file and not self.checksum:
            self.original_name = self.original_name or self.file.name.rsplit("/", 1)[-1]
            self.size_bytes = getattr(self.file, "size", 0) or 0
            self.checksum = self._hash_file()
        super().save(*args, **kwargs)

    def _hash_file(self) -> str:
        digest = hashlib.sha256()
        self.file.seek(0)
        for chunk in self.file.chunks():
            digest.update(chunk)
        self.file.seek(0)
        return digest.hexdigest()

    @property
    def verification_tone(self) -> str:
        return {
            C.VerificationState.PASSED: "gain",
            C.VerificationState.FLAGGED: "caution",
            C.VerificationState.FAILED: "loss",
        }.get(self.verification, "neutral")


class PricePoint(models.Model):
    """One row per asking-price change, so a listing can show a real history."""

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="price_history"
    )
    price = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=120, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("recorded_at",)

    def __str__(self) -> str:
        return f"{self.price} on {self.recorded_at:%Y-%m-%d}"
