"""
Automated document checks.

These are the checks a clerk would do before passing a file to a lawyer: is it
the format it claims to be, is it legible, is it the right size, has it expired,
and has this exact file already been used on another listing. They catch clerical
problems, not fraud, and the interface says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from properties.constants import VerificationState

#: First bytes of the formats accepted, keyed by the label shown to the user.
MAGIC_NUMBERS = {
    b"%PDF": "PDF",
    b"\xff\xd8\xff": "JPEG",
    b"\x89PNG\r\n\x1a\n": "PNG",
    b"RIFF": "WebP",
}

MIN_IMAGE_PIXELS = 700 * 500
MIN_FILE_BYTES = 8 * 1024


@dataclass
class Check:
    name: str
    passed: bool
    severity: str  # "info", "warn", "fail"
    detail: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass
class DocumentReport:
    state: str
    checks: list[dict]

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]


def inspect(document) -> DocumentReport:
    """Run every check against one uploaded document."""
    checks = [
        _format_check(document),
        _size_check(document),
        _legibility_check(document),
        _expiry_check(document),
        _duplicate_check(document),
    ]
    checks = [c for c in checks if c is not None]

    if any(c.severity == "fail" and not c.passed for c in checks):
        state = VerificationState.FAILED
    elif any(c.severity == "warn" and not c.passed for c in checks):
        state = VerificationState.FLAGGED
    else:
        state = VerificationState.PASSED

    return DocumentReport(state=state, checks=[c.as_dict() for c in checks])


def _format_check(document) -> Check:
    """Trust the bytes, not the file extension."""
    try:
        document.file.seek(0)
        head = document.file.read(16)
        document.file.seek(0)
    except Exception:  # noqa: BLE001
        return Check("File format", False, "fail", "The file could not be read")

    detected = next(
        (label for magic, label in MAGIC_NUMBERS.items() if head.startswith(magic)),
        None,
    )
    if detected is None:
        return Check(
            "File format",
            False,
            "fail",
            "Not a PDF or image. Re-upload as PDF, JPEG or PNG.",
        )
    return Check("File format", True, "info", f"Valid {detected}")


def _size_check(document) -> Check:
    size = document.size_bytes or 0
    if size < MIN_FILE_BYTES:
        return Check(
            "File size",
            False,
            "warn",
            f"Only {size / 1024:.0f} KB, which is usually a blank or truncated scan",
        )
    return Check("File size", True, "info", f"{size / 1024 / 1024:.1f} MB")


def _legibility_check(document) -> Check | None:
    """
    For images, a scan below roughly 700x500 will not survive being read. PDFs
    are skipped: page geometry is not a reliable legibility signal.
    """
    name = (document.original_name or document.file.name).lower()
    if name.endswith(".pdf"):
        return None
    try:
        from PIL import Image

        document.file.seek(0)
        with Image.open(document.file) as image:
            width, height = image.size
        document.file.seek(0)
    except Exception:  # noqa: BLE001
        return None

    if width * height < MIN_IMAGE_PIXELS:
        return Check(
            "Legibility",
            False,
            "warn",
            f"Scan is {width}x{height}, too small to read reliably",
        )
    return Check("Legibility", True, "info", f"{width}x{height} pixels")


def _expiry_check(document) -> Check | None:
    if not document.expires_on:
        return None
    today: date = timezone.localdate()
    if document.expires_on < today:
        return Check(
            "Validity",
            False,
            "fail",
            f"Expired on {document.expires_on:%d %b %Y}",
        )
    days = (document.expires_on - today).days
    if days < 60:
        return Check("Validity", False, "warn", f"Expires in {days} days")
    return Check("Validity", True, "info", f"Valid until {document.expires_on:%b %Y}")


def _duplicate_check(document) -> Check | None:
    """
    The same file bytes appearing under two different listings is worth a human
    look. Within one listing it is just a re-upload, so that is ignored.
    """
    if not document.checksum:
        return None

    from properties.models import ListingDocument

    clash = (
        ListingDocument.objects.filter(checksum=document.checksum)
        .exclude(pk=document.pk)
        .exclude(listing_id=document.listing_id)
        .exists()
    )
    if clash:
        return Check(
            "Uniqueness",
            False,
            "warn",
            "This exact file is already attached to a different listing",
        )
    return Check("Uniqueness", True, "info", "Not seen on any other listing")
