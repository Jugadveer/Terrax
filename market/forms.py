"""Offer and share-trading forms."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from core.forms import StyledFormMixin


class OfferForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("1"),
        label="Your offer (rupees)",
    )
    message = forms.CharField(
        required=False, max_length=400, widget=forms.Textarea(attrs={"rows": 3}),
        label="Message to the seller",
        help_text="Optional. Sellers respond faster to offers that explain themselves.",
    )

    def __init__(self, *args, listing=None, **kwargs):
        self.listing = listing
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        asking = self.listing.asking_price if self.listing else None
        if asking:
            # Below 40% of asking is almost always a typo, and it wastes the
            # seller's time either way.
            if amount < asking * Decimal("0.4"):
                raise ValidationError(
                    "That is less than half the asking price. Check the number of zeroes."
                )
            if amount > asking * Decimal("3"):
                raise ValidationError("That is well above the asking price. Check the number.")
        return amount


class CounterOfferForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("1"), label="Counter at"
    )
    message = forms.CharField(
        required=False, max_length=400, widget=forms.Textarea(attrs={"rows": 2}),
        label="Message",
    )


class MessageForm(StyledFormMixin, forms.Form):
    body = forms.CharField(
        max_length=400, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Write a reply"}),
        label="Message",
    )


class ShareTradeForm(StyledFormMixin, forms.Form):
    shares = forms.IntegerField(min_value=1, label="Shares")

    def __init__(self, *args, listing=None, maximum: int | None = None, **kwargs):
        self.listing = listing
        self.maximum = maximum
        super().__init__(*args, **kwargs)
        if maximum:
            self.fields["shares"].max_value = maximum
            self.fields["shares"].widget.attrs["max"] = maximum

    def clean_shares(self):
        shares = self.cleaned_data["shares"]
        if self.maximum and shares > self.maximum:
            raise ValidationError(f"The most you can trade here is {self.maximum:,}.")
        return shares
