"""
Turning the Solidity in `chain/contracts/` into an ABI and bytecode.

The compiler binary is fetched once by `py-solc-x` and cached by that library;
the compiled output is cached here as JSON, keyed by a hash of the source. That
hash is why a deployment cannot silently drift from the file on disk: edit a
contract and the artifact is rebuilt, leave it alone and nothing recompiles.

There is no Node toolchain anywhere in this project, which is the reason the
compile step runs from Python rather than from Hardhat or Foundry.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# `solcx` is imported inside the two functions that compile, not at the top.
# It takes about four tenths of a second to load and this module is reached
# from every listing page, but a deployed site only ever reads the cached
# artifact. A compiler is a build-time dependency, so it stays out of the
# request path and out of the production requirements.

SOLC_VERSION = "0.8.28"
OPTIMIZER_RUNS = 200

CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


@dataclass(frozen=True)
class Artifact:
    """Everything needed to deploy a contract or talk to a deployed one."""

    name: str
    abi: list[dict[str, Any]]
    bytecode: str
    source_hash: str

    @property
    def size_bytes(self) -> int:
        """Deployed size, against the 24,576 byte limit every EVM chain enforces."""
        return len(self.bytecode) // 2 - 1


def _source_path(name: str) -> Path:
    return CONTRACTS_DIR / f"{name}.sol"


def _artifact_path(name: str) -> Path:
    return ARTIFACTS_DIR / f"{name}.json"


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def available() -> list[str]:
    """Every contract with a source file, in a stable order."""
    return sorted(path.stem for path in CONTRACTS_DIR.glob("*.sol"))


def ensure_solc() -> str:
    """Install the pinned compiler if this machine does not have it yet."""
    import solcx

    installed = {str(version) for version in solcx.get_installed_solc_versions()}
    if SOLC_VERSION not in installed:
        logger.info("installing solc %s", SOLC_VERSION)
        solcx.install_solc(SOLC_VERSION)
    return SOLC_VERSION


def compile_contract(name: str, *, force: bool = False) -> Artifact:
    """
    Compile one contract, reusing the cached artifact when the source is unchanged.

    A pinned compiler version matters more here than in most builds: the same
    source compiled by two different versions produces different bytecode and a
    different deployed address, so an unpinned toolchain makes a deployment
    impossible to reproduce.
    """
    source = _source_path(name).read_text(encoding="utf-8")
    digest = _source_hash(source)
    cached = _artifact_path(name)

    if cached.exists() and not force:
        stored = json.loads(cached.read_text(encoding="utf-8"))
        if stored.get("source_hash") == digest:
            return Artifact(name, stored["abi"], stored["bytecode"], digest)

    import solcx

    ensure_solc()
    compiled = solcx.compile_files(
        [_source_path(name)],
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
        optimize=True,
        optimize_runs=OPTIMIZER_RUNS,
    )

    key = next(k for k in compiled if k.endswith(f":{name}"))
    artifact = Artifact(name, compiled[key]["abi"], "0x" + compiled[key]["bin"], digest)

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    cached.write_text(
        json.dumps(
            {
                "name": name,
                "solc": SOLC_VERSION,
                "optimizer_runs": OPTIMIZER_RUNS,
                "source_hash": digest,
                "abi": artifact.abi,
                "bytecode": artifact.bytecode,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("compiled %s (%s bytes)", name, artifact.size_bytes)
    return artifact


def compile_all(*, force: bool = False) -> dict[str, Artifact]:
    return {name: compile_contract(name, force=force) for name in available()}
