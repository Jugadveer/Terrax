"""Authentication, profile, identity, wallet, watchlist, and notifications."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts import services
from accounts.forms import (
    IdentityForm,
    LoginForm,
    NotificationPreferencesForm,
    ProfileForm,
    SavedSearchForm,
    SignUpForm,
    WalletForm,
)
from accounts.models import Profile, SavedSearch, WatchlistItem
from properties.models import Listing

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def sign_up(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        services.notify(
            user,
            kind="system",
            title="Welcome to Basix",
            body="Verify your identity to start listing or investing.",
            url="/account/identity/",
        )
        return redirect("core:dashboard")

    return render(request, "accounts/sign_up.html", {"form": form})


def sign_in(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect(request.POST.get("next") or "core:dashboard")

    return render(request, "accounts/sign_in.html", {"form": form})


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    """
    POST only.

    Logging out over GET lets any page on the internet sign a user out by
    embedding a link or an image, which is what the previous build allowed.
    """
    logout(request)
    return redirect("core:home")


# ---------------------------------------------------------------------------
# Profile and settings
# ---------------------------------------------------------------------------


@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    form = ProfileForm(request.POST or None, request.FILES or None, instance=profile)
    preferences = NotificationPreferencesForm(instance=profile)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("accounts:settings")

    return render(
        request,
        "accounts/settings.html",
        {"form": form, "preferences": preferences},
    )


@login_required
@require_POST
def save_preferences(request: HttpRequest) -> HttpResponse:
    form = NotificationPreferencesForm(request.POST, instance=request.user.profile)
    if form.is_valid():
        form.save()
        messages.success(request, "Notification preferences saved.")
    return redirect("accounts:settings")


@require_GET
def public_profile(request: HttpRequest, public_id) -> HttpResponse:
    profile = get_object_or_404(Profile.objects.select_related("user"), public_id=public_id)
    listings = (
        Listing.objects.public().with_display_data().filter(owner=profile.user)[:9]
    )
    return render(
        request,
        "accounts/public_profile.html",
        {"seller": profile, "listings": listings, "listing_count": listings.count()},
    )


# ---------------------------------------------------------------------------
# Identity and wallet
# ---------------------------------------------------------------------------


@login_required
def identity(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    form = IdentityForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.profile = profile
        document.save()
        services.submit_identity(profile)
        messages.success(request, "Document received. Verification is under way.")
        return redirect("accounts:identity")

    return render(
        request,
        "accounts/identity.html",
        {"form": form, "documents": profile.identity_documents.all()},
    )


@login_required
def wallet(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    form = WalletForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        services.connect_wallet(profile, form.cleaned_data["wallet_address"])
        messages.success(request, "Wallet address saved.")
        return redirect("accounts:wallet")

    return render(request, "accounts/wallet.html", {"form": form})


@login_required
@require_POST
def disconnect_wallet(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    profile.wallet_address = ""
    profile.wallet_verified_at = None
    profile.save(update_fields=["wallet_address", "wallet_verified_at"])
    messages.success(request, "Wallet disconnected.")
    return redirect("accounts:wallet")


# ---------------------------------------------------------------------------
# Watchlist, saved searches, listings
# ---------------------------------------------------------------------------


@login_required
@require_GET
def watchlist(request: HttpRequest) -> HttpResponse:
    items = (
        WatchlistItem.objects.filter(user=request.user)
        .select_related("listing", "listing__owner")
        .prefetch_related("listing__images")
    )
    # Price movement since the listing was added, computed here so the template
    # stays free of arithmetic.
    rows = []
    for item in items:
        current = item.listing.asking_price
        change = None
        if current and item.price_at_add:
            change = float((current - item.price_at_add) / item.price_at_add * 100)
        rows.append({"item": item, "listing": item.listing, "change": change})

    return render(request, "accounts/watchlist.html", {"rows": rows})


@login_required
@require_GET
def saved_searches(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "accounts/saved_searches.html",
        {"searches": SavedSearch.objects.filter(user=request.user)},
    )


@login_required
@require_POST
def save_search(request: HttpRequest) -> HttpResponse:
    """Store the filters currently applied on the marketplace."""
    from properties.forms import SearchForm

    search_form = SearchForm(request.POST)
    query = {
        key: str(value)
        for key, value in (search_form.cleaned_data if search_form.is_valid() else {}).items()
        if value not in (None, "", False)
    }

    form = SavedSearchForm(request.POST, user=request.user, query=query)
    if form.is_valid():
        saved = form.save()
        messages.success(request, f"Saved as “{saved.name}”. You will be told about new matches.")
    else:
        messages.error(request, next(iter(form.errors.values()))[0])
    return redirect(request.POST.get("next") or "properties:index")


@login_required
@require_POST
def delete_saved_search(request: HttpRequest, pk: int) -> HttpResponse:
    get_object_or_404(SavedSearch, pk=pk, user=request.user).delete()
    messages.success(request, "Saved search removed.")
    return redirect("accounts:saved_searches")


@login_required
@require_GET
def my_listings(request: HttpRequest) -> HttpResponse:
    listings = Listing.objects.owned_by(request.user).with_display_data()
    status = request.GET.get("status")
    if status:
        listings = listings.filter(status=status)

    counts = {"all": Listing.objects.owned_by(request.user).count()}
    for row in (
        Listing.objects.owned_by(request.user).values("status").annotate(n=Count("id"))
    ):
        counts[row["status"]] = row["n"]

    return render(
        request,
        "accounts/my_listings.html",
        {"listings": listings, "counts": counts, "active_status": status},
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@login_required
@require_GET
def notifications(request: HttpRequest) -> HttpResponse:
    items = request.user.notifications.all()[:60]
    return render(request, "accounts/notifications.html", {"notifications": items})


@login_required
@require_POST
def read_notifications(request: HttpRequest) -> HttpResponse:
    services.mark_notifications_read(request.user)
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204, headers={"HX-Refresh": "true"})
    return redirect("accounts:notifications")
