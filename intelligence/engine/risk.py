"""
Listing risk scoring.

A weighted sum of things that can actually be checked against the record. The
score is meaningless on its own, so every factor that contributed is returned
with it and shown in the interface. Nothing here guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from properties.constants import REQUIRED_DOCUMENTS, DocumentKind, VerificationState


@dataclass
class Factor:
    label: str
    points: float
    weight: float
    detail: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "points": round(self.points, 1),
            "weight": self.weight,
            "detail": self.detail,
            # Share of this factor's ceiling that was used, for the bar width.
            "severity": round(self.points / self.weight * 100, 1) if self.weight else 0,
        }


@dataclass
class RiskResult:
    score: int
    band: str
    factors: list[dict]
    summary: str


#: Each factor's maximum contribution. They total 100.
WEIGHTS = {
    "documents_missing": 24,
    "documents_flagged": 14,
    "identity": 20,
    "price_deviation": 16,
    "completeness": 10,
    "title": 10,
    "staleness": 6,
}


def assess(listing, valuation=None) -> RiskResult:
    """Score one listing. `valuation` is optional; without it, price risk is skipped."""
    factors = [
        _missing_documents(listing),
        _flagged_documents(listing),
        _identity(listing),
        _price_deviation(listing, valuation),
        _completeness(listing),
        _title_quality(listing),
        _staleness(listing),
    ]
    factors = [f for f in factors if f is not None]

    score = int(round(sum(f.points for f in factors)))
    score = max(0, min(score, 100))

    return RiskResult(
        score=score,
        band=band_for(score),
        factors=[f.as_dict() for f in sorted(factors, key=lambda f: -f.points)],
        summary=_summarise(score, factors),
    )


def band_for(score: int) -> str:
    if score < 20:
        return "low"
    if score < 40:
        return "moderate"
    if score < 65:
        return "elevated"
    return "high"


# ---------------------------------------------------------------------------
# Individual factors
# ---------------------------------------------------------------------------


def _missing_documents(listing) -> Factor:
    present = {doc.kind for doc in listing.documents.all()}
    missing = [k for k in REQUIRED_DOCUMENTS if k not in present]
    weight = WEIGHTS["documents_missing"]
    points = weight * (len(missing) / len(REQUIRED_DOCUMENTS))
    if missing:
        names = ", ".join(DocumentKind(k).label.lower() for k in missing)
        detail = f"Not supplied: {names}"
    else:
        detail = "All required documents are on file"
    return Factor("Document completeness", points, weight, detail)


def _flagged_documents(listing) -> Factor:
    docs = list(listing.documents.all())
    weight = WEIGHTS["documents_flagged"]
    if not docs:
        return Factor("Document checks", weight, weight, "No documents to check")

    failed = sum(1 for d in docs if d.verification == VerificationState.FAILED)
    flagged = sum(1 for d in docs if d.verification == VerificationState.FLAGGED)
    pending = sum(1 for d in docs if d.verification == VerificationState.PENDING)

    penalty = (failed * 1.0 + flagged * 0.55 + pending * 0.25) / len(docs)
    points = min(weight * penalty, weight)

    if failed:
        detail = f"{failed} document(s) failed automated checks"
    elif flagged:
        detail = f"{flagged} document(s) raised a flag"
    elif pending:
        detail = f"{pending} document(s) still awaiting checks"
    else:
        detail = "Every document passed its automated checks"
    return Factor("Document checks", points, weight, detail)


def _identity(listing) -> Factor:
    weight = WEIGHTS["identity"]
    profile = getattr(listing.owner, "profile", None)
    status = getattr(profile, "kyc_status", "not_started")
    points = {
        "verified": 0.0,
        "in_review": weight * 0.4,
        "submitted": weight * 0.55,
        "rejected": weight,
        "not_started": weight * 0.85,
    }.get(status, weight * 0.85)
    detail = {
        "verified": "Seller identity is verified",
        "in_review": "Seller identity is being reviewed",
        "submitted": "Seller identity submitted, not yet reviewed",
        "rejected": "Seller identity check was rejected",
    }.get(status, "Seller has not started identity verification")
    return Factor("Seller identity", points, weight, detail)


def _price_deviation(listing, valuation) -> Factor | None:
    if not valuation or not listing.asking_price or not valuation.estimate:
        return None
    weight = WEIGHTS["price_deviation"]
    gap = float(
        (Decimal(str(listing.asking_price)) - Decimal(str(valuation.estimate)))
        / Decimal(str(valuation.estimate))
        * 100
    )
    over = max(gap, 0)
    # Up to 10% above the estimate is ordinary negotiating room.
    points = min(weight, max(0.0, (over - 10) / 30) * weight)
    if gap > 25:
        detail = f"Asking price is {gap:.0f}% above the independent estimate"
    elif gap > 10:
        detail = f"Asking price is {gap:.0f}% above the estimate"
    elif gap < -15:
        detail = f"Asking price is {abs(gap):.0f}% below the estimate, worth checking why"
        points = weight * 0.25
    else:
        detail = "Asking price sits within the estimated range"
    return Factor("Price against valuation", points, weight, detail)


def _completeness(listing) -> Factor:
    weight = WEIGHTS["completeness"]
    fields = {
        "description": bool(listing.description),
        "area": listing.area_sqft is not None,
        "year built": listing.year_built is not None,
        "ownership type": bool(listing.ownership_type),
        "photographs": listing.images.exists(),
        "map location": listing.latitude is not None,
    }
    missing = [name for name, present in fields.items() if not present]
    points = weight * (len(missing) / len(fields))
    detail = (
        f"Not provided: {', '.join(missing)}" if missing else "Listing record is complete"
    )
    return Factor("Listing detail", points, weight, detail)


def _title_quality(listing) -> Factor:
    weight = WEIGHTS["title"]
    points, detail = {
        "freehold": (0.0, "Freehold title"),
        "leasehold": (weight * 0.35, "Leasehold title, check the remaining term"),
        "cooperative": (weight * 0.45, "Co-operative society share, transfer needs approval"),
        "poa": (weight, "Held under power of attorney, which is not a transfer of title"),
    }.get(listing.ownership_type, (weight * 0.6, "Ownership type not stated"))
    return Factor("Title", points, weight, detail)


def _staleness(listing) -> Factor | None:
    reference = listing.published_at or listing.created_at
    if not reference:
        return None
    weight = WEIGHTS["staleness"]
    days = (timezone.now() - reference).days
    if days < 90:
        return Factor("Time on market", 0.0, weight, f"Listed {days} days ago")
    points = min(weight, weight * (days - 90) / 275)
    return Factor(
        "Time on market",
        points,
        weight,
        f"On the market for {days} days without a sale",
    )


def _summarise(score: int, factors: list[Factor]) -> str:
    band = band_for(score)
    worst = max(factors, key=lambda f: f.points, default=None)
    opening = {
        "low": "Nothing material outstanding.",
        "moderate": "Mostly clean, with some gaps.",
        "elevated": "Several things to resolve before committing.",
        "high": "Significant gaps in the record.",
    }[band]
    if worst and worst.points >= 4:
        return f"{opening} {worst.detail}."
    return opening
