"""
The analysis layer's public surface.

Every function here follows the same rule: compute the answer locally first,
then, if a language model is configured, use it to improve the presentation of
that answer. The model never decides a number, a score, or a filter. If it is
absent, slow, or returns nonsense, the local result is what ships, and the
interface says which one the user is looking at.
"""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from intelligence import prompts
from intelligence.engine import comparables, documents, risk
from intelligence.models import AssistantExchange, RiskAssessment, Valuation
from intelligence.providers import LLMUnavailable, get_provider
from properties.constants import PropertyType

logger = logging.getLogger(__name__)

NARRATIVE_CACHE_SECONDS = 60 * 60 * 24


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def refresh_valuation(listing, *, with_narrative: bool = True) -> Valuation:
    """
    Recompute the valuation and store it.

    Called when a listing is published, when its price or specification
    changes, and on demand from the listing page. Not called during page
    rendering.
    """
    result = comparables.value_listing(listing)

    narrative, provider, model = "", "local", ""
    if with_narrative:
        narrative, provider, model = _valuation_narrative(listing, result)

    valuation = Valuation.objects.create(
        listing=listing,
        estimate=result.estimate,
        low=result.low,
        high=result.high,
        price_per_sqft=result.rate_per_sqft or None,
        confidence=result.confidence,
        comparable_count=len(result.comparables),
        comparables=result.comparables,
        adjustments=result.adjustments,
        narrative=narrative,
        provider=provider,
        model_name=model,
    )

    # Copy the headline figures onto the listing. The history stays in the
    # Valuation table; the marketplace sorts and filters against this column.
    listing.valuation_estimate = valuation.estimate
    listing.valuation_confidence = valuation.confidence
    listing.save(update_fields=["valuation_estimate", "valuation_confidence"])

    return valuation


def latest_valuation(listing) -> Valuation | None:
    return listing.valuations.first()


def _valuation_narrative(listing, result) -> tuple[str, str, str]:
    """Prose explaining the estimate. Falls back to a written-out summary."""
    local = _local_narrative(listing, result)

    provider = get_provider()
    if not provider.is_available or result.method == "unavailable":
        return local, "local", ""

    key = _cache_key("valuation-narrative", listing.pk, result.estimate, result.confidence)
    cached = cache.get(key)
    if cached:
        return cached, provider.name, provider.model

    try:
        response = provider.complete(
            prompts.VALUATION_SYSTEM,
            prompts.valuation_user(listing, result),
        )
        text = _clean(response.text)
        if len(text) < 40:
            raise LLMUnavailable("Narrative too short to be useful")
        cache.set(key, text, NARRATIVE_CACHE_SECONDS)
        return text, response.provider, response.model
    except LLMUnavailable as exc:
        logger.info("valuation narrative fell back to local: %s", exc)
        return local, "local", ""


def _local_narrative(listing, result) -> str:
    """
    A readable explanation assembled from the engine's own output. This is what
    ships with no model configured, and it is complete rather than a placeholder.
    """
    if result.method == "unavailable":
        return (
            "There is not enough information to value this property yet. "
            "Adding the built-up area will produce an estimate."
        )

    count = len(result.comparables)
    if count:
        locations = {c["location"] for c in result.comparables}
        where = next(iter(locations)) if len(locations) == 1 else f"{len(locations)} nearby areas"
        opening = (
            f"This estimate comes from {count} comparable "
            f"{'property' if count == 1 else 'properties'} in {where}, "
            f"weighted by how closely each one matches on size, location and age."
        )
    else:
        opening = (
            "There were too few comparable listings nearby, so this figure uses a "
            "regional benchmark rate instead. Treat it as a starting point."
        )

    moves = []
    for adjustment in result.adjustments[:3]:
        direction = "up" if adjustment["percent"] > 0 else "down"
        moves.append(
            f"{adjustment['label'].lower()} moved it {direction} "
            f"{abs(adjustment['percent']):.1f}% ({adjustment['reason'].lower()})"
        )
    middle = ("Adjustments: " + "; ".join(moves) + ".") if moves else ""

    confidence_note = {
        "high": "The comparable prices were tightly clustered, so the range is narrow.",
        "moderate": "The comparable prices varied, which widens the range.",
        "low": "The evidence base is thin, so treat the range as indicative only.",
    }[result.confidence]

    return " ".join(part for part in (opening, middle, confidence_note) if part)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def refresh_risk(listing) -> RiskAssessment:
    result = risk.assess(listing, valuation=latest_valuation(listing))
    assessment, _ = RiskAssessment.objects.update_or_create(
        listing=listing,
        defaults={
            "score": result.score,
            "band": result.band,
            "factors": result.factors,
            "summary": result.summary,
        },
    )
    return assessment


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def check_document(document) -> None:
    """Run the automated checks and write the outcome onto the document."""
    report = documents.inspect(document)
    document.verification = report.state
    document.verification_notes = report.checks
    document.save(update_fields=["verification", "verification_notes"])


def check_listing_documents(listing) -> None:
    for document in listing.documents.all():
        check_document(document)


# ---------------------------------------------------------------------------
# Listing copy
# ---------------------------------------------------------------------------


def draft_description(listing) -> tuple[str, str]:
    """
    Write listing copy from the structured record.

    Returns the text and the provider that produced it, so the interface can be
    honest about which one the user is reading.
    """
    provider = get_provider()
    facts = prompts.listing_facts(listing)

    if provider.is_available:
        key = _cache_key("description", listing.pk, facts)
        cached = cache.get(key)
        if cached:
            return cached, provider.name
        try:
            response = provider.complete(prompts.DESCRIPTION_SYSTEM, facts)
            text = _clean(response.text)
            if len(text) > 60:
                cache.set(key, text, NARRATIVE_CACHE_SECONDS)
                return text, response.provider
        except LLMUnavailable as exc:
            logger.info("description fell back to local: %s", exc)

    return _template_description(listing), "local"


def _template_description(listing) -> str:
    """Deterministic copy built from whatever the record actually contains."""
    kind = listing.get_property_type_display().lower() if listing.property_type else "property"
    bits: list[str] = []

    opening = f"A {kind}"
    if listing.bedrooms:
        opening = f"A {listing.bedrooms} bedroom {kind}"
    if listing.location_label:
        opening += f" in {listing.location_label}"
    if listing.area_sqft:
        opening += f", measuring {listing.area_sqft:,.0f} sq ft"
    bits.append(opening + ".")

    if listing.year_built:
        age = listing.age_years
        bits.append(
            f"Built in {listing.year_built}"
            + (f", so around {age} years old." if age else ".")
        )

    if listing.furnishing:
        bits.append(f"Offered {listing.get_furnishing_display().lower()}.")

    amenity_names = [a.name.lower() for a in listing.amenities.all()[:6]]
    if amenity_names:
        bits.append("Includes " + ", ".join(amenity_names) + ".")

    if listing.ownership_type:
        bits.append(f"Held as {listing.get_ownership_type_display().lower()}.")

    if listing.fractional_enabled and listing.total_shares:
        bits.append(
            f"Ownership is divided into {listing.total_shares:,} shares, so it can be "
            "bought in part rather than whole."
        )

    return " ".join(bits)


# ---------------------------------------------------------------------------
# Grounded question answering
# ---------------------------------------------------------------------------


def answer_question(listing, question: str, user=None) -> AssistantExchange:
    """
    Answer a question about one listing, using only that listing's record.

    The prompt carries the facts and forbids anything outside them, so the model
    cannot invent a school two streets away. When no model is configured, the
    local matcher answers from the same facts.
    """
    question = question.strip()[:400]
    provider = get_provider()

    answer, name, model, grounded = _local_answer(listing, question), "local", "", True

    if provider.is_available:
        try:
            response = provider.complete(
                prompts.ASSISTANT_SYSTEM,
                prompts.assistant_user(listing, question),
            )
            text = _clean(response.text)
            if len(text) > 20:
                answer, name, model = text, response.provider, response.model
        except LLMUnavailable as exc:
            logger.info("assistant fell back to local: %s", exc)

    return AssistantExchange.objects.create(
        listing=listing,
        user=user if (user and user.is_authenticated) else None,
        question=question,
        answer=answer,
        provider=name,
        model_name=model,
        grounded=grounded,
    )


#: Question keyword to (field label, accessor). Ordered, first match wins.
_ANSWER_MAP: list[tuple[tuple[str, ...], str, str]] = [
    (("price", "cost", "how much", "asking"), "asking price", "asking_price"),
    (("size", "area", "sq ft", "sqft", "square"), "area", "area_sqft"),
    (("bedroom", "bhk", "bed"), "bedrooms", "bedrooms"),
    (("bathroom", "bath"), "bathrooms", "bathrooms"),
    (("year", "old", "age", "built"), "year built", "year_built"),
    (("floor",), "floor", "floor"),
    (("furnish",), "furnishing", "furnishing"),
    (("facing", "direction"), "facing", "facing"),
    (("owner", "title", "freehold", "lease"), "ownership", "ownership_type"),
    (("where", "location", "address", "area of", "locality"), "location", "location"),
]


def _local_answer(listing, question: str) -> str:
    """
    Answer from the record by matching the question against known fields.

    Not a language model, and it does not pretend to be. It covers the questions
    people actually ask a listing page, and says so plainly when it cannot help.
    """
    from core.formatting import money, money_exact

    text = question.lower()

    if any(word in text for word in ("valuation", "worth", "overpriced", "fair")):
        valuation = latest_valuation(listing)
        if valuation and listing.asking_price:
            gap = valuation.gap_to_asking
            direction = "above" if gap and gap > 0 else "below"
            return (
                f"The independent estimate is {money(valuation.estimate)}, with a range "
                f"of {money(valuation.low)} to {money(valuation.high)}. The asking price "
                f"of {money(listing.asking_price)} sits {abs(gap or 0):.1f}% {direction} "
                f"that estimate. Confidence is {valuation.get_confidence_display().lower()}, "
                f"based on {valuation.comparable_count} comparable listings."
            )
        return "This listing has not been valued yet."

    if any(word in text for word in ("document", "paper", "deed", "legal")):
        present = [d.get_kind_display() for d in listing.documents.all()]
        missing = listing.missing_documents()
        parts = []
        if present:
            parts.append("On file: " + ", ".join(present) + ".")
        if missing:
            parts.append("Still missing: " + ", ".join(missing) + ".")
        return " ".join(parts) or "No documents have been uploaded for this listing."

    if any(word in text for word in ("risk", "safe", "problem", "concern")):
        assessment = getattr(listing, "risk", None)
        if assessment:
            top = assessment.factors[:2]
            detail = " ".join(f["detail"] + "." for f in top)
            return (
                f"Risk score is {assessment.score} out of 100, which is "
                f"{assessment.get_band_display().lower()}. {detail}"
            )
        return "This listing has not been risk assessed yet."

    if any(word in text for word in ("share", "fraction", "invest", "stake")):
        if listing.fractional_enabled and listing.share_price:
            return (
                f"Ownership is split into {listing.total_shares:,} shares at "
                f"{money(listing.share_price)} each. {listing.shares_available:,} shares "
                f"are still available, which is "
                f"{100 - listing.fraction_sold_percent:.1f}% of the property."
            )
        return "This listing is sold whole. Fractional ownership is not enabled on it."

    if any(word in text for word in ("amenity", "amenities", "facilities", "gym", "pool")):
        names = [a.name for a in listing.amenities.all()]
        return (
            "Amenities listed: " + ", ".join(names) + "."
            if names
            else "No amenities have been listed for this property."
        )

    for keywords, label, field in _ANSWER_MAP:
        if not any(word in text for word in keywords):
            continue
        if field == "location":
            return f"The property is in {listing.location_label}."
        value = getattr(listing, field, None)
        if value in (None, ""):
            return f"The {label} has not been recorded for this listing."
        if field == "asking_price":
            return (
                f"The asking price is {money(value)} "
                f"(₹{money_exact(value)}), and the seller has marked it "
                f"{'negotiable' if listing.price_negotiable else 'firm'}."
            )
        if field == "area_sqft":
            rate = listing.price_per_sqft
            suffix = f", which works out to {money(rate)} per sq ft" if rate else ""
            return f"The built-up area is {value:,.0f} sq ft{suffix}."
        display = getattr(listing, f"get_{field}_display", None)
        return f"The {label} is {display() if display else value}."

    return (
        "That is not something recorded on this listing. The page covers the "
        "specification, documents, valuation and ownership. For anything else, "
        "send the seller a message through the offer panel."
    )


# ---------------------------------------------------------------------------
# Natural-language search
# ---------------------------------------------------------------------------

_PRICE_UNITS = {
    "cr": Decimal("10000000"),
    "crore": Decimal("10000000"),
    "crores": Decimal("10000000"),
    "l": Decimal("100000"),
    "lakh": Decimal("100000"),
    "lakhs": Decimal("100000"),
    "lac": Decimal("100000"),
    "k": Decimal("1000"),
}

_TYPE_WORDS = {
    "flat": PropertyType.APARTMENT,
    "apartment": PropertyType.APARTMENT,
    "villa": PropertyType.VILLA,
    "house": PropertyType.VILLA,
    "bungalow": PropertyType.VILLA,
    "plot": PropertyType.PLOT,
    "land": PropertyType.PLOT,
    "office": PropertyType.COMMERCIAL,
    "commercial": PropertyType.COMMERCIAL,
    "shop": PropertyType.RETAIL,
    "retail": PropertyType.RETAIL,
    "warehouse": PropertyType.WAREHOUSE,
    "godown": PropertyType.WAREHOUSE,
    "farm": PropertyType.FARMLAND,
    "farmland": PropertyType.FARMLAND,
}


def parse_search(text: str) -> dict:
    """
    Turn "3 bhk in pune under 90 lakh with a gym" into filter parameters.

    The local parser handles price, bedrooms, property type, city and the
    fractional flag, which is the great majority of what people type. A model,
    when available, gets a chance first and its output is validated against the
    same keys before being trusted.
    """
    text = (text or "").strip()
    if not text:
        return {}

    local = _parse_search_local(text)

    provider = get_provider()
    if not provider.is_available:
        return local

    try:
        response = provider.complete(
            prompts.SEARCH_SYSTEM, prompts.search_user(text), json_mode=True
        )
        parsed = response.as_json()
        cleaned = _validate_search(parsed)
        # The model only wins where it found something the parser did not.
        return {**cleaned, **local} if local else cleaned
    except (LLMUnavailable, ValueError) as exc:
        logger.info("search parsing fell back to local: %s", exc)
        return local


def _parse_search_local(text: str) -> dict:
    lowered = text.lower()
    out: dict = {}

    bedrooms = re.search(r"(\d+)\s*(?:bhk|bed|bedroom|br)\b", lowered)
    if bedrooms:
        out["bedrooms"] = int(bedrooms.group(1))

    price = re.search(
        r"(?:under|below|less than|upto|up to|max|budget)\s*"
        r"(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lakh|lakhs|lac|k)?",
        lowered,
    )
    if price:
        amount = Decimal(price.group(1))
        unit = _PRICE_UNITS.get(price.group(2) or "", Decimal("1"))
        out["price_max"] = int(amount * unit)

    floor_price = re.search(
        r"(?:over|above|more than|at least|from|min)\s*"
        r"(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lakh|lakhs|lac|k)?",
        lowered,
    )
    if floor_price:
        amount = Decimal(floor_price.group(1))
        unit = _PRICE_UNITS.get(floor_price.group(2) or "", Decimal("1"))
        out["price_min"] = int(amount * unit)

    for word, kind in _TYPE_WORDS.items():
        if re.search(rf"\b{word}s?\b", lowered):
            out["property_type"] = kind.value
            break

    city = re.search(r"\b(?:in|near|around|at)\s+([a-z][a-z\s]{2,24}?)(?:\s+(?:under|below|with|for|over|above)\b|$)", lowered)
    if city:
        out["city"] = city.group(1).strip().title()

    if any(word in lowered for word in ("fraction", "share", "part own", "partial")):
        out["fractional"] = "1"

    # Whatever the parser did not claim becomes free text, so nothing is lost.
    residue = lowered
    for pattern in (r"\d+\s*(?:bhk|bed|bedroom|br)\b", r"(?:under|below|over|above)[^,]*"):
        residue = re.sub(pattern, " ", residue)
    residue = " ".join(w for w in residue.split() if w not in {"in", "near", "with", "a", "the", "for", "at", "around"})
    if out.get("city"):
        residue = residue.replace(out["city"].lower(), " ")
    residue = residue.strip()
    if residue and len(residue) > 2 and not out:
        out["q"] = text
    elif residue and len(residue) > 3:
        out["q"] = residue

    return out


_ALLOWED_SEARCH_KEYS = {
    "q", "city", "property_type", "bedrooms", "price_min", "price_max",
    "area_min", "fractional",
}


def _validate_search(parsed: dict) -> dict:
    """Keep only keys the search form understands, with sane types."""
    out: dict = {}
    for key, value in parsed.items():
        if key not in _ALLOWED_SEARCH_KEYS or value in (None, "", []):
            continue
        if key in {"bedrooms", "price_min", "price_max", "area_min"}:
            try:
                out[key] = int(float(value))
            except (TypeError, ValueError):
                continue
        elif key == "property_type":
            if value in PropertyType.values:
                out[key] = value
        else:
            out[key] = str(value)[:80]
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    """
    Strip the habits models fall into: wrapper quotes, a restated heading, and
    the em-dash, which does not belong in this interface.
    """
    cleaned = (text or "").strip().strip('"')
    cleaned = re.sub(r"^(here(?:'s| is)[^:]*:)\s*", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("—", " - ").replace("–", "-")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _cache_key(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"intel:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def stale_before(hours: int = 24):
    return timezone.now() - timezone.timedelta(hours=hours)
