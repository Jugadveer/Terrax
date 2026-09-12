"""
Comparable-sales valuation.

The method is the one a human valuer uses: find properties that are genuinely
similar, work out what they cost per square foot, then adjust for the ways this
property differs from them. Every step is recorded so the final number can be
defended rather than merely displayed.

No network calls and no model weights. The language model, when configured,
writes prose about this result; it never produces the number.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from properties.constants import PUBLIC_STATUSES, PropertyType

#: Minimum comparables before the estimate is considered better than a guess.
MIN_COMPARABLES = 3
#: More than this adds noise rather than signal.
MAX_COMPARABLES = 8

#: Fallback rates in rupees per square foot, used only when a city has too few
#: listings to compare against. Deliberately coarse, and always reported as low
#: confidence so nobody mistakes it for a real comparison.
FALLBACK_RATE_PER_SQFT = {
    PropertyType.APARTMENT: Decimal("7200"),
    PropertyType.VILLA: Decimal("9500"),
    PropertyType.PLOT: Decimal("4200"),
    PropertyType.COMMERCIAL: Decimal("11500"),
    PropertyType.RETAIL: Decimal("13000"),
    PropertyType.WAREHOUSE: Decimal("2000"),
    PropertyType.FARMLAND: Decimal("180"),
}
DEFAULT_RATE_PER_SQFT = Decimal("6500")

#: The city index is a residential signal. Applying it to land and sheds, whose
#: prices do not track flat prices, distorts more than it corrects.
CITY_INDEXED_TYPES = (
    PropertyType.APARTMENT,
    PropertyType.VILLA,
    PropertyType.COMMERCIAL,
    PropertyType.RETAIL,
)


@dataclass
class Comparable:
    """One property used in the comparison, with why it was trusted."""

    listing_id: int
    public_id: str
    title: str
    location: str
    area_sqft: float
    price: float
    rate_per_sqft: float
    similarity: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "public_id": self.public_id,
            "title": self.title,
            "location": self.location,
            "area_sqft": round(self.area_sqft),
            "price": round(self.price),
            "rate_per_sqft": round(self.rate_per_sqft),
            "similarity": round(self.similarity, 3),
            "weight_percent": 0.0,  # filled in once the whole set is known
            "reasons": self.reasons,
        }


@dataclass
class Adjustment:
    label: str
    percent: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "percent": round(self.percent, 2),
            "reason": self.reason,
        }


@dataclass
class ValuationResult:
    estimate: Decimal
    low: Decimal
    high: Decimal
    rate_per_sqft: Decimal
    confidence: str
    comparables: list[dict]
    adjustments: list[dict]
    method: str

    @property
    def spread_percent(self) -> float:
        if not self.estimate:
            return 0.0
        return float((self.high - self.low) / self.estimate * 100)


def value_listing(listing) -> ValuationResult:
    """Produce a defensible estimate for one listing."""
    area = float(listing.area_sqft or 0)
    if area <= 0:
        return _unvaluable(listing)

    comparables = _find_comparables(listing)

    if len(comparables) >= MIN_COMPARABLES:
        base_rate, dispersion = _blended_rate(comparables)
        method = "comparable-sales"
    else:
        base_rate = _fallback_rate(listing)
        dispersion = 0.18
        method = "regional-benchmark"

    adjustments = _adjustments(listing, comparables)
    multiplier = 1.0
    for adjustment in adjustments:
        multiplier *= 1 + adjustment.percent / 100

    adjusted_rate = base_rate * multiplier
    estimate = adjusted_rate * area

    # The interval widens with comparable scatter and narrows with sample size.
    spread = _interval_width(dispersion, len(comparables))
    confidence = _confidence(len(comparables), spread)

    payload = [c.as_dict() for c in comparables]
    total_weight = sum(c.similarity for c in comparables) or 1.0
    for row, comparable in zip(payload, comparables, strict=True):
        row["weight_percent"] = round(comparable.similarity / total_weight * 100, 1)

    return ValuationResult(
        estimate=_money(estimate),
        low=_money(estimate * (1 - spread)),
        high=_money(estimate * (1 + spread)),
        rate_per_sqft=_money(adjusted_rate),
        confidence=confidence,
        comparables=payload,
        adjustments=[a.as_dict() for a in adjustments],
        method=method,
    )


# ---------------------------------------------------------------------------
# Comparable selection
# ---------------------------------------------------------------------------


def _find_comparables(listing) -> list[Comparable]:
    """
    Widen the net until there is enough to work with: same city first, then the
    same state, then the same property type anywhere. Each widening costs
    similarity, which is what pushes confidence down.
    """
    from properties.models import Listing

    base = (
        Listing.objects.filter(status__in=PUBLIC_STATUSES)
        .exclude(pk=listing.pk)
        .exclude(asking_price__isnull=True)
        .exclude(area_sqft__isnull=True)
        .filter(area_sqft__gt=0)
    )

    tiers = [
        base.filter(property_type=listing.property_type, city__iexact=listing.city),
        base.filter(property_type=listing.property_type, state__iexact=listing.state),
        base.filter(property_type=listing.property_type),
    ]

    index = city_rate_index()
    seen: set[int] = set()
    scored: list[Comparable] = []
    for tier_index, queryset in enumerate(tiers):
        for other in queryset[:60]:
            if other.pk in seen:
                continue
            seen.add(other.pk)
            comparable = _score(listing, other, tier_index, index)
            if comparable.similarity > 0.25:
                scored.append(comparable)
        # Stop widening as soon as the current net is enough.
        if len(scored) >= MIN_COMPARABLES:
            break

    scored.sort(key=lambda c: c.similarity, reverse=True)
    return scored[:MAX_COMPARABLES]


def city_rate_index() -> dict[str, float]:
    """
    Median rupees per square foot for each city, used to put a comparable from
    another city on the same footing.

    Without this, a one bedroom flat in Bandra gets compared against a flat in
    Kothrud and comes out at a third of its value. A valuer would apply a
    location adjustment; this is that adjustment, derived from the market's own
    data rather than from a hard-coded table.
    """
    from django.core.cache import cache

    cached = cache.get("city-rate-index")
    if cached is not None:
        return cached

    from properties.models import Listing

    buckets: dict[str, list[float]] = {}
    rows = (
        Listing.objects.filter(status__in=PUBLIC_STATUSES)
        .exclude(asking_price__isnull=True)
        .exclude(area_sqft__isnull=True)
        .filter(area_sqft__gt=0)
        .values_list("city", "asking_price", "area_sqft")
    )
    for city, price, area in rows:
        if not city:
            continue
        buckets.setdefault(city.lower(), []).append(float(price) / float(area))

    index = {
        city: statistics.median(rates)
        for city, rates in buckets.items()
        if len(rates) >= 2
    }
    cache.set("city-rate-index", index, 600)
    return index


def _score(listing, other, tier_index: int, city_index: dict[str, float]) -> Comparable:
    """
    Similarity in [0, 1]. Size dominates, because price per square foot is only
    meaningful between properties of a comparable size; the rest are modifiers.
    """
    reasons: list[str] = []

    area_a = float(listing.area_sqft or 0)
    area_b = float(other.area_sqft)
    size_ratio = min(area_a, area_b) / max(area_a, area_b) if area_a and area_b else 0
    size_score = size_ratio**1.5
    if size_ratio > 0.85:
        reasons.append("similar size")

    location_score = 0.4
    if listing.locality and other.locality and listing.locality.lower() == other.locality.lower():
        location_score = 1.0
        reasons.append("same locality")
    elif listing.city and other.city and listing.city.lower() == other.city.lower():
        location_score = 0.75
        reasons.append("same city")
    elif listing.state and other.state and listing.state.lower() == other.state.lower():
        location_score = 0.5

    bed_score = 0.6
    if listing.bedrooms and other.bedrooms:
        gap = abs(listing.bedrooms - other.bedrooms)
        bed_score = max(0.0, 1 - gap * 0.3)
        if gap == 0:
            reasons.append("same configuration")

    age_score = 0.6
    if listing.year_built and other.year_built:
        gap = abs(listing.year_built - other.year_built)
        age_score = max(0.0, 1 - gap / 40)
        if gap <= 5:
            reasons.append("built around the same time")

    # A listing published two years ago says less about today's market.
    reference = other.published_at or other.created_at
    months_old = max((timezone.now() - reference).days / 30.0, 0)
    recency_score = math.exp(-months_old / 18)
    if months_old <= 3:
        reasons.append("recent")

    similarity = (
        0.34 * size_score
        + 0.26 * location_score
        + 0.14 * bed_score
        + 0.11 * age_score
        + 0.15 * recency_score
    )
    # Each tier of widening is a real loss of relevance, priced in here.
    similarity *= (1.0, 0.85, 0.65)[min(tier_index, 2)]

    price = float(other.asking_price)
    rate = price / area_b

    # A comparable from a different city is rebased onto this city's price
    # level before its rate is used, the same way a valuer applies a location
    # adjustment. Without it, cheap cities drag expensive ones down.
    here = city_index.get((listing.city or "").lower())
    there = city_index.get((other.city or "").lower())
    indexable = listing.property_type in CITY_INDEXED_TYPES
    if indexable and here and there and abs(here - there) / there > 0.08:
        rate *= here / there
        reasons.append("rebased to local rates")

    return Comparable(
        listing_id=other.pk,
        public_id=str(other.public_id),
        title=other.title or "Untitled listing",
        location=other.location_label,
        area_sqft=area_b,
        price=price,
        rate_per_sqft=rate,
        similarity=round(similarity, 4),
        reasons=reasons[:3],
    )


# ---------------------------------------------------------------------------
# Rate blending
# ---------------------------------------------------------------------------


def _blended_rate(comparables: list[Comparable]) -> tuple[float, float]:
    """
    Similarity-weighted mean of the comparable rates, with outliers trimmed.

    A weighted mean is used rather than a median because the weights carry real
    information; the trim is what protects it from one mispriced listing.
    """
    rates = [c.rate_per_sqft for c in comparables]
    if len(rates) >= 5:
        median = statistics.median(rates)
        spread = statistics.pstdev(rates) or median * 0.1
        kept = [
            c for c in comparables if abs(c.rate_per_sqft - median) <= 2.0 * spread
        ]
        comparables = kept or comparables
        rates = [c.rate_per_sqft for c in comparables]

    weights = [c.similarity for c in comparables]
    total = sum(weights) or 1.0
    blended = sum(r * w for r, w in zip(rates, weights, strict=True)) / total

    dispersion = (statistics.pstdev(rates) / blended) if len(rates) > 1 and blended else 0.15
    return blended, min(max(dispersion, 0.04), 0.35)


def _fallback_rate(listing) -> float:
    """
    Used when there were too few comparables.

    Tries the same property type nationally, rebased onto this city's price
    level, before falling back to a flat benchmark. Mixing property types would
    price a warehouse like a flat, which is how the naive version of this goes
    wrong.
    """
    from properties.models import Listing

    same_type = [
        float(price) / float(area)
        for price, area in Listing.objects.filter(
            status__in=PUBLIC_STATUSES, property_type=listing.property_type
        )
        .exclude(pk=listing.pk)
        .exclude(asking_price__isnull=True)
        .exclude(area_sqft__isnull=True)
        .filter(area_sqft__gt=0)
        .values_list("asking_price", "area_sqft")[:60]
    ]

    benchmark = float(
        FALLBACK_RATE_PER_SQFT.get(listing.property_type, DEFAULT_RATE_PER_SQFT)
    )
    if len(same_type) >= 2:
        benchmark = statistics.median(same_type)

    if listing.property_type in CITY_INDEXED_TYPES:
        index = city_rate_index()
        here = index.get((listing.city or "").lower())
        national = statistics.median(index.values()) if index else None
        if here and national:
            benchmark *= here / national

    return benchmark


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


def _adjustments(listing, comparables: list[Comparable]) -> list[Adjustment]:
    """
    Differences between this property and the comparable set, priced as
    percentage moves on the blended rate. Only differences that were actually
    observed produce an adjustment, so the list is never padded.
    """
    out: list[Adjustment] = []

    if listing.year_built:
        age = timezone.now().year - listing.year_built
        if age <= 3:
            out.append(Adjustment("Newly built", 6.0, f"Completed {listing.year_built}"))
        elif age >= 30:
            out.append(Adjustment("Older construction", -8.0, f"{age} years old"))
        elif age >= 18:
            out.append(Adjustment("Ageing construction", -4.0, f"{age} years old"))

    if listing.furnishing == "full":
        out.append(Adjustment("Fully furnished", 4.0, "Furnishing included in price"))
    elif listing.furnishing == "semi":
        out.append(Adjustment("Semi furnished", 1.5, "Partial furnishing included"))

    amenity_count = listing.amenities.count()
    if amenity_count >= 8:
        out.append(
            Adjustment("Amenity rich", 3.5, f"{amenity_count} amenities listed")
        )
    elif amenity_count and amenity_count <= 2:
        out.append(Adjustment("Few amenities", -2.5, f"Only {amenity_count} listed"))

    if listing.facing in ("N", "NE", "E"):
        out.append(
            Adjustment(
                "Favourable aspect", 1.5, f"{listing.get_facing_display()} facing"
            )
        )

    if listing.floor and listing.total_floors and listing.total_floors > 3:
        position = listing.floor / listing.total_floors
        if position >= 0.75:
            out.append(Adjustment("Upper floor", 2.0, f"Floor {listing.floor}"))
        elif listing.floor == 0:
            out.append(Adjustment("Ground floor", -2.0, "Ground floor unit"))

    if listing.ownership_type == "leasehold":
        out.append(Adjustment("Leasehold title", -5.0, "Leasehold rather than freehold"))
    elif listing.ownership_type == "poa":
        out.append(
            Adjustment("Power of attorney", -12.0, "Title held under power of attorney")
        )

    if listing.carpet_area_sqft and listing.area_sqft:
        efficiency = float(listing.carpet_area_sqft) / float(listing.area_sqft)
        if efficiency >= 0.80:
            out.append(
                Adjustment(
                    "Efficient layout",
                    2.5,
                    f"Carpet area is {efficiency:.0%} of built-up",
                )
            )
        elif efficiency <= 0.62:
            out.append(
                Adjustment(
                    "Low carpet ratio",
                    -3.0,
                    f"Carpet area is only {efficiency:.0%} of built-up",
                )
            )

    if not comparables:
        out.append(
            Adjustment(
                "Thin comparable set",
                -3.0,
                "Priced against a regional benchmark rather than nearby sales",
            )
        )

    return out


# ---------------------------------------------------------------------------
# Confidence and formatting
# ---------------------------------------------------------------------------


def _interval_width(dispersion: float, sample_size: int) -> float:
    """Half-width of the interval, as a fraction of the estimate."""
    shrink = 1 / math.sqrt(max(sample_size, 1))
    return min(max(dispersion * (0.6 + 0.9 * shrink), 0.05), 0.28)


def _confidence(sample_size: int, spread: float) -> str:
    if sample_size >= 5 and spread <= 0.12:
        return "high"
    if sample_size >= MIN_COMPARABLES and spread <= 0.20:
        return "moderate"
    return "low"


def _money(value: float) -> Decimal:
    """Round to the nearest thousand rupees. False precision helps nobody."""
    return (Decimal(str(value)) / 1000).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * 1000


def _unvaluable(listing) -> ValuationResult:
    price = Decimal(str(listing.asking_price or 0))
    return ValuationResult(
        estimate=price,
        low=price,
        high=price,
        rate_per_sqft=Decimal("0"),
        confidence="low",
        comparables=[],
        adjustments=[
            Adjustment(
                "No area recorded",
                0.0,
                "Add the built-up area to get a comparable valuation",
            ).as_dict()
        ],
        method="unavailable",
    )
