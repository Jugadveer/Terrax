"""Domain vocabulary for listings. Kept out of models.py so that forms,
services and templates can import the choices without importing the ORM."""

from django.db import models


class PropertyType(models.TextChoices):
    APARTMENT = "apartment", "Apartment"
    VILLA = "villa", "Villa"
    PLOT = "plot", "Plot"
    COMMERCIAL = "commercial", "Commercial"
    RETAIL = "retail", "Retail"
    WAREHOUSE = "warehouse", "Warehouse"
    FARMLAND = "farmland", "Farmland"


class OwnershipType(models.TextChoices):
    FREEHOLD = "freehold", "Freehold"
    LEASEHOLD = "leasehold", "Leasehold"
    COOPERATIVE = "cooperative", "Co-operative society"
    POWER_OF_ATTORNEY = "poa", "Power of attorney"


class Furnishing(models.TextChoices):
    UNFURNISHED = "unfurnished", "Unfurnished"
    SEMI = "semi", "Semi furnished"
    FULL = "full", "Fully furnished"


class ListingStatus(models.TextChoices):
    """
    The real pipeline. Each step is entered by an explicit action, so a
    listing can never appear verified because a template said so.
    """

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under review"
    VERIFIED = "verified", "Verified"
    TOKENIZED = "tokenized", "Tokenized"
    LISTED = "listed", "Listed"
    UNDER_OFFER = "under_offer", "Under offer"
    SOLD = "sold", "Sold"
    REJECTED = "rejected", "Rejected"


#: Statuses a visitor is allowed to see in the public marketplace.
PUBLIC_STATUSES = (
    ListingStatus.VERIFIED,
    ListingStatus.TOKENIZED,
    ListingStatus.LISTED,
    ListingStatus.UNDER_OFFER,
    ListingStatus.SOLD,
)

#: Statuses that can still receive offers.
TRADEABLE_STATUSES = (
    ListingStatus.VERIFIED,
    ListingStatus.TOKENIZED,
    ListingStatus.LISTED,
)

#: How each status should render. Keeps status colour out of the templates.
STATUS_TONE = {
    ListingStatus.DRAFT: "neutral",
    ListingStatus.SUBMITTED: "info",
    ListingStatus.UNDER_REVIEW: "caution",
    ListingStatus.VERIFIED: "accent",
    ListingStatus.TOKENIZED: "accent",
    ListingStatus.LISTED: "gain",
    ListingStatus.UNDER_OFFER: "caution",
    ListingStatus.SOLD: "neutral",
    ListingStatus.REJECTED: "loss",
}


class DocumentKind(models.TextChoices):
    TITLE_DEED = "title_deed", "Title deed"
    TAX_RECEIPT = "tax_receipt", "Property tax receipt"
    UTILITY_BILL = "utility_bill", "Utility bill"
    ENCUMBRANCE = "encumbrance", "Encumbrance certificate"
    APPROVAL = "approval", "Building approval"
    OTHER = "other", "Other"


#: Documents a listing must carry before it can be submitted for review.
REQUIRED_DOCUMENTS = (
    DocumentKind.TITLE_DEED,
    DocumentKind.TAX_RECEIPT,
    DocumentKind.UTILITY_BILL,
)


class VerificationState(models.TextChoices):
    PENDING = "pending", "Pending"
    PASSED = "passed", "Passed"
    FLAGGED = "flagged", "Flagged"
    FAILED = "failed", "Failed"


class Facing(models.TextChoices):
    NORTH = "N", "North"
    NORTH_EAST = "NE", "North east"
    EAST = "E", "East"
    SOUTH_EAST = "SE", "South east"
    SOUTH = "S", "South"
    SOUTH_WEST = "SW", "South west"
    WEST = "W", "West"
    NORTH_WEST = "NW", "North west"


SORT_OPTIONS = {
    "recent": ("Newest first", "-published_at"),
    "price_low": ("Price: low to high", "asking_price"),
    "price_high": ("Price: high to low", "-asking_price"),
    "area": ("Largest area", "-area_sqft"),
    "value": ("Best value vs valuation", "value_gap"),
}
