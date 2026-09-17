"""Compile the Solidity sources and report what came out."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from chain import compiler

#: Every EVM chain rejects a contract larger than this (EIP-170).
SIZE_LIMIT = 24_576


class Command(BaseCommand):
    help = "Compile the contracts in chain/contracts and cache the artifacts."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recompile even when the cached artifact matches the source.",
        )

    def handle(self, *args, **options) -> None:
        self.stdout.write(f"solc {compiler.ensure_solc()}, optimizer on")

        artifacts = compiler.compile_all(force=options["force"])
        for name, artifact in artifacts.items():
            share = artifact.size_bytes / SIZE_LIMIT * 100
            self.stdout.write(
                f"  {name:<16} {artifact.size_bytes:>6,} bytes  "
                f"{share:4.1f}% of the limit  {len(artifact.abi):>2} ABI entries"
            )

        oversized = [name for name, a in artifacts.items() if a.size_bytes > SIZE_LIMIT]
        if oversized:
            self.stderr.write(self.style.ERROR(f"Too large to deploy: {', '.join(oversized)}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Compiled {len(artifacts)} contracts."))
