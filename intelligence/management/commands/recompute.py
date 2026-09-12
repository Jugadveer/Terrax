"""
Recompute valuations, risk scores and document checks.

Valuations are comparisons, so every listing's estimate depends on the rest of
the market. Adding listings therefore makes older valuations stale. Run this
after a bulk import, and on a schedule in a deployment.

    python manage.py recompute
    python manage.py recompute --skip-narrative
"""

from __future__ import annotations

from django.core.cache import cache
from django.core.management.base import BaseCommand

from intelligence import services
from properties.constants import PUBLIC_STATUSES
from properties.models import Listing


class Command(BaseCommand):
    help = "Recompute valuations and risk scores for every public listing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-narrative",
            action="store_true",
            help="Do not call the language model for the prose explanation.",
        )
        parser.add_argument("--city", help="Limit to one city.")

    def handle(self, *args, **options):
        # The city rate index is derived from the listings themselves, so it
        # has to be dropped before a bulk recompute or every valuation uses a
        # stale view of the market.
        cache.delete("city-rate-index")
        cache.delete("market-stats")

        listings = Listing.objects.filter(status__in=PUBLIC_STATUSES)
        if options["city"]:
            listings = listings.filter(city__iexact=options["city"])

        total = listings.count()
        for index, listing in enumerate(listings.iterator(), start=1):
            valuation = services.refresh_valuation(
                listing, with_narrative=not options["skip_narrative"]
            )
            services.check_listing_documents(listing)
            risk = services.refresh_risk(listing)
            self.stdout.write(
                f"[{index}/{total}] {listing.title[:40]:42s} "
                f"{valuation.estimate:>14,.0f}  "
                f"{valuation.confidence:9s} risk {risk.score:3d}"
            )

        self.stdout.write(self.style.SUCCESS(f"Recomputed {total} listings."))
