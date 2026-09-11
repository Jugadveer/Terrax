"""
Upload validation.

Runs on the server, against the bytes. The original build relied on the `accept`
attribute and the `required` flag in the browser, both of which a client
controls and neither of which was ever checked again.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError

IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")
DOCUMENT_MAGIC = IMAGE_MAGIC + (b"%PDF",)


def validate_image(upload):
    """An uploaded photograph: right type, within size, actually an image."""
    _check_size(upload, settings.MAX_IMAGE_BYTES, "Images")
    _check_magic(upload, IMAGE_MAGIC, "Upload a JPEG, PNG or WebP image.")
    return upload


def validate_document(upload):
    """A compliance document: PDF or a scan."""
    _check_size(upload, settings.MAX_DOCUMENT_BYTES, "Documents")
    _check_magic(upload, DOCUMENT_MAGIC, "Upload a PDF, JPEG or PNG file.")
    return upload


def _check_size(upload, limit: int, noun: str) -> None:
    if upload.size > limit:
        raise ValidationError(
            f"{noun} must be under {limit // 1024 // 1024} MB. "
            f"This file is {upload.size / 1024 / 1024:.1f} MB."
        )
    if upload.size == 0:
        raise ValidationError("That file is empty.")


def _check_magic(upload, allowed: tuple[bytes, ...], message: str) -> None:
    """
    Read the leading bytes rather than trusting the extension or the
    browser-supplied content type, both of which are attacker controlled.
    """
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    if not any(head.startswith(magic) for magic in allowed):
        raise ValidationError(message)
