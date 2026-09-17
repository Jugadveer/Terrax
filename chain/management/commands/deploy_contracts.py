"""
Deploy the two contracts and print the addresses to put in `.env`.

Deliberately not automatic. Deployment spends real gas, produces addresses that
other people will rely on, and cannot be undone, so it is a thing a person runs
on purpose rather than a side effect of starting the server.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from chain import client, compiler


class Command(BaseCommand):
    help = "Deploy PropertyDeed and PropertyShares to the configured chain."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options) -> None:
        if not client.is_configured():
            raise CommandError(
                "Set WEB3_RPC_URL and WEB3_PRIVATE_KEY in .env first. "
                "See the chain section of .env.example."
            )

        chain = client.chain_name()
        balance = client.balance_eth()
        estimate = self._estimated_cost()

        self.stdout.write(f"Chain    {chain} ({settings.WEB3_CHAIN_ID})")
        self.stdout.write(f"Issuer   {client.address()}")
        self.stdout.write(f"Balance  {balance:.4f}")
        self.stdout.write(f"Estimate {estimate:.4f} for both contracts")

        if balance < estimate:
            raise CommandError(
                f"Balance is {balance:.4f} and the deployment needs about "
                f"{estimate:.4f}. Top up at https://faucet.polygon.technology "
                "and run this again."
            )

        if not options["yes"]:
            answer = input(f"\nDeploy two contracts to {chain}? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                self.stdout.write("Cancelled.")
                return

        deployed = {}
        for name in ("PropertyDeed", "PropertyShares"):
            self.stdout.write(f"\nDeploying {name}...")
            address, receipt = client.deploy(name)
            deployed[name] = address
            self.stdout.write(
                f"  {address}\n"
                f"  block {receipt.block_number}, {receipt.gas_used:,} gas\n"
                f"  {client.explorer_url('address', address)}"
            )

        self.stdout.write(self.style.SUCCESS("\nAdd these two lines to .env:\n"))
        self.stdout.write(f"DEED_CONTRACT_ADDRESS={deployed['PropertyDeed']}")
        self.stdout.write(f"SHARES_CONTRACT_ADDRESS={deployed['PropertyShares']}")
        self.stdout.write("\nThen run: python manage.py sync_chain")

    def _estimated_cost(self) -> float:
        """
        What this will actually cost, asked of the chain rather than guessed.

        Gas price on a testnet swings by an order of magnitude between quiet
        periods and busy ones, so a hardcoded minimum balance is wrong most of
        the time. Simulating both deployments from an address holding nothing
        costs nothing and gives the real number, with a quarter added for the
        price moving between here and the transaction landing.
        """
        connection = client.web3()
        empty = "0x" + "0" * 39 + "1"
        gas = sum(
            connection.eth.estimate_gas(
                {"from": empty, "data": compiler.compile_contract(name).bytecode}
            )
            for name in ("PropertyDeed", "PropertyShares")
        )
        return float(connection.from_wei(gas * connection.eth.gas_price, "ether")) * 1.25
