"""Shared fixtures. Everything builds real rows through the real services."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import KycStatus
from properties.constants import DocumentKind, ListingStatus, OwnershipType, PropertyType
from properties.models import Listing, ListingDocument


@pytest.fixture(autouse=True)
def clear_caches():
    """
    Empty the cache around every test.

    Market statistics and the city rate index are cached for a couple of
    minutes, which is right in production and turns tests into order-dependent
    guesswork if it leaks between them.
    """
    from django.core.cache import cache

    from intelligence.providers import get_provider

    cache.clear()
    get_provider.cache_clear()
    yield
    cache.clear()
    get_provider.cache_clear()


@pytest.fixture
def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 900), (40, 90, 60)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def pdf_bytes() -> bytes:
    """A PDF large enough to pass the legibility check."""
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + b"% pad\n" * (40 * 1024 // 6)
        + b"\n%%EOF\n"
    )


@pytest.fixture
def photo(png_bytes):
    return SimpleUploadedFile("front.png", png_bytes, content_type="image/png")


@pytest.fixture
def seller(db) -> User:
    user = User.objects.create_user("seller", "seller@example.in", "TallRiverStone42")
    user.profile.kyc_status = KycStatus.VERIFIED
    user.profile.display_name = "Priya Nair"
    user.profile.save()
    return user


@pytest.fixture
def buyer(db) -> User:
    user = User.objects.create_user("buyer", "buyer@example.in", "TallRiverStone42")
    user.profile.kyc_status = KycStatus.VERIFIED
    user.profile.display_name = "Rohit Menon"
    user.profile.save()
    return user


def make_listing(owner, **overrides) -> Listing:
    """A complete, publishable listing. Overrides win."""
    fields = {
        "title": "Two bed near the park",
        "summary": "Two bedroom flat of 1,100 sq ft in Kothrud, Pune.",
        "description": (
            "A two bedroom flat on the third floor with a lift and covered "
            "parking. The living room faces the park and the bedrooms are set "
            "back from the road."
        ),
        "property_type": PropertyType.APARTMENT,
        "ownership_type": OwnershipType.FREEHOLD,
        "locality": "Kothrud",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411038",
        "area_sqft": Decimal("1100"),
        "bedrooms": 2,
        "bathrooms": 2,
        "year_built": 2016,
        "asking_price": Decimal("9000000"),
        "status": ListingStatus.LISTED,
    }
    fields.update(overrides)
    return Listing.objects.create(owner=owner, **fields)


@pytest.fixture
def listing(seller) -> Listing:
    return make_listing(seller)


@pytest.fixture
def comparable_market(seller):
    """
    Eight flats of a similar size in one city.

    Enough for the valuation engine to reach high confidence, which is what
    makes the confidence assertions meaningful rather than incidental.
    """
    listings = []
    for index in range(8):
        listings.append(
            make_listing(
                seller,
                title=f"Comparable {index}",
                area_sqft=Decimal(1000 + index * 40),
                asking_price=Decimal((1000 + index * 40) * 8000),
                year_built=2015 + (index % 3),
            )
        )
    return listings


@pytest.fixture
def documented_listing(listing, pdf_bytes) -> Listing:
    for kind in (DocumentKind.TITLE_DEED, DocumentKind.TAX_RECEIPT, DocumentKind.UTILITY_BILL):
        ListingDocument.objects.create(
            listing=listing,
            kind=kind,
            file=SimpleUploadedFile(f"{kind}.pdf", pdf_bytes, content_type="application/pdf"),
        )
    return listing


# --- A real EVM -----------------------------------------------------------


@pytest.fixture(scope="session")
def evm_chain():
    """
    An in-memory EVM with both contracts deployed, shared across the session.

    This is a real execution environment, not a mock: the Solidity in
    `chain/contracts/` is compiled by solc and run by py-evm, so a test that
    passes here would pass against a node. Deployment costs about a second, so
    it happens once and the tests that mutate state use distinct token ids.
    """
    from eth_account import Account
    from web3 import EthereumTesterProvider, Web3

    from chain import compiler

    connection = Web3(EthereumTesterProvider())
    key = "0x" + "11" * 32
    issuer = Account.from_key(key)

    connection.provider.ethereum_tester.add_account(key)
    connection.eth.send_transaction(
        {
            "from": connection.eth.accounts[0],
            "to": issuer.address,
            "value": connection.to_wei(1000, "ether"),
        }
    )

    addresses = {}
    for name in ("PropertyDeed", "PropertyShares"):
        artifact = compiler.compile_contract(name)
        factory = connection.eth.contract(abi=artifact.abi, bytecode=artifact.bytecode)
        tx = factory.constructor().build_transaction(
            {
                "from": issuer.address,
                "nonce": connection.eth.get_transaction_count(issuer.address),
                "gas": 3_000_000,
                "chainId": connection.eth.chain_id,
                "gasPrice": connection.eth.gas_price,
            }
        )
        signed = issuer.sign_transaction(tx)
        receipt = connection.eth.wait_for_transaction_receipt(
            connection.eth.send_raw_transaction(signed.raw_transaction)
        )
        addresses[name] = receipt["contractAddress"]

    return {"web3": connection, "account": issuer, "addresses": addresses}


@pytest.fixture
def chain_configured(evm_chain, settings, monkeypatch):
    """Point `chain.client` at the in-memory EVM for the duration of one test."""
    from chain import client

    settings.WEB3_RPC_URL = "memory://eth-tester"
    settings.WEB3_PRIVATE_KEY = "0x" + "11" * 32
    settings.WEB3_CHAIN_ID = evm_chain["web3"].eth.chain_id
    settings.DEED_CONTRACT_ADDRESS = evm_chain["addresses"]["PropertyDeed"]
    settings.SHARES_CONTRACT_ADDRESS = evm_chain["addresses"]["PropertyShares"]

    monkeypatch.setattr(client, "web3", lambda: evm_chain["web3"])
    monkeypatch.setattr(client, "account", lambda: evm_chain["account"])
    return evm_chain
