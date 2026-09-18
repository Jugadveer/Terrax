"""
Load a realistic demonstration market.

Run once after migrating. Everything it creates is real data flowing through
the real code paths: listings go through `submit_for_review` and `publish`,
valuations come from the comparables engine, offers go through the offer
service. Nothing is written straight into a template.

    python manage.py seed_demo --reset
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import KycStatus, Notification, Profile, SavedSearch, WatchlistItem
from accounts.services import notify
from market import services as market
from market.models import Holding
from properties import services as properties_service
from properties.constants import DocumentKind, Furnishing, OwnershipType, PropertyType
from properties.models import Amenity, Listing, ListingDocument, ListingImage, PricePoint

AMENITIES = [
    ("Lift", "arrow-up-right"), ("Covered parking", "cube"), ("Power backup", "lightning"),
    ("Gymnasium", "target"), ("Swimming pool", "globe-hemisphere-east"),
    ("Clubhouse", "buildings"), ("Children's play area", "star"),
    ("24x7 security", "shield-check"), ("Rainwater harvesting", "path"),
    ("Solar water heating", "sun"), ("Piped gas", "lightning"),
    ("Visitor parking", "cube"), ("Landscaped garden", "globe-hemisphere-east"),
    ("Indoor games room", "target"), ("Jogging track", "path"),
    ("Intercom", "phone"), ("Fire safety system", "shield-check"),
    ("Servant quarters", "house-line"),
]

PEOPLE = [
    ("aarav.mehta", "Aarav Mehta", "Chandigarh", "9878241093"),
    ("ishita.rao", "Ishita Rao", "Bengaluru", "9845117362"),
    ("kabir.dsouza", "Kabir D'Souza", "Goa", "9823440187"),
    ("meera.krishnan", "Meera Krishnan", "Kochi", "9847556210"),
    ("rohan.bhatt", "Rohan Bhatt", "Ahmedabad", "9825613478"),
    ("saanvi.iyer", "Saanvi Iyer", "Chennai", "9840227614"),
    ("devansh.sethi", "Devansh Sethi", "Gurugram", "9811047293"),
    ("naina.qureshi", "Naina Qureshi", "Hyderabad", "9866312048"),
    ("aditya.pillai", "Aditya Pillai", "Pune", "9822704516"),
    ("tara.chawla", "Tara Chawla", "Mumbai", "9820165347"),
]

#: Title, locality, city, state, pincode, lat, lng, type, sqft, beds, baths,
#: floor, total floors, year, price, furnishing, ownership, fractional.
LISTINGS = [
    ("Corner flat with two balconies", "Sector 22", "Chandigarh", "Chandigarh", "160022",
     30.7333, 76.7794, PropertyType.APARTMENT, 1480, 3, 2, 4, 6, 2016, 9_850_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Quiet duplex behind the lake", "Sector 9", "Chandigarh", "Chandigarh", "160009",
     30.7460, 76.7840, PropertyType.VILLA, 2650, 4, 4, 0, 2, 2009, 24_500_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, False),
    ("Two bed near the metro line", "Indiranagar", "Bengaluru", "Karnataka", "560038",
     12.9784, 77.6408, PropertyType.APARTMENT, 1150, 2, 2, 7, 14, 2019, 14_200_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Top floor with a roof terrace", "Koramangala", "Bengaluru", "Karnataka", "560034",
     12.9352, 77.6245, PropertyType.APARTMENT, 1720, 3, 3, 11, 11, 2021, 21_900_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, True),
    ("Office floor on the outer ring", "Marathahalli", "Bengaluru", "Karnataka", "560037",
     12.9569, 77.7011, PropertyType.COMMERCIAL, 4200, None, 4, 3, 8, 2015, 48_000_000,
     Furnishing.UNFURNISHED, OwnershipType.LEASEHOLD, True),
    ("Portuguese house with a courtyard", "Assagao", "Goa", "Goa", "403507",
     15.6100, 73.7710, PropertyType.VILLA, 3100, 4, 4, 0, 2, 1974, 38_500_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, True),
    ("Two bed a walk from Anjuna", "Anjuna", "Goa", "Goa", "403509",
     15.5730, 73.7400, PropertyType.APARTMENT, 980, 2, 2, 1, 3, 2018, 11_400_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, True),
    ("Backwater plot with road access", "Kumbalam", "Kochi", "Kerala", "682506",
     9.8890, 76.3180, PropertyType.PLOT, 8700, None, None, None, None, None, 17_400_000,
     Furnishing.UNFURNISHED, OwnershipType.FREEHOLD, False),
    ("Three bed facing the canal", "Panampilly Nagar", "Kochi", "Kerala", "682036",
     9.9580, 76.2990, PropertyType.APARTMENT, 1620, 3, 3, 6, 9, 2017, 13_800_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Row house on a quiet lane", "Bopal", "Ahmedabad", "Gujarat", "380058",
     23.0350, 72.4670, PropertyType.VILLA, 2200, 3, 3, 0, 2, 2013, 12_600_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, False),
    ("Warehouse near the ring road", "Changodar", "Ahmedabad", "Gujarat", "382213",
     22.9000, 72.4500, PropertyType.WAREHOUSE, 14500, None, 2, 0, 1, 2011, 29_000_000,
     Furnishing.UNFURNISHED, OwnershipType.LEASEHOLD, True),
    ("Sea facing two bed", "Besant Nagar", "Chennai", "Tamil Nadu", "600090",
     13.0002, 80.2668, PropertyType.APARTMENT, 1340, 2, 2, 5, 8, 2012, 16_100_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Shop unit on a main road", "T Nagar", "Chennai", "Tamil Nadu", "600017",
     13.0418, 80.2341, PropertyType.RETAIL, 850, None, 1, 0, 4, 2008, 22_800_000,
     Furnishing.UNFURNISHED, OwnershipType.FREEHOLD, True),
    ("Builder floor with a private lift", "DLF Phase 3", "Gurugram", "Haryana", "122010",
     28.4920, 77.0930, PropertyType.APARTMENT, 2450, 4, 4, 3, 4, 2020, 42_500_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, True),
    ("Studio in a serviced block", "Golf Course Road", "Gurugram", "Haryana", "122002",
     28.4430, 77.0990, PropertyType.APARTMENT, 620, 1, 1, 12, 22, 2022, 8_900_000,
     Furnishing.FULL, OwnershipType.LEASEHOLD, True),
    ("Three bed with a study", "Gachibowli", "Hyderabad", "Telangana", "500032",
     17.4400, 78.3480, PropertyType.APARTMENT, 1860, 3, 3, 9, 18, 2020, 15_700_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Independent house on 300 yards", "Jubilee Hills", "Hyderabad", "Telangana", "500033",
     17.4310, 78.4070, PropertyType.VILLA, 3600, 5, 5, 0, 3, 2006, 62_000_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, False),
    ("Two bed near the university", "Kothrud", "Pune", "Maharashtra", "411038",
     18.5070, 73.8070, PropertyType.APARTMENT, 1080, 2, 2, 3, 7, 2014, 8_650_000,
     Furnishing.SEMI, OwnershipType.FREEHOLD, True),
    ("Duplex with a terrace garden", "Baner", "Pune", "Maharashtra", "411045",
     18.5590, 73.7770, PropertyType.APARTMENT, 2140, 3, 3, 10, 11, 2021, 19_400_000,
     Furnishing.FULL, OwnershipType.FREEHOLD, True),
    ("Farmland off the expressway", "Mulshi", "Pune", "Maharashtra", "412108",
     18.5210, 73.5060, PropertyType.FARMLAND, 43560, None, None, None, None, None, 9_800_000,
     Furnishing.UNFURNISHED, OwnershipType.FREEHOLD, False),
    ("One bed off Carter Road", "Bandra West", "Mumbai", "Maharashtra", "400050",
     19.0596, 72.8295, PropertyType.APARTMENT, 640, 1, 1, 8, 12, 2011, 26_500_000,
     Furnishing.FULL, OwnershipType.COOPERATIVE, True),
    ("Two bed in a redeveloped block", "Andheri West", "Mumbai", "Maharashtra", "400058",
     19.1364, 72.8296, PropertyType.APARTMENT, 1120, 2, 2, 14, 21, 2019, 31_200_000,
     Furnishing.SEMI, OwnershipType.COOPERATIVE, True),
    ("Commercial unit in a business park", "Lower Parel", "Mumbai", "Maharashtra", "400013",
     18.9960, 72.8300, PropertyType.COMMERCIAL, 2800, None, 3, 6, 15, 2017, 78_000_000,
     Furnishing.UNFURNISHED, OwnershipType.LEASEHOLD, True),
    ("Ground floor with a garden strip", "Sector 15", "Chandigarh", "Chandigarh", "160015",
     30.7600, 76.7700, PropertyType.APARTMENT, 1250, 2, 2, 0, 4, 2005, 7_400_000,
     Furnishing.UNFURNISHED, OwnershipType.FREEHOLD, False),
]

DESCRIPTIONS = {
    PropertyType.APARTMENT: (
        "The flat sits on the {floor} floor of a {age} year old block with a lift "
        "and covered parking. Living room and kitchen run along the front, with "
        "the bedrooms set back from the road. {extra}"
    ),
    PropertyType.VILLA: (
        "An independent house on its own plot, built in {year} and maintained "
        "since. Ground floor has the living and dining rooms with the kitchen "
        "behind; the bedrooms are upstairs. {extra}"
    ),
    PropertyType.PLOT: (
        "A cleared plot with direct road access and boundary walls already in "
        "place. Corner position, and services run to the edge of the site. {extra}"
    ),
    PropertyType.COMMERCIAL: (
        "A full floor fitted for office use, with its own reception area and a "
        "dedicated service lift. Power backup covers the whole floor. {extra}"
    ),
    PropertyType.RETAIL: (
        "A shop unit at street level with a glazed frontage and a rear storage "
        "room. Steady footfall on this stretch through the week. {extra}"
    ),
    PropertyType.WAREHOUSE: (
        "A clear span shed with a loading bay and space for two containers in "
        "the yard. Height to the eaves is around seven metres. {extra}"
    ),
    PropertyType.FARMLAND: (
        "An acre of level agricultural land with a borewell and an approach road "
        "wide enough for a truck. Currently under seasonal cultivation. {extra}"
    ),
}

EXTRAS = [
    "Sold with the fittings shown in the photographs.",
    "The society has approved the transfer in principle.",
    "Property tax is paid to date and the receipts are attached.",
    "Vacant, so possession can follow registration immediately.",
    "Currently let, with the tenancy ending this quarter.",
    "The owner is willing to complete quickly for the right buyer.",
]

FLOORS = {0: "ground", 1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}


class Command(BaseCommand):
    help = "Load a demonstration market with listings, offers and holdings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Delete existing demo data first."
        )

    def handle(self, *args, **options):
        random.seed(20260917)
        self._pool = self._load_photo_pool()
        self._cursor: dict[str, int] = {}

        if options["reset"]:
            self._reset()

        self._amenities()
        users = self._users()
        listings = self._listings(users)
        self._market(users, listings)
        self._admin()

        # Every valuation is a comparison against the rest of the market, so the
        # ones computed while the market was still half empty are now stale.
        self.stdout.write("Recomputing valuations against the finished market")
        call_command("recompute", "--skip-narrative")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeeded {len(listings)} listings across "
                f"{len({listing.city for listing in listings})} cities, "
                f"{len(users)} accounts.\n"
                "Sign in as  aarav.mehta / demo-basix-2026\n"
                "Admin       admin / demo-basix-2026\n"
            )
        )

    # -- steps ------------------------------------------------------------

    def _reset(self) -> None:
        self.stdout.write("Clearing existing data")
        Listing.objects.all().delete()
        User.objects.exclude(is_superuser=True).delete()
        Amenity.objects.all().delete()

    def _amenities(self) -> None:
        for name, icon in AMENITIES:
            Amenity.objects.get_or_create(
                name=name,
                defaults={"slug": name.lower().replace(" ", "-").replace("'", ""), "icon": icon},
            )
        self.stdout.write(f"Amenities: {Amenity.objects.count()}")

    def _users(self) -> list[User]:
        users = []
        for index, (username, name, city, phone) in enumerate(PEOPLE):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.in", "first_name": name.split()[0]},
            )
            if created:
                user.set_password("demo-basix-2026")
                user.save()

            profile: Profile = user.profile
            profile.display_name = name
            profile.city = city
            profile.phone = phone
            # Most sellers are verified; two are deliberately not, so the risk
            # score has something real to react to.
            profile.kyc_status = (
                KycStatus.VERIFIED if index < 8 else KycStatus.IN_REVIEW
            )
            profile.kyc_reviewed_at = timezone.now() - timedelta(days=index * 3 + 2)
            profile.headline = random.choice(
                [
                    "Selling one property at a time, properly.",
                    "Family holdings, documented and clear.",
                    "Long term investor, mostly residential.",
                    "Builder, three completed projects.",
                ]
            )
            if index % 3 == 0:
                profile.wallet_address = "0x" + "".join(
                    random.choice("0123456789abcdef") for _ in range(40)
                )
            profile.save()
            users.append(user)

        self.stdout.write(f"Accounts: {len(users)}")
        return users

    @transaction.atomic
    def _listings(self, users: list[User]) -> list[Listing]:
        created: list[Listing] = []
        amenities = list(Amenity.objects.all())

        for index, row in enumerate(LISTINGS):
            (title, locality, city, state, pincode, lat, lng, kind, sqft, beds,
             baths, floor, total_floors, year, price, furnishing, ownership,
             fractional) = row

            owner = users[index % len(users)]
            listed_days_ago = random.randint(4, 220)

            listing = Listing.objects.create(
                owner=owner,
                title=title,
                summary=self._summary(kind, beds, sqft, locality, city),
                description=self._description(kind, year, floor),
                property_type=kind,
                ownership_type=ownership,
                furnishing=furnishing,
                facing=random.choice(["N", "NE", "E", "SE", "S", "W"]),
                address_line=f"{random.randint(1, 240)}, {locality}",
                locality=locality,
                city=city,
                state=state,
                pincode=pincode,
                latitude=Decimal(str(round(lat + random.uniform(-0.01, 0.01), 6))),
                longitude=Decimal(str(round(lng + random.uniform(-0.01, 0.01), 6))),
                area_sqft=Decimal(sqft),
                carpet_area_sqft=Decimal(int(sqft * random.uniform(0.62, 0.82)))
                if kind not in (PropertyType.PLOT, PropertyType.FARMLAND) else None,
                bedrooms=beds,
                bathrooms=baths,
                floor=floor,
                total_floors=total_floors,
                year_built=year,
                asking_price=Decimal(price),
                price_negotiable=random.random() > 0.3,
                fractional_enabled=fractional,
                total_shares=random.choice([500, 1000, 2000]) if fractional else 0,
                created_at=timezone.now() - timedelta(days=listed_days_ago),
            )
            listing.amenities.set(random.sample(amenities, k=random.randint(3, 9)))

            self._photos(listing)
            self._documents(listing, index)
            self._price_history(listing, listed_days_ago)

            properties_service.submit_for_review(listing)
            properties_service.publish(listing)

            # Backdate so the market does not look like it appeared today.
            published = timezone.now() - timedelta(days=listed_days_ago)
            Listing.objects.filter(pk=listing.pk).update(
                published_at=published,
                created_at=published,
                view_count=random.randint(12, 940),
            )
            listing.refresh_from_db()

            created.append(listing)
            self.stdout.write(f"  {listing.title} ({listing.city})")

        self.stdout.write(f"Listings: {len(created)}")
        return created

    def _market(self, users: list[User], listings: list[Listing]) -> None:
        """Offers, share purchases, watchlists and saved searches."""
        fractional = [listing for listing in listings if listing.fractional_enabled]

        for buyer in users:
            for listing in random.sample(
                [item for item in fractional if item.owner_id != buyer.pk],
                k=min(3, len(fractional)),
            ):
                try:
                    market.buy_shares(
                        listing=listing,
                        buyer=buyer,
                        shares=random.choice([8, 15, 25, 40, 60, 120]),
                    )
                except ValueError:
                    continue

        # A couple of holders take money off the table, so the demo exercises
        # the sell path and not only the buy path. Without this the ledger's
        # secondary side is never written and a bug there stays invisible.
        for holding in Holding.objects.filter(shares__gte=16).order_by("?")[:3]:
            market.sell_shares(
                listing=holding.listing, seller=holding.user, shares=holding.shares // 3
            )

        for buyer in users[:6]:
            for listing in random.sample(
                [item for item in listings if item.owner_id != buyer.pk], k=2
            ):
                try:
                    offer = market.make_offer(
                        listing=listing,
                        buyer=buyer,
                        amount=(listing.asking_price * Decimal(
                            str(round(random.uniform(0.82, 0.99), 3))
                        )).quantize(Decimal("1")),
                        message=random.choice(
                            [
                                "Ready to complete within the month if the documents check out.",
                                "Funds are in place. Happy to meet the seller first.",
                                "Offer holds for a week. Open to discussing the fittings.",
                                "",
                            ]
                        ),
                    )
                    if random.random() < 0.3:
                        market.counter_offer(
                            offer=offer,
                            actor=listing.owner,
                            amount=(offer.amount * Decimal("1.04")).quantize(Decimal("1")),
                            message="Close. This is where I can meet you.",
                        )
                except Exception:
                    continue

        for user in users:
            for listing in random.sample(
                [item for item in listings if item.owner_id != user.pk], k=4
            ):
                WatchlistItem.objects.get_or_create(
                    user=user,
                    listing=listing,
                    defaults={"price_at_add": listing.asking_price * Decimal("1.02")},
                )

        SavedSearch.objects.get_or_create(
            user=users[0],
            name="Pune, under 2 crore",
            defaults={"query": {"city": "Pune", "price_max": "20000000", "sort": "recent"}},
        )
        SavedSearch.objects.get_or_create(
            user=users[1],
            name="Fractional, three bed",
            defaults={"query": {"fractional": "on", "bedrooms": "3"}},
        )

        notify(
            users[0],
            kind="system",
            title="Welcome back",
            body="Three listings you watch have moved since your last visit.",
            url="/properties/",
        )

        self._price_drift(listings)
        self._backdate()
        self.stdout.write("Offers, holdings, watchlists and saved searches loaded")

    def _price_drift(self, listings: list[Listing]) -> None:
        """
        Move some asking prices after the purchases have settled.

        A market where nothing has moved since anyone bought shows a portfolio
        of zeroes and a watchlist with nothing to report. Running this after the
        trades means the cost basis and the current value genuinely differ.
        """
        for listing in random.sample(listings, k=len(listings) // 2):
            factor = Decimal(str(round(random.uniform(0.88, 1.16), 3)))
            listing.asking_price = (listing.asking_price * factor).quantize(Decimal("1"))
            listing.save(update_fields=["asking_price", "updated_at"])
            properties_service.record_price(
                listing,
                listing.asking_price,
                "Price reduced" if factor < 1 else "Price revised",
            )

    def _backdate(self) -> None:
        """
        Spread the activity over the last few weeks.

        Everything above is created in one pass, which leaves a dashboard whose
        every row reads "just now". A demonstration market that all happened in
        the same second does not look like a market.
        """
        import random as rnd

        from market.models import LedgerEntry, Offer, OfferEvent, Trade

        def shift(queryset, field: str, span: int) -> None:
            for row in queryset:
                when = timezone.now() - timedelta(
                    days=rnd.randint(0, span), hours=rnd.randint(0, 23)
                )
                type(row).objects.filter(pk=row.pk).update(**{field: when})

        shift(Offer.objects.all(), "created_at", 18)
        for offer in Offer.objects.all():
            # The reply lands somewhere between the offer and now, never after
            # it, which would render as "just now" on a week-old negotiation.
            answered = offer.created_at + timedelta(hours=rnd.randint(1, 60))
            Offer.objects.filter(pk=offer.pk).update(
                updated_at=min(answered, timezone.now() - timedelta(hours=2))
            )
            for index, event in enumerate(offer.events.order_by("pk")):
                OfferEvent.objects.filter(pk=event.pk).update(
                    created_at=offer.created_at + timedelta(hours=index * 9)
                )

        shift(Trade.objects.all(), "created_at", 40)
        for trade in Trade.objects.all():
            LedgerEntry.objects.filter(trade=trade).update(created_at=trade.created_at)

        shift(Notification.objects.all(), "created_at", 12)
        shift(WatchlistItem.objects.all(), "created_at", 30)

        # A listing's last change is when it was published, not when the seeder
        # happened to run, otherwise the whole feed reads "just now".
        for listing in Listing.objects.exclude(published_at__isnull=True):
            Listing.objects.filter(pk=listing.pk).update(
                updated_at=listing.published_at + timedelta(hours=rnd.randint(1, 72))
            )

        # Half the notifications have already been read, so the unread count is
        # a number rather than every row the account has ever received.
        unread = list(Notification.objects.order_by("-created_at")[:9].values_list("pk", flat=True))
        Notification.objects.exclude(pk__in=unread).update(read_at=timezone.now())

    def _admin(self) -> None:
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@example.in", "is_staff": True, "is_superuser": True},
        )
        if created:
            admin.set_password("demo-basix-2026")
            admin.save()
        admin.profile.display_name = "Platform admin"
        admin.profile.kyc_status = KycStatus.VERIFIED
        admin.profile.save()

    # -- content ----------------------------------------------------------

    def _load_photo_pool(self) -> dict[str, list[Path]]:
        """Group the bundled photographs by the filename prefix."""
        folder = Path(settings.BASE_DIR) / "static" / "img" / "seed"
        pool: dict[str, list[Path]] = {}
        for path in sorted(folder.glob("*.jpg")):
            pool.setdefault(path.stem.split("-")[0], []).append(path)
        if not pool:
            self.stdout.write(
                self.style.WARNING("No photographs in static/img/seed; listings will have none.")
            )
        return pool


    def _summary(self, kind, beds, sqft, locality, city) -> str:
        if beds:
            return f"{beds} bedroom {kind} of {sqft:,} sq ft in {locality}, {city}."
        return f"{sqft:,} sq ft {kind} in {locality}, {city}."

    def _description(self, kind, year, floor) -> str:
        template = DESCRIPTIONS[kind]
        age = timezone.now().year - year if year else 0
        return template.format(
            floor=FLOORS.get(floor, f"{floor}th") if floor is not None else "ground",
            age=age,
            year=year or "an earlier decade",
            extra=random.choice(EXTRAS),
        )

    def _photos(self, listing: Listing) -> None:
        """
        Attach photographs from the local pool in `static/img/seed`.

        Local files rather than remote URLs, so the marketplace never waits on
        a third party while a page renders, and the whole project seeds with no
        network connection at all. The exterior shot is chosen to suit the kind
        of property, then interiors follow, so a warehouse does not open with a
        photograph of a kitchen.
        """
        exteriors = {
            PropertyType.APARTMENT: "block",
            PropertyType.VILLA: "house",
            PropertyType.COMMERCIAL: "block",
            PropertyType.RETAIL: "block",
            PropertyType.WAREHOUSE: "industrial",
            PropertyType.PLOT: "plot",
            PropertyType.FARMLAND: "farm",
        }
        outside = exteriors.get(listing.property_type, "house")

        # Bare land has no rooms to photograph, so it gets exteriors throughout.
        if outside in ("plot", "farm"):
            sequence = [outside, outside, outside]
        elif outside == "industrial":
            sequence = ["industrial", "industrial", "block"]
        else:
            inside = ["interior", "kitchen", "interior", "amenity"]
            sequence = [outside] + inside[: random.randint(2, 4)]

        for position, group in enumerate(sequence):
            data = self._pool_photo(group)
            if data is None:
                continue
            image = ListingImage(
                listing=listing,
                order=position,
                is_cover=(position == 0),
                alt_text=f"{listing.title}, photograph {position + 1}",
            )
            image.image.save(
                f"{listing.public_id.hex[:10]}-{position}.jpg",
                ContentFile(data),
                save=False,
            )
            image.save()

    def _pool_photo(self, group: str) -> bytes | None:
        """
        Take the next photograph from this group, round robin.

        A per-group cursor guarantees that consecutive listings never draw the
        same picture until the pool wraps. A modular stride looks cleverer and
        collapses the moment the stride shares a factor with the pool size.
        """
        pool = self._pool.get(group) or self._pool.get("house")
        if not pool:
            return None
        position = self._cursor.get(group, 0)
        self._cursor[group] = position + 1
        return pool[position % len(pool)].read_bytes()

    def _documents(self, listing: Listing, index: int) -> None:
        kinds = [DocumentKind.TITLE_DEED, DocumentKind.TAX_RECEIPT, DocumentKind.UTILITY_BILL]
        if index % 4 == 0:
            kinds.append(DocumentKind.ENCUMBRANCE)

        for kind in kinds:
            document = ListingDocument(
                listing=listing,
                kind=kind,
                original_name=f"{kind}-{listing.public_id.hex[:6]}.pdf",
                expires_on=(
                    timezone.localdate() + timedelta(days=random.randint(40, 900))
                    if kind == DocumentKind.UTILITY_BILL
                    else None
                ),
            )
            # One listing in five gets an unusably small scan, so the checks
            # have something real to flag instead of passing every row.
            size = 5 if (index % 5 == 0 and kind == DocumentKind.UTILITY_BILL) else 42
            document.file.save(
                document.original_name,
                ContentFile(_pdf(f"{kind} for {listing.title}", kilobytes=size)),
                save=False,
            )
            document.save()

    def _price_history(self, listing: Listing, days_ago: int) -> None:
        """
        A few real price points, so the chart on the detail page has something
        to draw and the movement is consistent with the current asking price.
        """
        price = listing.asking_price * Decimal(str(round(random.uniform(1.0, 1.14), 3)))
        points = random.randint(2, 4)
        for step in range(points):
            recorded = timezone.now() - timedelta(
                days=int(days_ago * (points - step) / points)
            )
            PricePoint.objects.create(
                listing=listing,
                price=price.quantize(Decimal("1")),
                note="Listed" if step == 0 else "Price revised",
                recorded_at=recorded,
            )
            price *= Decimal(str(round(random.uniform(0.94, 1.01), 3)))

        PricePoint.objects.create(
            listing=listing,
            price=listing.asking_price,
            note="Current asking price",
            recorded_at=timezone.now() - timedelta(days=2),
        )

def _pdf(title: str, kilobytes: int = 42) -> bytes:
    """
    The smallest valid single-page PDF.

    Written by hand rather than pulled from a library, because the seed only
    needs a file that passes the document checks: correct magic bytes, a real
    page, and enough size not to look truncated.
    """
    text = title.replace("(", "").replace(")", "")[:70]
    content = f"BT /F1 16 Tf 60 700 Td ({text}) Tj ET".encode()
    # Padded to a plausible scan size. A few kilobytes would be flagged by the
    # legibility check, which is correct behaviour but would make every seeded
    # listing look broken.
    padding = b"% demonstration document\n" * (kilobytes * 1024 // 25)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    out += padding
    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
