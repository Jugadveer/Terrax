"""
Push everything the database knows to the contracts, and report what moved.

This runs off the request path on purpose. Recording a deed takes one
transaction and a testnet takes seconds to mine it, so doing this while someone
waits for a page would make publishing a listing feel broken. Run it after a
seed, on a schedule, or by hand.

Every step is idempotent. A deed already recorded is re-checked, not
re-recorded; a pool already open is left alone; a holder whose on-chain balance
already matches the ledger is skipped. Running this twice does nothing the
second time, which is the property that makes it safe to put on a timer.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from chain import client
from chain import services as chain
from market.models import Holding
from properties.constants import PUBLIC_STATUSES
from properties.models import Listing


class Command(BaseCommand):
    help = "Record published listings, open share pools, and issue shares on chain."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=0, help="Stop after N listings.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be sent, send nothing."
        )
        parser.add_argument("--listing", help="Sync one listing by public id.")

    def handle(self, *args, **options) -> None:
        if not client.is_configured():
            raise CommandError(
                "No chain configured. Set WEB3_RPC_URL and WEB3_PRIVATE_KEY in .env.\n"
                "Without them the site still runs and every record stays local."
            )
        if not client.has_contracts():
            raise CommandError(
                "No contract addresses. Run: python manage.py deploy_contracts"
            )

        listings = Listing.objects.filter(status__in=PUBLIC_STATUSES).order_by("pk")
        if options["listing"]:
            listings = listings.filter(public_id=options["listing"])
        if options["limit"]:
            listings = listings[: options["limit"]]

        self.stdout.write(
            f"{client.chain_name()} | issuer {client.address()} "
            f"| balance {client.balance_eth():.4f}\n"
        )

        counts = {"recorded": 0, "pools": 0, "issues": 0, "skipped": 0, "failed": 0}
        for listing in listings:
            try:
                self._sync_one(listing, counts, dry_run=options["dry_run"])
            except Exception as exc:  # noqa: BLE001 - one bad listing must not stop the run
                counts["failed"] += 1
                self.stderr.write(self.style.ERROR(f"  {listing.title[:40]}: {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{counts['recorded']} deeds recorded, {counts['pools']} pools opened, "
                f"{counts['issues']} share issues, {counts['skipped']} already current, "
                f"{counts['failed']} failed."
            )
        )

    # -- one listing --------------------------------------------------------

    def _sync_one(self, listing: Listing, counts: dict[str, int], *, dry_run: bool) -> None:
        token = getattr(listing, "token", None)
        state = chain.verify(listing)

        if state["checked"] and state["matches"]:
            self._sync_shares(listing, counts, dry_run=dry_run)
            counts["skipped"] += 1
            return

        action = "update" if token and token.chain_token_id else "record"
        if dry_run:
            self.stdout.write(f"  would {action}  {listing.title[:44]}")
            counts["recorded"] += 1
            return

        token = chain.record_on_chain(listing)
        counts["recorded"] += 1
        self.stdout.write(
            f"  {action:<6} #{token.chain_token_id:<4} {listing.title[:34]:<36} {token.short_tx}"
        )
        self._sync_shares(listing, counts, dry_run=dry_run)

    def _sync_shares(self, listing: Listing, counts: dict[str, int], *, dry_run: bool) -> None:
        """Open the pool if it is not open, then top up each holder's balance."""
        if not listing.fractional_enabled or not listing.total_shares:
            return

        token = listing.token
        if not token.shares_opened_at:
            if dry_run:
                self.stdout.write(f"  would open pool of {listing.total_shares:,}")
                counts["pools"] += 1
                return
            chain.open_share_pool(listing)
            counts["pools"] += 1
            self.stdout.write(f"  pool   {listing.total_shares:,} shares opened")

        for holding in Holding.objects.filter(listing=listing).select_related("user__profile"):
            wallet = getattr(holding.user, "profile", None)
            wallet = wallet.wallet_address if wallet else ""
            if not wallet:
                continue

            already = 0 if dry_run else chain.onchain_shares(listing, wallet=wallet)
            owed = holding.shares - already
            if owed <= 0:
                continue

            if dry_run:
                self.stdout.write(f"  would issue {owed:,} to {wallet[:10]}...")
            else:
                chain.issue_shares(listing, wallet=wallet, amount=owed)
                self.stdout.write(f"  issue  {owed:,} shares to {wallet[:10]}...")
            counts["issues"] += 1
