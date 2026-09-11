"""Registration, profile, identity, and wallet forms."""

from __future__ import annotations

import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from accounts.models import IdentityDocument, Profile, SavedSearch
from core.forms import StyledFormMixin
from properties.validators import validate_document

WALLET_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
PHONE_PATTERN = re.compile(r"^[6-9]\d{9}$")


class SignUpForm(StyledFormMixin, UserCreationForm):
    """
    Registration.

    Built on Django's own form so the configured password validators actually
    run. The previous build called `User.objects.create_user` directly, which
    skipped every one of them.
    """

    email = forms.EmailField(required=True)
    display_name = forms.CharField(max_length=80, label="Full name")
    phone = forms.CharField(max_length=10, label="Mobile number", required=False)

    class Meta:
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "Letters, digits and @ . + - _ only."
        self.fields["password1"].help_text = "At least 10 characters, not all numbers."
        self.fields["password2"].label = "Confirm password"
        self.order_fields(
            ["display_name", "username", "email", "phone", "password1", "password2"]
        )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account already uses that email address.")
        return email

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip().replace(" ", "")
        phone = phone.removeprefix("+91")
        if phone and not PHONE_PATTERN.match(phone):
            raise ValidationError("Enter a 10 digit Indian mobile number.")
        return phone

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            # The profile is created by a signal; fill in what the form knows.
            Profile.objects.filter(user=user).update(
                display_name=self.cleaned_data["display_name"],
                phone=self.cleaned_data.get("phone", ""),
            )
        return user


class LoginForm(StyledFormMixin, AuthenticationForm):
    """Accepts a username or the email address, since people try both."""

    username = forms.CharField(label="Username or email")

    def clean(self):
        identifier = self.cleaned_data.get("username", "")
        if "@" in identifier:
            match = User.objects.filter(email__iexact=identifier).first()
            if match:
                self.cleaned_data["username"] = match.username
        return super().clean()


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            "display_name", "headline", "bio", "avatar", "phone", "city",
            "currency", "theme",
        ]
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}
        labels = {
            "headline": "One line about you",
            "currency": "Show prices in",
            "theme": "Appearance",
        }

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip().replace(" ", "")
        phone = phone.removeprefix("+91")
        if phone and not PHONE_PATTERN.match(phone):
            raise ValidationError("Enter a 10 digit Indian mobile number.")
        return phone


class NotificationPreferencesForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["notify_offers", "notify_watchlist", "notify_digest"]
        labels = {
            "notify_offers": "Offers on my listings, and replies to mine",
            "notify_watchlist": "Price changes on properties I watch",
            "notify_digest": "Weekly market summary",
        }


class IdentityForm(StyledFormMixin, forms.ModelForm):
    """
    Identity document upload.

    Only the last four digits of the number are kept. Storing a full Aadhaar or
    PAN number would put a national identifier in the database for no benefit.
    """

    number = forms.CharField(
        max_length=20,
        label="Document number",
        help_text="Only the last four characters are stored.",
    )

    class Meta:
        model = IdentityDocument
        fields = ["kind", "file"]

    def clean_file(self):
        return validate_document(self.cleaned_data["file"])

    def clean_number(self):
        return (self.cleaned_data["number"] or "").strip()[-4:]

    def save(self, commit: bool = True) -> IdentityDocument:
        document = super().save(commit=False)
        document.number_last4 = self.cleaned_data["number"]
        if commit:
            document.save()
        return document


class WalletForm(StyledFormMixin, forms.Form):
    wallet_address = forms.CharField(
        max_length=64,
        label="Wallet address",
        help_text="An Ethereum-format address, starting 0x.",
    )

    def clean_wallet_address(self):
        address = self.cleaned_data["wallet_address"].strip()
        if not WALLET_PATTERN.match(address):
            raise ValidationError("That is not a valid address. It should be 0x followed by 40 hex characters.")
        return address


class SavedSearchForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SavedSearch
        fields = ["name", "alerts_enabled"]
        labels = {"name": "Name this search", "alerts_enabled": "Tell me about new matches"}

    def __init__(self, *args, user=None, query: dict | None = None, **kwargs):
        self.user = user
        self.query = query or {}
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = SavedSearch.objects.filter(user=self.user, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("You already have a saved search with that name.")
        return name

    def save(self, commit: bool = True) -> SavedSearch:
        saved = super().save(commit=False)
        saved.user = self.user
        saved.query = self.query
        if commit:
            saved.save()
        return saved
