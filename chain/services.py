"""
Publishing a listing's evidence, and recording it.

Three things happen to a published listing, in order, and each one degrades on
its own without taking the others down:

1. **A metadata bundle is built** from the listing's facts, its documents'
   checksums and its valuation. This always happens, because it is just a
   dictionary.
2. **The bundle is pinned to IPFS** when Pinata is configured, which gives it a
   content identifier anyone can fetch.
3. **The identifier and a hash of the bundle are written to a contract** when a
   chain is configured. Without one, the same record is kept locally and the
   interface says so rather than implying a chain that is not there.

The web request never waits on a chain. `publish_to_chain` writes the local
record and returns; `python manage.py sync_chain` is what actually sends
transactions. A listing page that hangs for eleven seconds because a testnet is
congested is a worse failure than a listing that says "not yet recorded".
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone
from eth_utils import keccak

from chain import client
from chain.models import PinRecord, TokenRecord

logger = logging.getLogger(__name__)

TIMEOUT = 30


# ---------------------------------------------------------------------------
# Metadata and its hash
# ---------------------------------------------------------------------------


def build_metadata(listing) -> dict[str, Any]:
    """
    The record that gets pinned.

    Deliberately a snapshot of the facts and the analysis, not a copy of the
    database row: a CID is only useful if what it points at is meaningful to
    someone who does not have this application.
    """
    valuation = listing.valuations.first()

    return {
        "schema": "terrax/listing/1",
        "id": str(listing.public_id),
        "title": listing.title,
        "summary": listing.summary,
        "property": {
            "type": listing.property_type,
            "ownership": listing.ownership_type,
            "area_sqft": float(listing.area_sqft) if listing.area_sqft else None,
            "carpet_area_sqft": (
                float(listing.carpet_area_sqft) if listing.carpet_area_sqft else None
            ),
            "bedrooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "year_built": listing.year_built,
            "amenities": [a.name for a in listing.amenities.all()],
        },
        "location": {
            "locality": listing.locality,
            "city": listing.city,
            "state": listing.state,
            "pincode": listing.pincode,
            "latitude": float(listing.latitude) if listing.latitude else None,
            "longitude": float(listing.longitude) if listing.longitude else None,
        },
        "price": {
            "asking": float(listing.asking_price) if listing.asking_price else None,
            "currency": "INR",
            "fractional": listing.fractional_enabled,
            "total_shares": listing.total_shares,
        },
        "valuation": (
            {
                "estimate": float(valuation.estimate),
                "low": float(valuation.low),
                "high": float(valuation.high),
                "confidence": valuation.confidence,
                "method": "comparable-sales",
                "comparables": valuation.comparable_count,
                "computed_at": valuation.created_at.isoformat(),
            }
            if valuation
            else None
        ),
        "documents": [
            {
                "kind": document.kind,
                "sha256": document.checksum,
                "verification": document.verification,
            }
            for document in listing.documents.all()
        ],
        "published_at": listing.published_at.isoformat() if listing.published_at else None,
    }


def canonical(metadata: dict[str, Any]) -> bytes:
    """
    The exact bytes that get hashed.

    Sorted keys and no incidental whitespace, so that two people who build the
    same bundle produce the same bytes. Without this the hash would depend on
    dictionary ordering and verification would fail for no reason anyone could
    see.
    """
    return json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()


def evidence_hash(metadata: dict[str, Any]) -> bytes:
    """
    keccak256 of the pinned bundle, which is what the contract stores.

    Verification is therefore one step with no special knowledge: fetch the CID,
    hash the bytes, compare against the chain. Change one document, one figure
    or one word and the hashes stop matching.
    """
    return keccak(canonical(metadata))


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


def is_pinning_configured() -> bool:
    return bool(settings.PINATA_API_KEY and settings.PINATA_SECRET_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "pinata_api_key": settings.PINATA_API_KEY,
        "pinata_secret_api_key": settings.PINATA_SECRET_API_KEY,
    }


def pin_listing(listing) -> PinRecord | None:
    """
    Pin the listing's metadata and return the record, or None.

    Failures are logged and swallowed. The caller is publishing a listing; an
    unreachable pinning service is a reason to record that the listing is not
    pinned, not a reason to refuse the publication.
    """
    if not is_pinning_configured():
        logger.info("pinning skipped for listing %s: no credentials", listing.pk)
        return None

    metadata = build_metadata(listing)
    try:
        response = requests.post(
            f"{settings.PINATA_BASE_URL}/pinJSONToIPFS",
            json={
                "pinataContent": metadata,
                "pinataMetadata": {"name": f"terrax-{listing.public_id}"},
            },
            headers=_headers(),
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        cid = response.json()["IpfsHash"]
    except Exception as exc:  # noqa: BLE001 - the listing must still publish
        logger.warning("pinning failed for listing %s: %s", listing.pk, exc)
        return None

    return PinRecord.objects.create(
        listing=listing,
        kind=PinRecord.Kind.METADATA,
        cid=cid,
        label="Listing metadata",
        size_bytes=len(canonical(metadata)),
        provider="pinata",
    )


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def publish_to_chain(listing) -> dict[str, Any]:
    """
    The chain-side step of publishing: pin, then write the local record.

    Returns what happened, so the caller can tell the user whether the evidence
    was pinned rather than quietly implying that it was. Sending the record to
    an actual chain is `sync_chain`'s job, off the request path.
    """
    pin = pin_listing(listing)
    metadata = build_metadata(listing)

    token, _ = TokenRecord.objects.get_or_create(
        listing=listing,
        defaults={"token_id": f"TRX-{listing.pk:05d}", "chain": TokenRecord.Chain.LOCAL},
    )
    if pin:
        token.metadata_cid = pin.cid
    token.evidence_hash = "0x" + evidence_hash(metadata).hex()
    token.save(update_fields=["metadata_cid", "evidence_hash"])

    return {
        "pinned": pin is not None,
        "cid": pin.cid if pin else "",
        "token_id": token.token_id,
        "reason": ""
        if pin
        else ("no credentials" if not is_pinning_configured() else "provider unavailable"),
    }


def record_on_chain(listing) -> TokenRecord:
    """
    Write one listing's deed to the contract.

    The evidence hash is recomputed here rather than read from the row, because
    the point of the hash is to describe the listing as it is now. A listing
    re-valued since it was recorded gets `updateEvidence`, which leaves the
    earlier hash in the event log rather than erasing it.
    """
    metadata = build_metadata(listing)
    digest = evidence_hash(metadata)
    cid = _metadata_cid(listing)

    token, _ = TokenRecord.objects.get_or_create(
        listing=listing,
        defaults={"token_id": f"TRX-{listing.pk:05d}", "chain": TokenRecord.Chain.LOCAL},
    )
    contract = client.deed()

    if token.chain_token_id:
        receipt = client.send(contract.functions.updateEvidence(token.chain_token_id, cid, digest))
    else:
        receipt = client.send(contract.functions.record(_holder_for(listing), cid, digest))
        token.chain_token_id = contract.functions.totalMinted().call()

    token.chain = _chain_choice()
    token.contract_address = contract.address
    token.tx_hash = _prefixed(receipt.tx_hash)
    token.block_number = receipt.block_number
    token.metadata_cid = cid
    token.evidence_hash = "0x" + digest.hex()
    token.recorded_at = timezone.now()
    token.save()
    return token


def open_share_pool(listing) -> TokenRecord:
    """Fix a property's share supply on chain, once, before any is sold."""
    token = listing.token
    if not token.chain_token_id:
        raise client.ChainError("Record the deed before opening its share pool.")

    contract = client.shares()
    if contract.functions.supplyOf(token.chain_token_id).call():
        return token

    client.send(contract.functions.open(token.chain_token_id, listing.total_shares))
    token.shares_opened_at = timezone.now()
    token.save(update_fields=["shares_opened_at"])
    return token


def issue_shares(listing, *, wallet: str, amount: int) -> str:
    """Move shares out of the on-chain pool into a holder's wallet."""
    contract = client.shares()
    receipt = client.send(
        contract.functions.issue(listing.token.chain_token_id, _checksum(wallet), amount)
    )
    return _prefixed(receipt.tx_hash)


def onchain_shares(listing, *, wallet: str) -> int:
    """What the chain says a wallet holds, for reconciling against the ledger."""
    return (
        client.shares()
        .functions.balanceOf(_checksum(wallet), listing.token.chain_token_id)
        .call()
    )


def verify(listing) -> dict[str, Any]:
    """
    Recompute the evidence hash and ask the contract whether it matches.

    This is the question the whole arrangement exists to answer, so it is a
    function rather than a paragraph in a README: are the documents on this page
    the documents that were recorded?
    """
    token = getattr(listing, "token", None)
    if not token or not token.chain_token_id or not client.has_contracts():
        return {"checked": False, "matches": False, "reason": "not recorded on a chain"}

    digest = evidence_hash(build_metadata(listing))
    matches = client.deed().functions.verifyEvidence(token.chain_token_id, digest).call()

    return {
        "checked": True,
        "matches": matches,
        "expected": "0x" + digest.hex(),
        "recorded": token.evidence_hash,
        "reason": "" if matches else "the listing has changed since it was recorded",
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _metadata_cid(listing) -> str:
    """
    The identifier to record, or a stand-in when nothing is pinned.

    The contract requires a non-empty string, and a deed that points at nothing
    is still worth recording: the hash alone fixes what the documents were on
    the day it was written.
    """
    pin = listing.pins.filter(kind=PinRecord.Kind.METADATA).first()
    return pin.cid if pin else f"unpinned/{listing.public_id}"


def _holder_for(listing) -> str:
    """
    The wallet that should hold the deed.

    The seller's own address when they have connected one, otherwise the
    issuer's. A deed sent to an address nobody controls is a deed that is lost,
    so an unconnected seller is a reason to hold it on their behalf.
    """
    wallet = getattr(getattr(listing.owner, "profile", None), "wallet_address", "")
    return _checksum(wallet) if wallet else client.address()


def _checksum(wallet: str) -> str:
    from web3 import Web3

    return Web3.to_checksum_address(wallet)


def _prefixed(tx_hash: str) -> str:
    return tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"


def _chain_choice() -> str:
    """
    Map the configured chain id onto the stored label.

    An id this build has no name for is still a real chain, so it is recorded
    as one. Only a record that never left the database is `LOCAL`.
    """
    return {
        80002: TokenRecord.Chain.POLYGON_AMOY,
        11155111: TokenRecord.Chain.SEPOLIA,
    }.get(settings.WEB3_CHAIN_ID, TokenRecord.Chain.OTHER)
