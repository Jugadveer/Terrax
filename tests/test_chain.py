"""
The contracts, and the layer that talks to them.

The tests below marked with `chain_configured` run against a real EVM held in
memory: solc compiles `chain/contracts/*.sol` and py-evm executes the bytecode.
Reverts are real reverts and gas is real gas, so what passes here is what the
contract does on a node rather than what a mock was told to say.

The rest cover the state the project actually ships in, which is no chain at
all, because that is the path every reader will run first.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from eth_tester.exceptions import TransactionFailed
from web3.exceptions import ContractLogicError

from chain import client as evm
from chain import compiler
from chain import services as chain
from chain.models import PinRecord, TokenRecord
from intelligence import services as intel
from properties import services as properties_service
from properties.constants import ListingStatus

pytestmark = pytest.mark.django_db


def reverts(reason: str):
    """
    Expect a revert, however the backend chooses to report it.

    web3 raises `ContractLogicError` against a node and eth-tester raises its
    own `TransactionFailed`. The reason string is the same either way, and the
    reason string is the part the contract actually promises.
    """
    return pytest.raises((ContractLogicError, TransactionFailed), match=reason)


# --- Compilation ----------------------------------------------------------


def test_both_contracts_compile_and_fit_on_chain():
    """EIP-170 rejects any contract over 24,576 bytes, on every EVM chain."""
    artifacts = compiler.compile_all()

    assert set(artifacts) == {"PropertyDeed", "PropertyShares"}
    for artifact in artifacts.values():
        assert artifact.bytecode.startswith("0x")
        assert 0 < artifact.size_bytes < 24_576


def test_the_artifact_is_keyed_by_the_source():
    """Editing a contract must invalidate the cached build, or a deploy lies."""
    first = compiler.compile_contract("PropertyDeed")
    again = compiler.compile_contract("PropertyDeed")

    assert first.source_hash == again.source_hash
    assert first.bytecode == again.bytecode


# --- The deed contract ----------------------------------------------------


def test_recording_a_deed_stores_its_evidence(chain_configured):
    deed = evm.deed()
    digest = bytes.fromhex("ab" * 32)

    receipt = evm.send(deed.functions.record(evm.address(), "bafyTEST1", digest))
    token_id = deed.functions.totalMinted().call()

    assert receipt.succeeded
    assert deed.functions.ownerOf(token_id).call() == evm.address()
    assert deed.functions.tokenURI(token_id).call() == "ipfs://bafyTEST1"
    assert deed.functions.verifyEvidence(token_id, digest).call() is True


def test_a_changed_bundle_no_longer_verifies(chain_configured):
    deed = evm.deed()
    evm.send(deed.functions.record(evm.address(), "bafyTEST2", bytes.fromhex("cd" * 32)))
    token_id = deed.functions.totalMinted().call()

    assert deed.functions.verifyEvidence(token_id, bytes.fromhex("ce" * 32)).call() is False


def test_only_the_issuer_can_record_a_property(chain_configured):
    """
    An open mint would let anyone record a property nobody checked, and inherit
    the credibility of the ones that were.
    """
    deed = evm.deed()
    stranger = chain_configured["web3"].eth.accounts[3]

    with reverts("not the issuer"):
        deed.functions.record(stranger, "bafyX", bytes(32)).call({"from": stranger})


def test_a_deed_cannot_be_recorded_without_an_identifier(chain_configured):
    deed = evm.deed()

    with reverts("metadata cid required"):
        deed.functions.record(evm.address(), "", bytes(32)).call({"from": evm.address()})


def test_updating_evidence_replaces_the_hash(chain_configured):
    deed = evm.deed()
    before, after = bytes.fromhex("11" * 32), bytes.fromhex("22" * 32)

    evm.send(deed.functions.record(evm.address(), "bafyOLD", before))
    token_id = deed.functions.totalMinted().call()
    evm.send(deed.functions.updateEvidence(token_id, "bafyNEW", after))

    assert deed.functions.verifyEvidence(token_id, after).call() is True
    assert deed.functions.verifyEvidence(token_id, before).call() is False
    assert deed.functions.tokenURI(token_id).call() == "ipfs://bafyNEW"


def test_a_deed_transfers_to_its_new_holder(chain_configured):
    deed = evm.deed()
    buyer = chain_configured["web3"].eth.accounts[4]

    evm.send(deed.functions.record(evm.address(), "bafyMOVE", bytes(32)))
    token_id = deed.functions.totalMinted().call()
    evm.send(deed.functions.transferFrom(evm.address(), buyer, token_id))

    assert deed.functions.ownerOf(token_id).call() == buyer


def test_a_stranger_cannot_move_someone_elses_deed(chain_configured):
    deed = evm.deed()
    stranger = chain_configured["web3"].eth.accounts[5]

    evm.send(deed.functions.record(evm.address(), "bafyHOLD", bytes(32)))
    token_id = deed.functions.totalMinted().call()

    with reverts("not authorised"):
        deed.functions.transferFrom(evm.address(), stranger, token_id).call(
            {"from": stranger}
        )


def test_the_deed_announces_itself_as_an_erc721(chain_configured):
    deed = evm.deed()

    assert deed.functions.supportsInterface("0x80ac58cd").call() is True  # ERC-721
    assert deed.functions.supportsInterface("0x01ffc9a7").call() is True  # ERC-165
    assert deed.functions.supportsInterface("0xffffffff").call() is False


# --- The shares contract --------------------------------------------------


def _fresh_deed(label: str) -> int:
    """Record a deed and return its token id, so each test gets its own pool."""
    deed = evm.deed()
    evm.send(deed.functions.record(evm.address(), label, bytes(32)))
    return deed.functions.totalMinted().call()


def test_opening_a_pool_fixes_the_supply(chain_configured):
    shares = evm.shares()
    deed_id = _fresh_deed("bafyPOOL1")

    evm.send(shares.functions.open(deed_id, 1000))

    assert shares.functions.supplyOf(deed_id).call() == 1000
    assert shares.functions.unsold(deed_id).call() == 1000


def test_a_pool_cannot_be_reopened_at_a_larger_supply(chain_configured):
    """Raising the supply after people have bought in dilutes them silently."""
    shares = evm.shares()
    deed_id = _fresh_deed("bafyPOOL2")
    evm.send(shares.functions.open(deed_id, 500))

    with reverts("already open"):
        shares.functions.open(deed_id, 5000).call({"from": evm.address()})


def test_issuing_shares_moves_them_out_of_the_pool(chain_configured):
    shares = evm.shares()
    holder = chain_configured["web3"].eth.accounts[6]
    deed_id = _fresh_deed("bafyPOOL3")
    evm.send(shares.functions.open(deed_id, 1000))

    evm.send(shares.functions.issue(deed_id, holder, 250))

    assert shares.functions.balanceOf(holder, deed_id).call() == 250
    assert shares.functions.issuedOf(deed_id).call() == 250
    assert shares.functions.unsold(deed_id).call() == 750


def test_the_pool_cannot_be_oversold(chain_configured):
    shares = evm.shares()
    holder = chain_configured["web3"].eth.accounts[7]
    deed_id = _fresh_deed("bafyPOOL4")
    evm.send(shares.functions.open(deed_id, 100))
    evm.send(shares.functions.issue(deed_id, holder, 100))

    with reverts("not enough shares left"):
        shares.functions.issue(deed_id, holder, 1).call({"from": evm.address()})


def test_redeeming_returns_shares_to_the_pool(chain_configured):
    shares = evm.shares()
    holder = chain_configured["web3"].eth.accounts[8]
    deed_id = _fresh_deed("bafyPOOL5")
    evm.send(shares.functions.open(deed_id, 1000))
    evm.send(shares.functions.issue(deed_id, holder, 400))

    evm.send(shares.functions.redeem(deed_id, holder, 150))

    assert shares.functions.balanceOf(holder, deed_id).call() == 250
    assert shares.functions.unsold(deed_id).call() == 750


def test_nobody_can_redeem_shares_they_do_not_hold(chain_configured):
    shares = evm.shares()
    holder = chain_configured["web3"].eth.accounts[9]
    deed_id = _fresh_deed("bafyPOOL6")
    evm.send(shares.functions.open(deed_id, 100))

    with reverts("not enough shares held"):
        shares.functions.redeem(deed_id, holder, 1).call({"from": evm.address()})


def test_shares_transfer_between_holders(chain_configured):
    shares = evm.shares()
    web3 = chain_configured["web3"]
    first, second = web3.eth.accounts[1], web3.eth.accounts[2]
    deed_id = _fresh_deed("bafyPOOL7")
    evm.send(shares.functions.open(deed_id, 1000))
    evm.send(shares.functions.issue(deed_id, first, 300))

    shares.functions.safeTransferFrom(first, second, deed_id, 100, b"").transact(
        {"from": first}
    )

    assert shares.functions.balanceOf(first, deed_id).call() == 200
    assert shares.functions.balanceOf(second, deed_id).call() == 100
    assert shares.functions.issuedOf(deed_id).call() == 300, "a transfer is not an issue"


def test_the_pool_announces_itself_as_an_erc1155(chain_configured):
    assert evm.shares().functions.supportsInterface("0xd9b67a26").call() is True


# --- The hash -------------------------------------------------------------


def test_the_evidence_hash_does_not_depend_on_key_order():
    """Two people building the same bundle must get the same hash."""
    one = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    two = {"a": 1, "nested": {"x": 1, "y": 2}, "b": 2}

    assert chain.evidence_hash(one) == chain.evidence_hash(two)
    assert len(chain.evidence_hash(one)) == 32


def test_the_evidence_hash_changes_with_the_evidence(documented_listing, comparable_market):
    intel.check_listing_documents(documented_listing)
    before = chain.evidence_hash(chain.build_metadata(documented_listing))

    documented_listing.title = "A different property entirely"
    documented_listing.save()
    after = chain.evidence_hash(chain.build_metadata(documented_listing))

    assert before != after


def test_the_metadata_snapshot_carries_the_evidence(documented_listing, comparable_market):
    intel.check_listing_documents(documented_listing)
    intel.refresh_valuation(documented_listing, with_narrative=False)

    metadata = chain.build_metadata(documented_listing)

    assert metadata["schema"] == "terrax/listing/1"
    assert metadata["id"] == str(documented_listing.public_id)
    assert metadata["valuation"]["method"] == "comparable-sales"
    assert len(metadata["documents"]) == 3
    assert all(document["sha256"] for document in metadata["documents"])


# --- Recording a listing, end to end --------------------------------------


def test_a_listing_is_recorded_on_chain_with_a_real_transaction(
    chain_configured, documented_listing
):
    token = chain.record_on_chain(documented_listing)

    assert token.is_onchain
    assert token.chain_token_id > 0
    assert token.tx_hash.startswith("0x") and len(token.tx_hash) == 66
    assert token.block_number > 0
    assert token.contract_address == evm.deed().address


def test_verification_passes_and_then_fails_when_the_listing_moves(
    chain_configured, documented_listing
):
    """
    The whole point of the arrangement, in one test.

    Record a listing, confirm the chain agrees with the documents on the page,
    change the listing, and watch the agreement break.
    """
    chain.record_on_chain(documented_listing)
    assert chain.verify(documented_listing)["matches"] is True

    documented_listing.asking_price = documented_listing.asking_price + 1
    documented_listing.save()

    result = chain.verify(documented_listing)
    assert result["checked"] is True
    assert result["matches"] is False
    assert result["reason"] == "the listing has changed since it was recorded"


def test_re_recording_updates_rather_than_mints_again(chain_configured, documented_listing):
    first = chain.record_on_chain(documented_listing)
    original_id = first.chain_token_id

    documented_listing.summary = "Revalued after the road widening."
    documented_listing.save()
    second = chain.record_on_chain(documented_listing)

    assert second.chain_token_id == original_id, "a re-valuation is not a second property"
    assert second.tx_hash != first.tx_hash
    assert chain.verify(documented_listing)["matches"] is True
    assert TokenRecord.objects.count() == 1


def test_the_share_pool_matches_the_listing(chain_configured, documented_listing):
    documented_listing.fractional_enabled = True
    documented_listing.total_shares = 2000
    documented_listing.save()

    chain.record_on_chain(documented_listing)
    chain.open_share_pool(documented_listing)

    token = documented_listing.token
    assert token.shares_opened_at is not None
    assert evm.shares().functions.supplyOf(token.chain_token_id).call() == 2000


def test_shares_issued_on_chain_match_the_ledger(chain_configured, documented_listing):
    documented_listing.fractional_enabled = True
    documented_listing.total_shares = 1000
    documented_listing.save()
    chain.record_on_chain(documented_listing)
    chain.open_share_pool(documented_listing)

    wallet = chain_configured["web3"].eth.accounts[5]
    chain.issue_shares(documented_listing, wallet=wallet, amount=120)

    assert chain.onchain_shares(documented_listing, wallet=wallet) == 120


def test_a_pool_cannot_be_opened_before_the_deed_exists(chain_configured, listing):
    chain.publish_to_chain(listing)

    with pytest.raises(evm.ChainError, match="Record the deed"):
        chain.open_share_pool(listing)


# --- With nothing configured, which is how the project ships ---------------


def test_the_chain_is_not_configured_by_default(settings):
    settings.WEB3_RPC_URL = ""
    settings.WEB3_PRIVATE_KEY = ""

    assert evm.is_configured() is False


def test_pinning_is_skipped_without_credentials(listing, settings):
    settings.PINATA_API_KEY = ""
    settings.PINATA_SECRET_API_KEY = ""

    assert chain.is_pinning_configured() is False
    assert chain.pin_listing(listing) is None
    assert not PinRecord.objects.exists()


def test_publishing_succeeds_with_nothing_configured(documented_listing, photo, settings):
    """A missing optional integration must not be able to fail a publication."""
    settings.PINATA_API_KEY = ""
    settings.WEB3_RPC_URL = ""
    from properties.models import ListingImage

    ListingImage.objects.create(listing=documented_listing, image=photo)
    documented_listing.status = ListingStatus.DRAFT
    documented_listing.save()

    properties_service.submit_for_review(documented_listing)
    properties_service.publish(documented_listing)
    documented_listing.refresh_from_db()

    assert documented_listing.status == ListingStatus.LISTED
    assert documented_listing.token.evidence_hash.startswith("0x")
    assert documented_listing.token.is_onchain is False
    assert not documented_listing.pins.exists()


def test_publishing_survives_an_unreachable_pinning_provider(listing, settings):
    settings.PINATA_API_KEY = "key"
    settings.PINATA_SECRET_API_KEY = "secret"

    with patch("chain.services.requests.post", side_effect=OSError("connection refused")):
        result = chain.publish_to_chain(listing)

    assert result["pinned"] is False
    assert result["reason"] == "provider unavailable"
    assert result["token_id"]


def test_a_successful_pin_is_recorded(listing, settings):
    settings.PINATA_API_KEY = "key"
    settings.PINATA_SECRET_API_KEY = "secret"

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"IpfsHash": "bafybeigdyrztestcidvalue0000000000000000000000000"}

    with patch("chain.services.requests.post", return_value=Response()):
        result = chain.publish_to_chain(listing)

    pin = PinRecord.objects.get()
    assert result["pinned"] is True
    assert pin.kind == PinRecord.Kind.METADATA
    assert pin.gateway_url.startswith("https://gateway.pinata.cloud/ipfs/")


def test_an_unrecorded_listing_reports_that_it_is_not_on_a_chain(listing, settings):
    settings.DEED_CONTRACT_ADDRESS = ""
    chain.publish_to_chain(listing)

    result = chain.verify(listing)

    assert result["checked"] is False
    assert result["reason"] == "not recorded on a chain"


def test_a_local_record_carries_no_transaction(listing):
    chain.publish_to_chain(listing)
    token = listing.token

    assert token.tx_hash == ""
    assert token.contract_address == ""
    assert token.chain_token_id is None
    assert token.is_onchain is False
    assert token.explorer_tx_url == ""


def test_the_listing_page_states_that_the_record_is_off_chain(client, listing):
    chain.publish_to_chain(listing)

    body = client.get(listing.get_absolute_url()).content.decode()

    assert "Off chain" in body
    assert "No contract is deployed" in body


# --- The command and the pages --------------------------------------------


def test_sync_refuses_without_a_chain(settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.WEB3_RPC_URL = ""
    settings.WEB3_PRIVATE_KEY = ""

    with pytest.raises(CommandError, match="No chain configured"):
        call_command("sync_chain")


def test_sync_records_every_published_listing_then_does_nothing(
    chain_configured, documented_listing, photo
):
    """
    Idempotence is the property that makes this safe to put on a timer.

    The first run records the listing. The second finds the chain already
    agrees and sends no transaction, which is why a cron entry cannot quietly
    mint the same property forty times.
    """
    from io import StringIO

    from django.core.management import call_command

    from properties.models import ListingImage

    ListingImage.objects.create(listing=documented_listing, image=photo)
    documented_listing.status = ListingStatus.DRAFT
    documented_listing.save()
    properties_service.submit_for_review(documented_listing)
    properties_service.publish(documented_listing)

    first = StringIO()
    call_command("sync_chain", stdout=first)
    assert "1 deeds recorded" in first.getvalue()

    documented_listing.refresh_from_db()
    token_id = documented_listing.token.chain_token_id
    assert documented_listing.token.is_onchain

    second = StringIO()
    call_command("sync_chain", stdout=second)
    assert "0 deeds recorded" in second.getvalue()
    assert "1 already current" in second.getvalue()

    documented_listing.refresh_from_db()
    assert documented_listing.token.chain_token_id == token_id


def test_the_status_page_says_when_nothing_is_configured(client, settings):
    settings.WEB3_RPC_URL = ""
    settings.WEB3_PRIVATE_KEY = ""

    body = client.get("/chain/").content.decode()

    assert "Not configured" in body
    assert "ERC-721" in body and "ERC-1155" in body


def test_the_status_page_names_the_deployed_contracts(client, chain_configured):
    body = client.get("/chain/").content.decode()

    assert evm.deed().address[:10] in body
    assert evm.shares().address[:10] in body


def test_verifying_a_recorded_listing_confirms_the_match(
    client, chain_configured, documented_listing
):
    chain.record_on_chain(documented_listing)

    body = client.get(f"/chain/verify/{documented_listing.public_id}/").content.decode()

    assert "hash to exactly what" in body


def test_verifying_a_changed_listing_reports_the_mismatch(
    client, chain_configured, documented_listing
):
    chain.record_on_chain(documented_listing)
    documented_listing.title = "Renamed after recording"
    documented_listing.save()

    body = client.get(f"/chain/verify/{documented_listing.public_id}/").content.decode()

    assert "has changed since it was recorded" in body


def test_verifying_an_unrecorded_listing_says_so(client, listing, settings):
    settings.DEED_CONTRACT_ADDRESS = ""
    chain.publish_to_chain(listing)

    body = client.get(f"/chain/verify/{listing.public_id}/").content.decode()

    assert "not recorded on a chain" in body


def test_the_listing_page_shows_the_transaction_once_it_is_recorded(
    client, chain_configured, documented_listing, photo
):
    """
    The panel a reader sees after a listing has actually been recorded.

    Off chain it prints a reference number and says so. On chain it prints the
    token, the transaction, the block and the evidence hash, and offers the
    check that compares the page against the contract.
    """
    from properties.models import ListingImage

    ListingImage.objects.create(listing=documented_listing, image=photo)
    documented_listing.status = ListingStatus.LISTED
    documented_listing.save()
    token = chain.record_on_chain(documented_listing)

    body = client.get(documented_listing.get_absolute_url()).content.decode()

    assert "On chain" in body
    assert f"#{token.chain_token_id}" in body
    assert token.short_tx in body
    assert token.short_hash in body
    assert "Check against the contract" in body
    assert "No contract is deployed" not in body
