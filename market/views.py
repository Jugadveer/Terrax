"""Offers, the negotiation thread, share trading, and the portfolio."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from market import services
from market.forms import CounterOfferForm, MessageForm, OfferForm, ShareTradeForm
from market.models import Holding, LedgerEntry, Offer, Trade
from properties.models import Listing

# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


@login_required
@require_GET
def offers(request: HttpRequest) -> HttpResponse:
    tab = request.GET.get("tab", "received")
    queryset = Offer.objects.for_user(request.user).with_context()

    if tab == "made":
        queryset = queryset.filter(buyer=request.user)
    elif tab == "received":
        queryset = queryset.filter(listing__owner=request.user)

    return render(
        request,
        "market/offers.html",
        {
            "offers": queryset[:50],
            "tab": tab,
            "counts": services.open_offer_counts(request.user),
        },
    )


@login_required
@require_GET
def offer_detail(request: HttpRequest, public_id) -> HttpResponse:
    offer = _participant_offer(request.user, public_id)
    return render(
        request,
        "market/offer_detail.html",
        {
            "offer": offer,
            "events": offer.events.select_related("actor", "actor__profile"),
            "is_seller": offer.listing.owner_id == request.user.pk,
            "counter_form": CounterOfferForm(initial={"amount": offer.amount}),
            "message_form": MessageForm(),
        },
    )


@login_required
@require_POST
def make_offer(request: HttpRequest, public_id) -> HttpResponse:
    listing = get_object_or_404(Listing.objects.public(), public_id=public_id)
    form = OfferForm(request.POST, listing=listing)

    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()))[0])
        return redirect(listing.get_absolute_url())

    try:
        offer = services.make_offer(
            listing=listing,
            buyer=request.user,
            amount=form.cleaned_data["amount"],
            message=form.cleaned_data["message"],
        )
    except (ValueError, PermissionDenied) as exc:
        messages.error(request, str(exc))
        return redirect(listing.get_absolute_url())

    messages.success(request, "Offer sent. The seller has seven days to respond.")
    return redirect("market:offer_detail", public_id=offer.public_id)


@login_required
@require_POST
def respond(request: HttpRequest, public_id, action: str) -> HttpResponse:
    """One entry point for every response, so the guard rails are written once."""
    offer = _participant_offer(request.user, public_id)

    try:
        if action == "accept":
            services.accept_offer(offer=offer, actor=request.user)
            messages.success(request, "Offer accepted. The listing is now under offer.")
        elif action == "decline":
            services.decline_offer(
                offer=offer, actor=request.user, reason=request.POST.get("reason", "")
            )
            messages.success(request, "Offer declined.")
        elif action == "withdraw":
            services.withdraw_offer(
                offer=offer, actor=request.user, reason=request.POST.get("reason", "")
            )
            messages.success(request, "Offer withdrawn.")
        elif action == "counter":
            form = CounterOfferForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Enter a valid counter amount.")
            else:
                services.counter_offer(
                    offer=offer,
                    actor=request.user,
                    amount=form.cleaned_data["amount"],
                    message=form.cleaned_data["message"],
                )
                messages.success(request, "Counter offer sent.")
        elif action == "message":
            form = MessageForm(request.POST)
            if form.is_valid():
                services.post_message(
                    offer=offer, actor=request.user, body=form.cleaned_data["body"]
                )
        else:
            messages.error(request, "Unknown action.")
    except (ValueError, PermissionDenied) as exc:
        messages.error(request, str(exc))

    return redirect("market:offer_detail", public_id=offer.public_id)


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------


@login_required
@require_POST
def buy_shares(request: HttpRequest, public_id) -> HttpResponse:
    listing = get_object_or_404(Listing.objects.public(), public_id=public_id)
    form = ShareTradeForm(request.POST, listing=listing, maximum=listing.shares_available)

    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()))[0])
        return redirect(listing.get_absolute_url())

    try:
        trade = services.buy_shares(
            listing=listing, buyer=request.user, shares=form.cleaned_data["shares"]
        )
    except (ValueError, PermissionDenied) as exc:
        messages.error(request, str(exc))
        return redirect(listing.get_absolute_url())

    messages.success(
        request, f"{trade.shares:,} shares bought. They are in your portfolio."
    )
    return redirect("market:portfolio")


@login_required
@require_POST
def sell_shares(request: HttpRequest, public_id) -> HttpResponse:
    listing = get_object_or_404(Listing, public_id=public_id)
    holding = Holding.objects.filter(user=request.user, listing=listing).first()
    form = ShareTradeForm(
        request.POST, listing=listing, maximum=holding.shares if holding else 0
    )

    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()))[0])
        return redirect("market:portfolio")

    try:
        trade = services.sell_shares(
            listing=listing, seller=request.user, shares=form.cleaned_data["shares"]
        )
    except (ValueError, PermissionDenied) as exc:
        messages.error(request, str(exc))
        return redirect("market:portfolio")

    messages.success(request, f"{trade.shares:,} shares sold.")
    return redirect("market:portfolio")


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


@login_required
@require_GET
def portfolio(request: HttpRequest) -> HttpResponse:
    data = services.portfolio(request.user)
    return render(request, "market/portfolio.html", data)


@login_required
@require_GET
def activity(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "market/activity.html",
        {
            "entries": LedgerEntry.objects.filter(user=request.user).select_related(
                "listing", "trade"
            )[:100],
            "trades": Trade.objects.filter(buyer=request.user).select_related("listing")[:40],
        },
    )


def _participant_offer(user, public_id) -> Offer:
    """
    Load an offer only if this user is one of the two parties.

    The filter is in the query, so there is no path that fetches someone else's
    negotiation and then checks.
    """
    offer = (
        Offer.objects.for_user(user)
        .select_related("listing", "listing__owner", "buyer", "buyer__profile")
        .filter(public_id=public_id)
        .first()
    )
    if offer is None:
        raise PermissionDenied("That offer is not yours.")
    return offer
