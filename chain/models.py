"""
Where a listing's evidence was published, and where its deed was recorded.

Two rows per listing at most. `PinRecord` says where the evidence bundle lives
on IPFS. `TokenRecord` says which deed represents the property and, once a
chain is configured, which transaction put it there.

Both are written optimistically: a listing publishes whether or not either
integration is reachable, and the columns that would carry a transaction stay
empty rather than being filled with something that looks like one.
"""

from __future__ import annotations

from django.db import models


class PinRecord(models.Model):
    class Kind(models.TextChoices):
        METADATA = "metadata", "Metadata"
        IMAGE = "image", "Image"
        DOCUMENT = "document", "Document"

    listing = models.ForeignKey(
        "properties.Listing", on_delete=models.CASCADE, related_name="pins"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    cid = models.CharField(max_length=120, db_index=True)
    label = models.CharField(max_length=140, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    provider = models.CharField(max_length=20, default="pinata")
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-pinned_at",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.cid}"

    @property
    def gateway_url(self) -> str:
        return f"https://gateway.pinata.cloud/ipfs/{self.cid.removeprefix('ipfs://')}"

    @property
    def short_cid(self) -> str:
        bare = self.cid.removeprefix("ipfs://")
        return f"{bare[:8]}...{bare[-6:]}" if len(bare) > 18 else bare


class TokenRecord(models.Model):
    """
    The deed for one property.

    `token_id` is the human reference this site prints. `chain_token_id` is the
    integer the contract uses, and it stays null until a transaction has
    actually been mined, which is what `is_onchain` reads. Nothing here is
    populated speculatively: an empty `tx_hash` means no transaction was sent,
    not that one was sent and forgotten.
    """

    class Chain(models.TextChoices):
        POLYGON_AMOY = "polygon-amoy", "Polygon Amoy"
        SEPOLIA = "sepolia", "Ethereum Sepolia"
        OTHER = "other", "Development chain"
        LOCAL = "local", "Local registry"

    listing = models.OneToOneField(
        "properties.Listing", on_delete=models.CASCADE, related_name="token"
    )
    token_id = models.CharField(max_length=60)
    chain = models.CharField(max_length=20, choices=Chain.choices, default=Chain.LOCAL)

    #: keccak256 of the canonical metadata bundle, as stored by the contract.
    evidence_hash = models.CharField(max_length=66, blank=True)
    metadata_cid = models.CharField(max_length=120, blank=True)

    #: Filled only by a mined transaction.
    chain_token_id = models.PositiveIntegerField(null=True, blank=True)
    contract_address = models.CharField(max_length=64, blank=True)
    tx_hash = models.CharField(max_length=80, blank=True)
    block_number = models.PositiveBigIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    shares_opened_at = models.DateTimeField(null=True, blank=True)

    minted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.token_id

    @property
    def is_onchain(self) -> bool:
        """
        True only once a transaction has actually put this deed somewhere.

        All three columns are written by the same mined receipt, so there is no
        state where one is filled and the others are not. Checking the chain
        name instead would be checking a label rather than the evidence.
        """
        return bool(self.chain_token_id and self.tx_hash and self.contract_address)

    @property
    def shares_are_open(self) -> bool:
        return self.is_onchain and self.shares_opened_at is not None

    @property
    def short_tx(self) -> str:
        if not self.tx_hash:
            return ""
        return f"{self.tx_hash[:10]}...{self.tx_hash[-8:]}"

    @property
    def short_hash(self) -> str:
        if not self.evidence_hash:
            return ""
        return f"{self.evidence_hash[:10]}...{self.evidence_hash[-8:]}"

    @property
    def short_contract(self) -> str:
        if not self.contract_address:
            return ""
        return f"{self.contract_address[:8]}...{self.contract_address[-6:]}"

    def explorer_url(self, kind: str) -> str:
        """A link into the chain's block explorer, or empty off chain."""
        if not self.is_onchain:
            return ""

        from chain.client import EXPLORERS

        chain_ids = {
            self.Chain.POLYGON_AMOY: 80002,
            self.Chain.SEPOLIA: 11155111,
        }
        known = EXPLORERS.get(chain_ids.get(self.chain, 0))
        if not known:
            return ""

        value = {"tx": self.tx_hash, "address": self.contract_address}.get(kind, "")
        return f"{known[1]}/{kind}/{value}" if value else ""

    @property
    def explorer_tx_url(self) -> str:
        return self.explorer_url("tx")

    @property
    def explorer_contract_url(self) -> str:
        return self.explorer_url("address")
