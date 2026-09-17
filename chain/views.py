"""One page and one action: prove that a listing still matches its record."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from chain import client
from chain import services as chain
from properties.services import visible_listing_or_404


def verify(request: HttpRequest, public_id) -> HttpResponse:
    """
    Recompute a listing's evidence hash and ask the contract about it.

    Deliberately an action rather than part of rendering the listing page. It
    is a call to a node, and nobody should wait on a testnet to read a property
    description.
    """
    listing = visible_listing_or_404(request.user, public_id)

    try:
        result = chain.verify(listing)
    except Exception as exc:  # noqa: BLE001 - an unreachable node is an answer
        result = {"checked": False, "matches": False, "reason": str(exc)}

    return render(
        request,
        "chain/partials/verification.html",
        {"listing": listing, "token": getattr(listing, "token", None), "result": result},
    )


def status(request: HttpRequest) -> HttpResponse:
    """What is configured, what is deployed, and how much of it has been used."""
    from chain.models import TokenRecord

    configured = client.is_configured() and client.has_contracts()
    context = {
        "configured": configured,
        "chain_name": client.chain_name() if client.is_configured() else "",
        "recorded": TokenRecord.objects.exclude(tx_hash="").count(),
        "total": TokenRecord.objects.count(),
        "contracts": [],
    }

    if configured:
        from chain import compiler

        context["issuer"] = client.address()
        context["balance"] = client.balance_eth()
        context["contracts"] = [
            {
                "name": name,
                "address": getattr(client, attribute)().address,
                "size": compiler.compile_contract(name).size_bytes,
                "url": client.explorer_url("address", getattr(client, attribute)().address),
            }
            for name, attribute in (("PropertyDeed", "deed"), ("PropertyShares", "shares"))
        ]

    return render(request, "chain/status.html", context)
