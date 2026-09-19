"""Risk scoring and the automated document checks."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import KycStatus
from intelligence import services as intel
from intelligence.engine import documents, risk
from properties.constants import DocumentKind, OwnershipType, VerificationState
from properties.models import ListingDocument
from tests.conftest import make_listing

pytestmark = pytest.mark.django_db


# --- Risk -----------------------------------------------------------------


def test_a_complete_listing_scores_low(documented_listing):
    intel.check_listing_documents(documented_listing)

    result = risk.assess(documented_listing)

    assert result.band == "low"
    assert result.score < 20


def test_missing_documents_dominate_the_score(listing):
    result = risk.assess(listing)

    top = result.factors[0]
    assert top["label"] == "Document completeness"
    assert top["points"] == pytest.approx(24, abs=0.1)
    assert "title deed" in top["detail"].lower()


def test_an_unverified_seller_raises_the_score(documented_listing):
    intel.check_listing_documents(documented_listing)
    before = risk.assess(documented_listing).score

    documented_listing.owner.profile.kyc_status = KycStatus.NOT_STARTED
    documented_listing.owner.profile.save()
    after = risk.assess(documented_listing).score

    assert after > before


def test_power_of_attorney_title_is_flagged(listing):
    listing.ownership_type = OwnershipType.POWER_OF_ATTORNEY
    listing.save()

    labels = {f["label"]: f for f in risk.assess(listing).factors}

    assert labels["Title"]["points"] == pytest.approx(10, abs=0.1)
    assert "power of attorney" in labels["Title"]["detail"].lower()


def test_overpricing_against_the_valuation_is_penalised(documented_listing, comparable_market):
    intel.check_listing_documents(documented_listing)
    valuation = intel.refresh_valuation(documented_listing, with_narrative=False)

    documented_listing.asking_price = valuation.estimate * Decimal("1.6")
    documented_listing.save()

    labels = {f["label"]: f for f in risk.assess(documented_listing, valuation).factors}

    assert labels["Price against valuation"]["points"] > 5
    assert "above the independent estimate" in labels["Price against valuation"]["detail"]


def test_every_factor_carries_its_reason(documented_listing):
    for factor in risk.assess(documented_listing).factors:
        assert factor["detail"]
        assert 0 <= factor["severity"] <= 100


def test_the_score_is_bounded(listing):
    listing.owner.profile.kyc_status = KycStatus.REJECTED
    listing.owner.profile.save()
    listing.ownership_type = OwnershipType.POWER_OF_ATTORNEY
    listing.description = ""
    listing.save()

    assert 0 <= risk.assess(listing).score <= 100


# --- Documents ------------------------------------------------------------


def _document(listing, data: bytes, name: str = "deed.pdf", **kwargs) -> ListingDocument:
    return ListingDocument.objects.create(
        listing=listing,
        kind=kwargs.pop("kind", DocumentKind.TITLE_DEED),
        file=SimpleUploadedFile(name, data, content_type="application/pdf"),
        **kwargs,
    )


def test_a_real_pdf_passes(listing, pdf_bytes):
    report = documents.inspect(_document(listing, pdf_bytes))

    assert report.state == VerificationState.PASSED
    assert not report.failed


def test_a_renamed_executable_fails(listing):
    report = documents.inspect(_document(listing, b"MZ\x90\x00" + b"\x00" * 40000))

    assert report.state == VerificationState.FAILED
    assert any(c["name"] == "File format" for c in report.failed)


def test_a_tiny_scan_is_flagged(listing):
    report = documents.inspect(_document(listing, b"%PDF-1.4\n" + b"x" * 200))

    assert report.state == VerificationState.FLAGGED
    assert any("truncated" in c["detail"] for c in report.failed)


def test_an_expired_document_fails(listing, pdf_bytes):
    document = _document(
        listing, pdf_bytes, kind=DocumentKind.UTILITY_BILL,
        expires_on=timezone.localdate() - timedelta(days=5),
    )

    report = documents.inspect(document)

    assert report.state == VerificationState.FAILED
    assert any("Expired" in c["detail"] for c in report.failed)


def test_a_document_expiring_soon_is_flagged(listing, pdf_bytes):
    document = _document(
        listing, pdf_bytes, kind=DocumentKind.UTILITY_BILL,
        expires_on=timezone.localdate() + timedelta(days=20),
    )

    assert documents.inspect(document).state == VerificationState.FLAGGED


def test_the_same_file_on_two_listings_is_flagged(seller, pdf_bytes):
    first = make_listing(seller, title="First")
    second = make_listing(seller, title="Second")

    _document(first, pdf_bytes)
    duplicate = _document(second, pdf_bytes)

    report = documents.inspect(duplicate)

    assert report.state == VerificationState.FLAGGED
    assert any("already attached to a different listing" in c["detail"] for c in report.failed)


def test_the_same_file_twice_on_one_listing_is_fine(listing, pdf_bytes):
    _document(listing, pdf_bytes)
    again = _document(listing, pdf_bytes, name="deed-copy.pdf")

    assert documents.inspect(again).state == VerificationState.PASSED


def test_checking_writes_the_result_to_the_document(listing, pdf_bytes):
    document = _document(listing, pdf_bytes)

    intel.check_document(document)
    document.refresh_from_db()

    assert document.verification == VerificationState.PASSED
    assert document.verification_notes
    assert document.checksum
