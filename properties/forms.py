"""
Forms for the listing wizard and the marketplace filters.

All validation lives here, on the server. The wizard cannot advance past a step
whose form does not validate, which is what stops the empty draft rows the
previous build accumulated.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from core.forms import StyledFormMixin
from properties import constants as C
from properties.models import Amenity, Listing, ListingDocument, ListingImage
from properties.validators import validate_document, validate_image


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    """A file field that cleans every file in the selection, not just the first."""

    widget = MultiFileInput

    def clean(self, data, initial=None):
        if not data:
            if self.required and not initial:
                raise ValidationError(self.error_messages["required"], code="required")
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultiFileField, self).clean(f, initial) for f in files]


# ---------------------------------------------------------------------------
# Wizard, step 1: photographs
# ---------------------------------------------------------------------------


class MediaForm(StyledFormMixin, forms.Form):
    images = MultiFileField(
        required=False,
        label="Photographs",
        help_text=(
            f"JPEG, PNG or WebP, up to {settings.MAX_IMAGE_BYTES // 1024 // 1024} MB "
            f"each. {settings.MAX_IMAGES_PER_LISTING} maximum."
        ),
    )

    def __init__(self, *args, listing: Listing | None = None, **kwargs):
        self.listing = listing
        super().__init__(*args, **kwargs)

    def clean_images(self):
        uploads = self.cleaned_data.get("images") or []
        existing = self.listing.images.count() if self.listing else 0

        if not uploads and not existing:
            raise ValidationError("Add at least one photograph of the property.")

        if existing + len(uploads) > settings.MAX_IMAGES_PER_LISTING:
            raise ValidationError(
                f"That would be {existing + len(uploads)} photographs. "
                f"The limit is {settings.MAX_IMAGES_PER_LISTING}."
            )

        for upload in uploads:
            validate_image(upload)
        return uploads

    def save(self) -> Listing:
        listing = self.listing
        start = listing.images.count()
        for index, upload in enumerate(self.cleaned_data["images"]):
            ListingImage.objects.create(
                listing=listing,
                image=upload,
                order=start + index,
                is_cover=(start + index == 0),
                alt_text=listing.title or "Property photograph",
            )
        return listing


# ---------------------------------------------------------------------------
# Wizard, step 2: the property itself
# ---------------------------------------------------------------------------


class DetailsForm(StyledFormMixin, forms.ModelForm):
    amenities = forms.ModelMultipleChoiceField(
        queryset=Amenity.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Listing
        fields = [
            "title", "summary", "description",
            "property_type", "ownership_type", "furnishing", "facing",
            "address_line", "locality", "city", "state", "pincode",
            "latitude", "longitude",
            "area_sqft", "carpet_area_sqft", "bedrooms", "bathrooms",
            "floor", "total_floors", "year_built",
            "amenities",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "summary": forms.TextInput(
                attrs={"placeholder": "One line a buyer sees before they open the listing"}
            ),
            "latitude": forms.HiddenInput(),
            "longitude": forms.HiddenInput(),
        }
        labels = {
            "area_sqft": "Built-up area (sq ft)",
            "carpet_area_sqft": "Carpet area (sq ft)",
            "address_line": "Street address",
            "pincode": "PIN code",
        }
        help_texts = {
            "summary": "Shown on cards and in search results.",
            "carpet_area_sqft": "Optional, but buyers look for it.",
        }

    #: Fields the wizard insists on before step 2 counts as done.
    REQUIRED = ("title", "summary", "description", "property_type", "city", "area_sqft")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.REQUIRED:
            self.fields[name].required = True
        self.fields["ownership_type"].required = True

    def clean_pincode(self):
        pincode = (self.cleaned_data.get("pincode") or "").strip()
        if pincode and (not pincode.isdigit() or len(pincode) != 6):
            raise ValidationError("An Indian PIN code is six digits.")
        return pincode

    def clean_year_built(self):
        from django.utils import timezone

        year = self.cleaned_data.get("year_built")
        if year is None:
            return year
        current = timezone.now().year
        if year < 1850 or year > current + 4:
            raise ValidationError(f"Enter a year between 1850 and {current + 4}.")
        return year

    def clean_description(self):
        description = (self.cleaned_data.get("description") or "").strip()
        if len(description) < 60:
            raise ValidationError(
                "Write at least a couple of sentences. Buyers skip listings with "
                "no description, and it is what the valuation summary reads from."
            )
        return description

    def clean(self):
        cleaned = super().clean()
        built_up = cleaned.get("area_sqft")
        carpet = cleaned.get("carpet_area_sqft")
        if built_up and carpet and carpet > built_up:
            self.add_error(
                "carpet_area_sqft", "Carpet area cannot exceed the built-up area."
            )

        floor = cleaned.get("floor")
        total = cleaned.get("total_floors")
        if floor is not None and total and floor > total:
            self.add_error("floor", "The floor cannot be above the building's height.")

        return cleaned


# ---------------------------------------------------------------------------
# Wizard, step 3: documents
# ---------------------------------------------------------------------------


class DocumentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ListingDocument
        fields = ["kind", "file", "expires_on"]
        widgets = {"expires_on": forms.DateInput(attrs={"type": "date"})}
        labels = {"expires_on": "Expires on (if applicable)"}

    def clean_file(self):
        return validate_document(self.cleaned_data["file"])


# ---------------------------------------------------------------------------
# Wizard, step 4: price
# ---------------------------------------------------------------------------


class PricingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Listing
        fields = [
            "asking_price",
            "price_negotiable",
            "fractional_enabled",
            "total_shares",
        ]
        labels = {
            "asking_price": "Asking price (rupees)",
            "price_negotiable": "Open to offers",
            "fractional_enabled": "Allow fractional ownership",
            "total_shares": "Number of shares",
        }
        help_texts = {
            "total_shares": "Between 100 and 100,000. Each share is an equal part.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asking_price"].required = True
        self.fields["total_shares"].required = False

    def clean_asking_price(self):
        price = self.cleaned_data["asking_price"]
        if price < Decimal("50000"):
            raise ValidationError("Enter the full price in rupees, not in lakh.")
        if price > Decimal("10000000000"):
            raise ValidationError("That is above the limit for a single listing.")
        return price

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("fractional_enabled"):
            shares = cleaned.get("total_shares") or 1000
            if not 100 <= shares <= 100_000:
                self.add_error(
                    "total_shares", "Choose between 100 and 100,000 shares."
                )
            cleaned["total_shares"] = shares
        else:
            cleaned["total_shares"] = 0
        return cleaned


# ---------------------------------------------------------------------------
# Marketplace filters
# ---------------------------------------------------------------------------


class SearchForm(StyledFormMixin, forms.Form):
    """
    Bound to the query string, so a filtered view is a shareable URL and the
    back button behaves. Every field is optional; blanks are dropped.
    """

    q = forms.CharField(required=False, label="Search")
    city = forms.CharField(required=False)
    property_type = forms.ChoiceField(
        required=False, choices=[("", "Any type")] + C.PropertyType.choices
    )
    bedrooms = forms.IntegerField(required=False, min_value=0, max_value=20)
    price_min = forms.DecimalField(required=False, min_value=0)
    price_max = forms.DecimalField(required=False, min_value=0)
    area_min = forms.DecimalField(required=False, min_value=0)
    fractional = forms.BooleanField(required=False)
    verified_only = forms.BooleanField(required=False)
    sort = forms.ChoiceField(
        required=False,
        choices=[(key, label) for key, (label, _) in C.SORT_OPTIONS.items()],
    )

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("price_min"), cleaned.get("price_max")
        if low and high and low > high:
            cleaned["price_min"], cleaned["price_max"] = high, low
        return cleaned

    @property
    def active_filters(self) -> list[tuple[str, str, str]]:
        """(field name, label, display value) for the removable filter chips."""
        if not self.is_valid():
            return []
        out = []
        for name, value in self.cleaned_data.items():
            if name == "sort" or not value:
                continue
            field = self.fields[name]
            label = field.label or name.replace("_", " ").title()
            if name == "property_type":
                value = dict(C.PropertyType.choices).get(value, value)
            elif name in {"price_min", "price_max"}:
                from core.formatting import money

                label = "From" if name == "price_min" else "Up to"
                value = money(value)
            elif name == "bedrooms":
                label, value = "Bedrooms", f"{value}+"
            elif name == "area_min":
                label, value = "Area", f"{value:,.0f}+ sq ft"
            elif name in {"fractional", "verified_only"}:
                value = {"fractional": "Fractional", "verified_only": "Verified"}[name]
                label = "Filter"
            out.append((name, label, str(value)))
        return out
