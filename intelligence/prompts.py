"""
Prompts, kept together so the model's instructions can be reviewed in one place.

Two rules run through all of them. The model is given the facts and told not to
go beyond them, and it is told to explain a number it has been handed rather
than produce one. That is what keeps a hallucinated valuation off the page.
"""

from __future__ import annotations

from core.formatting import money

_HOUSE_STYLE = (
    "Write in plain British English. Short sentences. No marketing language, no "
    "exclamation marks, and never the words elevate, seamless, unleash, "
    "bespoke, nestled or discerning. Do not use em-dashes. Do not use bullet "
    "points unless asked. Do not restate the question."
)

VALUATION_SYSTEM = (
    "You explain property valuations to buyers who are not surveyors.\n"
    "You are given a valuation that has already been calculated from comparable "
    "sales, together with the comparables and the adjustments that produced it.\n"
    "Explain how that figure was reached and what would make it move. "
    "Never state a different figure, and never invent a comparable, a location, "
    "an amenity or a market trend that is not in the input.\n"
    "Three to five sentences, one paragraph.\n" + _HOUSE_STYLE
)

DESCRIPTION_SYSTEM = (
    "You write property listing copy for an Indian marketplace.\n"
    "Use only the facts supplied. If a fact is absent, leave it out rather than "
    "guessing. Do not mention schools, transport, neighbours or future value "
    "unless they appear in the input.\n"
    "Two short paragraphs, around 90 words in total. No headings.\n" + _HOUSE_STYLE
)

ASSISTANT_SYSTEM = (
    "You answer questions about one property listing, using only the record "
    "supplied below.\n"
    "If the answer is not in the record, say that it is not recorded on this "
    "listing and suggest asking the seller. Do not estimate, infer or fill gaps.\n"
    "Two or three sentences.\n" + _HOUSE_STYLE
)

SEARCH_SYSTEM = (
    "You convert a property search written in plain language into filters.\n"
    "Return only a JSON object, with no surrounding text.\n"
    "Allowed keys: q (free text), city, property_type, bedrooms, price_min, "
    "price_max, area_min, fractional.\n"
    "property_type must be one of: apartment, villa, plot, commercial, retail, "
    "warehouse, farmland.\n"
    "Prices are whole rupees: 1 crore is 10000000, 1 lakh is 100000.\n"
    "Omit any key you are not confident about. Never invent a city."
)


def valuation_user(listing, result) -> str:
    lines = [
        "PROPERTY",
        f"  Type: {listing.get_property_type_display() or 'not stated'}",
        f"  Location: {listing.location_label}",
        f"  Built-up area: {listing.area_sqft or 'not stated'} sq ft",
        f"  Bedrooms: {listing.bedrooms or 'not stated'}",
        f"  Year built: {listing.year_built or 'not stated'}",
        f"  Asking price: {money(listing.asking_price) if listing.asking_price else 'not stated'}",
        "",
        "CALCULATED VALUATION (do not change these numbers)",
        f"  Estimate: {money(result.estimate)}",
        f"  Range: {money(result.low)} to {money(result.high)}",
        f"  Rate used: {money(result.rate_per_sqft)} per sq ft",
        f"  Confidence: {result.confidence}",
        f"  Method: {result.method}",
        "",
        f"COMPARABLES USED ({len(result.comparables)})",
    ]
    for comparable in result.comparables[:6]:
        lines.append(
            f"  {comparable['title']}, {comparable['location']}: "
            f"{comparable['area_sqft']:,} sq ft at "
            f"{comparable['rate_per_sqft']:,}/sq ft, "
            f"weight {comparable['weight_percent']}%"
        )
    if not result.comparables:
        lines.append("  none; a regional benchmark rate was used instead")

    lines.append("")
    lines.append("ADJUSTMENTS APPLIED")
    for adjustment in result.adjustments:
        lines.append(
            f"  {adjustment['label']}: {adjustment['percent']:+.1f}% "
            f"({adjustment['reason']})"
        )
    if not result.adjustments:
        lines.append("  none")

    return "\n".join(lines)


def listing_facts(listing) -> str:
    """The structured record, as the description writer receives it."""
    amenities = ", ".join(a.name for a in listing.amenities.all()) or "none listed"
    rows = {
        "Title": listing.title,
        "Type": listing.get_property_type_display() if listing.property_type else None,
        "Location": listing.location_label,
        "Built-up area": f"{listing.area_sqft} sq ft" if listing.area_sqft else None,
        "Carpet area": f"{listing.carpet_area_sqft} sq ft" if listing.carpet_area_sqft else None,
        "Bedrooms": listing.bedrooms,
        "Bathrooms": listing.bathrooms,
        "Floor": f"{listing.floor} of {listing.total_floors}" if listing.floor is not None and listing.total_floors else None,
        "Year built": listing.year_built,
        "Furnishing": listing.get_furnishing_display() if listing.furnishing else None,
        "Facing": listing.get_facing_display() if listing.facing else None,
        "Ownership": listing.get_ownership_type_display() if listing.ownership_type else None,
        "Asking price": money(listing.asking_price) if listing.asking_price else None,
        "Amenities": amenities,
        "Fractional ownership": (
            f"{listing.total_shares:,} shares" if listing.fractional_enabled else "no"
        ),
    }
    return "\n".join(f"{k}: {v}" for k, v in rows.items() if v not in (None, ""))


def assistant_user(listing, question: str) -> str:
    parts = [listing_facts(listing)]

    documents = listing.documents.all()
    if documents:
        parts.append(
            "Documents on file: "
            + ", ".join(f"{d.get_kind_display()} ({d.verification})" for d in documents)
        )
    missing = listing.missing_documents()
    if missing:
        parts.append("Documents missing: " + ", ".join(missing))

    valuation = listing.valuations.first()
    if valuation:
        parts.append(
            f"Independent valuation: {money(valuation.estimate)} "
            f"(range {money(valuation.low)} to {money(valuation.high)}, "
            f"{valuation.confidence} confidence, "
            f"{valuation.comparable_count} comparables)"
        )

    assessment = getattr(listing, "risk", None)
    if assessment:
        parts.append(
            f"Risk score: {assessment.score}/100 ({assessment.band}). "
            f"{assessment.summary}"
        )

    if listing.description:
        parts.append(f"Seller description: {listing.description}")

    parts.append(f"\nQUESTION: {question}")
    return "\n\n".join(parts)


def search_user(text: str) -> str:
    return f'Search: "{text}"\n\nReturn the JSON filter object.'
