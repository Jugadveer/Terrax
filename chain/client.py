"""
The connection to an EVM chain, and the one way transactions are sent.

Everything that touches a node goes through here, so the rest of the
application never holds a key, builds a transaction, or decides what to do
about a receipt. `is_configured()` is the single question asked before any of
it runs, and it is false by default: the project ships with no RPC URL and no
key, and the site works without them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

from chain import compiler

logger = logging.getLogger(__name__)

# `web3` is imported inside the functions that need it rather than at the top of
# this module. It takes about a second and a half to import, this module is
# reached from `properties.services` on every listing page, and the project
# ships with no chain configured. Paying that on a cold start for a page that
# never opens a socket is the kind of cost that used to make this site slow.

#: Chains this build knows how to link to a block explorer.
EXPLORERS = {
    80002: ("Polygon Amoy", "https://amoy.polygonscan.com"),
    11155111: ("Ethereum Sepolia", "https://sepolia.etherscan.io"),
    137: ("Polygon", "https://polygonscan.com"),
    1: ("Ethereum", "https://etherscan.io"),
}

#: Receipt wait, in seconds. Amoy settles in a few seconds; Sepolia is slower.
RECEIPT_TIMEOUT = 180


class ChainError(RuntimeError):
    """Raised when a transaction cannot be sent or is rejected on chain."""


@dataclass(frozen=True)
class Receipt:
    """What the caller needs from a mined transaction, without the web3 types."""

    tx_hash: str
    block_number: int
    gas_used: int
    status: int

    @property
    def succeeded(self) -> bool:
        return self.status == 1


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """True when there is somewhere to send a transaction and a key to sign it."""
    return bool(settings.WEB3_RPC_URL and settings.WEB3_PRIVATE_KEY)


def has_contracts() -> bool:
    return bool(settings.DEED_CONTRACT_ADDRESS and settings.SHARES_CONTRACT_ADDRESS)


def chain_name(chain_id: int | None = None) -> str:
    chain_id = settings.WEB3_CHAIN_ID if chain_id is None else chain_id
    known = EXPLORERS.get(chain_id)
    return known[0] if known else f"chain {chain_id}"


def explorer_url(kind: str, value: str, chain_id: int | None = None) -> str:
    """A link to a transaction, address or token on the chain's explorer."""
    chain_id = settings.WEB3_CHAIN_ID if chain_id is None else chain_id
    known = EXPLORERS.get(chain_id)
    if not known or not value:
        return ""
    return f"{known[1]}/{kind}/{value}"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def web3():
    """
    One connection per process.

    The proof-of-authority middleware is not optional on Polygon. Its blocks
    carry a longer `extraData` field than the Ethereum schema allows, and
    without this every block read raises a validation error that reads like a
    network fault.
    """
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware

    if not settings.WEB3_RPC_URL:
        raise ChainError("WEB3_RPC_URL is not set.")

    connection = Web3(Web3.HTTPProvider(settings.WEB3_RPC_URL, request_kwargs={"timeout": 30}))
    connection.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return connection


@lru_cache(maxsize=1)
def account():
    """The issuer account, derived from the configured key."""
    from eth_account import Account

    if not settings.WEB3_PRIVATE_KEY:
        raise ChainError("WEB3_PRIVATE_KEY is not set.")
    return Account.from_key(settings.WEB3_PRIVATE_KEY)


def address() -> str:
    return account().address


def balance_eth() -> float:
    """The issuer's balance, for the status page and the deploy preflight."""
    return float(web3().from_wei(web3().eth.get_balance(address()), "ether"))


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


def contract(name: str, contract_address: str = ""):
    """A handle on a deployed contract, by name and address."""
    from web3 import Web3

    artifact = compiler.compile_contract(name)
    resolved = contract_address or {
        "PropertyDeed": settings.DEED_CONTRACT_ADDRESS,
        "PropertyShares": settings.SHARES_CONTRACT_ADDRESS,
    }.get(name, "")

    if not resolved:
        raise ChainError(f"No deployed address configured for {name}.")

    return web3().eth.contract(address=Web3.to_checksum_address(resolved), abi=artifact.abi)


def deed():
    return contract("PropertyDeed")


def shares():
    return contract("PropertyShares")


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def send(function, *, gas_limit: int | None = None) -> Receipt:
    """
    Sign and send one contract call, then wait for it to be mined.

    The gas limit is estimated and padded by a quarter. An estimate is made
    against the current state, and the state moves between the estimate and the
    transaction landing in a block, so an exact estimate is the one number most
    likely to run out.
    """
    connection = web3()
    signer = account()

    limit = gas_limit or int(function.estimate_gas({"from": signer.address}) * 1.25)
    transaction = function.build_transaction(
        {
            "from": signer.address,
            "nonce": connection.eth.get_transaction_count(signer.address),
            "gas": limit,
            "chainId": settings.WEB3_CHAIN_ID,
            **_fees(connection),
        }
    )

    signed = signer.sign_transaction(transaction)
    tx_hash = connection.eth.send_raw_transaction(signed.raw_transaction)
    mined = connection.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT)

    receipt = Receipt(
        tx_hash=mined["transactionHash"].hex(),
        block_number=mined["blockNumber"],
        gas_used=mined["gasUsed"],
        status=mined["status"],
    )
    if not receipt.succeeded:
        raise ChainError(f"Transaction {receipt.tx_hash} was mined but reverted.")
    return receipt


def deploy(name: str) -> tuple[str, Receipt]:
    """Deploy one contract and return its address."""
    artifact = compiler.compile_contract(name)
    connection = web3()
    signer = account()

    factory = connection.eth.contract(abi=artifact.abi, bytecode=artifact.bytecode)
    constructor = factory.constructor()

    transaction = constructor.build_transaction(
        {
            "from": signer.address,
            "nonce": connection.eth.get_transaction_count(signer.address),
            "gas": int(constructor.estimate_gas({"from": signer.address}) * 1.25),
            "chainId": settings.WEB3_CHAIN_ID,
            **_fees(connection),
        }
    )

    signed = signer.sign_transaction(transaction)
    tx_hash = connection.eth.send_raw_transaction(signed.raw_transaction)
    mined = connection.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT)

    if mined["status"] != 1:
        raise ChainError(f"Deployment of {name} reverted.")

    return mined["contractAddress"], Receipt(
        tx_hash=mined["transactionHash"].hex(),
        block_number=mined["blockNumber"],
        gas_used=mined["gasUsed"],
        status=mined["status"],
    )


def _fees(connection) -> dict[str, int]:
    """
    EIP-1559 fees where the chain supports them, a flat gas price where it does not.

    Reading the chain's own suggestion rather than hardcoding a number is what
    stops a transaction sitting unmined for an hour when the network is busy.
    """
    try:
        base = connection.eth.get_block("latest")["baseFeePerGas"]
    except KeyError:
        return {"gasPrice": connection.eth.gas_price}

    tip = connection.eth.max_priority_fee
    return {"maxFeePerGas": base * 2 + tip, "maxPriorityFeePerGas": tip}
