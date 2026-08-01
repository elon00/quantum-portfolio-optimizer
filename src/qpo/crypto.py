"""
Cryptocurrency universes, post-quantum (PQC) risk taxonomy, and the
quantum-risk price adjustment.

Two things make crypto different from the IBEX/NSE equity case:

1. **24/7 trading and extreme volatility.** A risk-aversion coefficient
   calibrated for equities (γ = 1000 in the paper) is badly mis-scaled for
   assets that routinely move 10% in a day. `calibrate_risk_aversion` picks γ
   so the risk and return terms carry comparable weight - which is exactly the
   rationale the paper gives for its own choice of γ.

2. **Quantum threat is an asset-level risk factor.** Nearly all major chains
   authenticate transactions with ECDSA/EdDSA, which Shor's algorithm breaks.
   A handful of chains have deployed post-quantum signatures. That difference
   is a genuine, forward-looking risk dimension that ordinary mean-variance
   optimization cannot see, because it has not shown up in the price history
   yet.

The quantum-risk tilt
---------------------
We express "prefer quantum-safe assets" as a linear penalty on the weights:

    + θ Σ_t Σ_a v_a ω̃_{t,a}

where v_a ∈ [0,1] is the asset's quantum *vulnerability* and θ its price.
The return term of the QUBO is −Σ_t μ_{t,a} ω̃_{t,a}, so this penalty is
identical to replacing μ_{t,a} with (μ_{t,a} − θ v_a).

That equivalence is useful, because the hosted Qiskit Function only accepts
seven QUBO settings and gives no hook for a custom term. But it *does* accept
arbitrary price series. Applying a geometric drag to the price path,

    P'_{s,a} = P_{s,a} · exp(−δ_a · s),      δ_a = θ v_a / dt   (per day)

shifts every daily log-return by exactly −δ_a, hence every period return by
−θ v_a, and leaves the covariance matrix untouched (subtracting a constant
from a series does not change its covariance). So the tilt can be pushed
through the black-box function exactly, with no API changes at all.

Sources for the taxonomy below: NIST PQC standards (ML-KEM/FIPS 203,
ML-DSA/FIPS 204, SLH-DSA/FIPS 205, Falcon selected for future
standardization); QRL's IETF-specified XMSS; Algorand's Falcon-based State
Proofs (2022) and its first Falcon-1024 mainnet transaction (Nov 2025).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "PQC_PROFILES",
    "PQC_UNIVERSE",
    "MAJORS_UNIVERSE",
    "MIXED_UNIVERSE",
    "AssetProfile",
    "vulnerability_vector",
    "apply_quantum_risk_adjustment",
    "calibrate_risk_aversion",
    "pqc_report",
]


@dataclass(frozen=True)
class AssetProfile:
    """Cryptographic posture of a chain.

    Attributes
    ----------
    name :
        Human-readable asset name.
    tier :
        ``pqc``       - post-quantum signatures deployed in production
        ``partial``   - PQC deployed for part of the stack, or credibly on a
                        funded roadmap
        ``classical`` - relies on ECDSA/EdDSA/Schnorr throughout
    signature :
        The signature scheme(s) securing user funds.
    vulnerability :
        v ∈ [0,1]. 0 = fully post-quantum, 1 = fully exposed to Shor.
    note :
        Short factual justification.
    """

    name: str
    tier: str
    signature: str
    vulnerability: float
    note: str


# Vulnerability scores are judgement calls on published cryptographic posture,
# not investment advice. They are deliberately conservative: a chain only
# scores low if post-quantum signatures actually protect user funds today.
PQC_PROFILES: dict[str, AssetProfile] = {
    # ---- Tier 1: post-quantum signatures securing user funds in production --
    "QRL-USD": AssetProfile(
        "Quantum Resistant Ledger", "pqc", "XMSS (hash-based, IETF RFC 8391)",
        0.05,
        "PQ from genesis; stateful hash-based signatures, no ECDSA anywhere.",
    ),
    "ABEL-USD": AssetProfile(
        "Abelian", "pqc", "Lattice-based (Dilithium-family)", 0.15,
        "Privacy L1 designed around lattice cryptography.",
    ),
    "CELL-USD": AssetProfile(
        "Cellframe", "pqc", "Dilithium / Falcon / SPHINCS+", 0.15,
        "Service-oriented L1 with selectable NIST PQC signature schemes.",
    ),
    "XX-USD": AssetProfile(
        "xx network", "pqc", "Hash-based (WOTS+)", 0.20,
        "PQ signatures plus metadata-shredding mixnet.",
    ),
    "MWC-USD": AssetProfile(
        "MimbleWimbleCoin", "partial", "Schnorr / MimbleWimble", 0.55,
        "No reusable addresses reduces harvest-now-decrypt-later surface, but "
        "the signature scheme itself is not post-quantum.",
    ),

    # ---- Tier 2: partial deployment or credible funded roadmap -------------
    "ALGO-USD": AssetProfile(
        "Algorand", "partial", "Ed25519 + Falcon-1024 state proofs", 0.35,
        "Falcon State Proofs since 2022 secure chain history; first Falcon "
        "mainnet transaction Nov 2025; consensus VRF still classical.",
    ),
    "IOTA-USD": AssetProfile(
        "IOTA", "partial", "Ed25519 (formerly Winternitz OTS)", 0.60,
        "Used hash-based W-OTS pre-Chrysalis; current protocol is Ed25519.",
    ),
    "QNT-USD": AssetProfile(
        "Quant", "partial", "Overledger interoperability layer", 0.55,
        "Publishes PQC interoperability research; security inherits from the "
        "chains it connects.",
    ),
    "KMD-USD": AssetProfile(
        "Komodo", "partial", "ECDSA + delayed Proof-of-Work", 0.55,
        "dPoW notarisation to Bitcoin hardens history, not signatures.",
    ),
    "HBAR-USD": AssetProfile(
        "Hedera", "partial", "Ed25519", 0.60,
        "Enterprise governance enables coordinated migration; not yet PQ.",
    ),

    # ---- Tier 3: classical elliptic-curve cryptography ---------------------
    "BTC-USD": AssetProfile(
        "Bitcoin", "classical", "ECDSA / Schnorr (secp256k1)", 0.95,
        "Exposed reused/taproot public keys are Shor-vulnerable; migration "
        "requires consensus-level change.",
    ),
    "ETH-USD": AssetProfile(
        "Ethereum", "classical", "ECDSA (secp256k1)", 0.90,
        "Account abstraction and leanXMSS research underway; nothing shipped.",
    ),
    "SOL-USD": AssetProfile("Solana", "classical", "Ed25519", 0.90, "Classical EdDSA."),
    "XRP-USD": AssetProfile("XRP", "classical", "ECDSA / Ed25519", 0.90, "Classical."),
    "BNB-USD": AssetProfile("BNB", "classical", "ECDSA (secp256k1)", 0.90, "Classical."),
    "ADA-USD": AssetProfile("Cardano", "classical", "Ed25519", 0.88,
                            "PQ research published; not deployed."),
    "AVAX-USD": AssetProfile("Avalanche", "classical", "ECDSA", 0.90, "Classical."),
    "DOT-USD": AssetProfile("Polkadot", "classical", "sr25519 / Ed25519", 0.88,
                            "Forkless upgrades ease a future migration."),
    "LINK-USD": AssetProfile("Chainlink", "classical", "ECDSA", 0.90, "Classical."),
    "LTC-USD": AssetProfile("Litecoin", "classical", "ECDSA (secp256k1)", 0.92, "Classical."),
    "DOGE-USD": AssetProfile("Dogecoin", "classical", "ECDSA (secp256k1)", 0.93, "Classical."),
    "TRX-USD": AssetProfile("Tron", "classical", "ECDSA (secp256k1)", 0.90, "Classical."),
    "XLM-USD": AssetProfile("Stellar", "classical", "Ed25519", 0.88, "Classical."),
}


# Seven-asset universes, matching the paper's portfolio size.
PQC_UNIVERSE = ["QRL-USD", "ALGO-USD", "CELL-USD", "ABEL-USD",
                "XX-USD", "IOTA-USD", "QNT-USD"]

MAJORS_UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
                   "BNB-USD", "ADA-USD", "LINK-USD"]

# Deliberately spans the vulnerability spectrum, so the quantum-risk tilt has
# something to actually trade off against.
MIXED_UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD",      # classical, liquid
                  "ALGO-USD", "HBAR-USD",                # partial
                  "QRL-USD", "CELL-USD"]                 # post-quantum


def vulnerability_vector(assets: list[str], default: float = 0.85) -> np.ndarray:
    """Quantum-vulnerability score v ∈ [0,1] for each asset, in order."""
    return np.array(
        [PQC_PROFILES[a].vulnerability if a in PQC_PROFILES else default for a in assets],
        dtype=float,
    )


def apply_quantum_risk_adjustment(
    prices: pd.DataFrame,
    theta: float,
    dt: int,
    default_vulnerability: float = 0.85,
) -> pd.DataFrame:
    """Bake the quantum-risk tilt into the price series.

    Applies P'_{s,a} = P_{s,a} · exp(−θ v_a s / dt), which shifts each asset's
    per-period log-return by exactly −θ v_a and leaves covariances unchanged.

    The returned table can be handed straight to the hosted Qiskit Function:
    the tilt travels inside the price data, so no API extension is needed.

    Parameters
    ----------
    theta :
        Price of quantum risk, in units of per-period log-return. θ = 0.02
        means a maximally vulnerable asset is charged 2% of period return.
    """
    if theta == 0.0:
        return prices.copy()
    v = vulnerability_vector(list(prices.columns), default_vulnerability)
    s = np.arange(len(prices), dtype=float)[:, None]  # day index
    drag = np.exp(-theta * v[None, :] * s / float(dt))
    return pd.DataFrame(
        prices.to_numpy(dtype=float) * drag, index=prices.index, columns=prices.columns
    )


def calibrate_risk_aversion(
    prices: pd.DataFrame, nt: int, dt: int, target_ratio: float = 1.0
) -> float:
    """Pick γ so the risk term is comparable in magnitude to the return term.

    The paper chose γ = 1000 for equities on exactly this reasoning ("a γ value
    that ensures the risk function and the expected return function have the
    same influence on the final solution"). Crypto volatility is roughly an
    order of magnitude larger, so the equity value would let risk swamp
    everything; this recomputes it from the data.

    Uses an equal-weight portfolio as the reference allocation.
    """
    P = prices.to_numpy(dtype=float)
    na = P.shape[1]
    w = np.ones(na) / na
    daily = np.log(P[1:] / P[:-1])

    f_tot, r_tot = 0.0, 0.0
    for t in range(nt):
        i0, i1 = t * dt, (t + 1) * dt
        if i1 >= len(P):
            break
        f_tot += abs(float(np.log(P[i1] / P[i0]) @ w))
        win = daily[i0:i1]
        cov = np.cov(win, rowvar=False, ddof=1)
        cov = np.atleast_2d(cov)
        r_tot += float(w @ cov @ w)

    if r_tot <= 0:
        return 1000.0
    gamma = 2.0 * target_ratio * f_tot / r_tot
    # Keep it in a sane band; γ is a modelling choice, not a fitted parameter.
    return float(np.clip(gamma, 1.0, 1e6))


def pqc_report(assets: list[str]) -> pd.DataFrame:
    """Tabulate the cryptographic posture of a universe."""
    rows = []
    for a in assets:
        p = PQC_PROFILES.get(a)
        if p is None:
            rows.append({"ticker": a, "name": a, "tier": "unknown",
                         "signature": "?", "vulnerability": 0.85, "note": ""})
        else:
            rows.append({"ticker": a, "name": p.name, "tier": p.tier,
                         "signature": p.signature, "vulnerability": p.vulnerability,
                         "note": p.note})
    return pd.DataFrame(rows)
