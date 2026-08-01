"""Tests for the crypto / PQC extension.

The central claim to verify: applying the quantum-risk tilt as a drag on the
price series is *exactly* equivalent to subtracting theta*v from each asset's
period return, and leaves the covariance matrix untouched. That equivalence is
what lets the tilt pass through the hosted Qiskit Function, which exposes no
hook for a custom QUBO term.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qpo.crypto import (  # noqa: E402
    MAJORS_UNIVERSE,
    MIXED_UNIVERSE,
    PQC_PROFILES,
    PQC_UNIVERSE,
    apply_quantum_risk_adjustment,
    calibrate_risk_aversion,
    pqc_report,
    vulnerability_vector,
)
from qpo.qubo import QUBOSettings, build_dpo_problem  # noqa: E402


def _prices(assets, days=200, seed=11, vol=0.03):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days, freq="D").strftime("%Y-%m-%d")
    p = 100 * np.exp(np.cumsum(rng.normal(0.001, vol, size=(days, len(assets))), axis=0))
    return pd.DataFrame(p, index=idx, columns=assets)


def test_tilt_shifts_returns_exactly_and_preserves_covariance():
    assets = MIXED_UNIVERSE
    prices = _prices(assets)
    theta, dt = 0.07, 30
    st = QUBOSettings(nt=4, nq=2, dt=dt, max_investment=5)

    base = build_dpo_problem(prices, st)
    tilted = build_dpo_problem(apply_quantum_risk_adjustment(prices, theta, dt), st)
    v = vulnerability_vector(assets)

    shift = base.mu - tilted.mu  # (nt, na)
    assert np.allclose(shift, theta * v, atol=1e-12), "return shift is not theta*v"
    assert np.allclose(base.sigma, tilted.sigma, atol=1e-14), "covariance changed"
    print("PASS  tilt shifts mu by exactly theta*v and leaves Sigma untouched")


def test_zero_theta_is_a_noop():
    prices = _prices(PQC_UNIVERSE)
    out = apply_quantum_risk_adjustment(prices, 0.0, 30)
    assert np.allclose(out.to_numpy(), prices.to_numpy())
    print("PASS  theta=0 leaves prices unchanged")


def test_tilt_penalises_vulnerable_assets_more():
    """A more exposed asset must be dragged down harder."""
    prices = _prices(MIXED_UNIVERSE)
    adj = apply_quantum_risk_adjustment(prices, 0.2, 30)
    ratio = (adj.iloc[-1] / prices.iloc[-1]).to_numpy()
    v = vulnerability_vector(MIXED_UNIVERSE)
    # higher vulnerability -> smaller surviving price ratio
    assert np.corrcoef(v, ratio)[0, 1] < -0.95
    qrl = MIXED_UNIVERSE.index("QRL-USD")
    btc = MIXED_UNIVERSE.index("BTC-USD")
    assert ratio[qrl] > ratio[btc], "post-quantum asset should be penalised less"
    print(f"PASS  tilt is monotone in vulnerability "
          f"(QRL keeps {ratio[qrl]:.3f}, BTC keeps {ratio[btc]:.3f})")


def test_higher_theta_reduces_quantum_exposure():
    """The economically meaningful check: raising theta buys quantum safety."""
    from qpo.solvers import solve_bruteforce

    assets = ["BTC-USD", "ETH-USD", "ALGO-USD", "QRL-USD"]
    prices = _prices(assets, days=200)
    v = vulnerability_vector(assets)
    dt = 30

    def exposure_at(theta):
        st = QUBOSettings(nt=2, nq=2, dt=dt, max_investment=5,
                          risk_aversion=150.0, restriction_coeff=2.0)
        p = build_dpo_problem(apply_quantum_risk_adjustment(prices, theta, dt), st,
                              assets=assets)
        w = p.weights(solve_bruteforce(p).best_x)
        return float((w @ v).sum() / w.sum())

    lo, hi = exposure_at(0.0), exposure_at(1.5)
    print(f"      exposure: theta=0 -> {lo:.3f},  theta=1.5 -> {hi:.3f}")
    assert hi < lo, "a large theta must reduce quantum exposure"
    print("PASS  raising theta reduces portfolio quantum exposure")


def test_gamma_calibration_is_sane_and_scales_with_volatility():
    calm = _prices(MIXED_UNIVERSE, vol=0.01)
    wild = _prices(MIXED_UNIVERSE, vol=0.06)
    g_calm = calibrate_risk_aversion(calm, 4, 30)
    g_wild = calibrate_risk_aversion(wild, 4, 30)
    assert 1.0 <= g_wild <= 1e6 and 1.0 <= g_calm <= 1e6
    assert g_wild < g_calm, "more volatile data should need a smaller gamma"
    print(f"PASS  gamma calibration: calm={g_calm:.0f} > wild={g_wild:.0f}")


def test_taxonomy_is_well_formed():
    for ticker, prof in PQC_PROFILES.items():
        assert 0.0 <= prof.vulnerability <= 1.0, f"{ticker} vulnerability out of range"
        assert prof.tier in {"pqc", "partial", "classical"}, f"{ticker} bad tier"
        assert prof.signature and prof.note, f"{ticker} missing documentation"

    # Universes must be fully covered by the taxonomy.
    for uni in (PQC_UNIVERSE, MAJORS_UNIVERSE, MIXED_UNIVERSE):
        assert len(uni) == 7, "universes should hold 7 assets (paper's size)"
        for a in uni:
            assert a in PQC_PROFILES, f"{a} missing from the taxonomy"

    # Sanity: the PQC universe must be meaningfully safer than the majors.
    v_pqc = vulnerability_vector(PQC_UNIVERSE).mean()
    v_maj = vulnerability_vector(MAJORS_UNIVERSE).mean()
    assert v_pqc < v_maj - 0.3, "PQC universe should be much less vulnerable"
    print(f"PASS  taxonomy well-formed (mean v: pqc={v_pqc:.2f}, majors={v_maj:.2f})")


def test_mixed_universe_spans_the_spectrum():
    v = vulnerability_vector(MIXED_UNIVERSE)
    assert v.min() < 0.2 and v.max() > 0.85, "mixed universe must span the range"
    print(f"PASS  mixed universe spans v={v.min():.2f} to v={v.max():.2f}")


def test_report_covers_every_asset():
    df = pqc_report(MIXED_UNIVERSE)
    assert len(df) == len(MIXED_UNIVERSE)
    assert not df["signature"].eq("?").any()
    print("PASS  pqc_report documents every asset")


if __name__ == "__main__":
    test_tilt_shifts_returns_exactly_and_preserves_covariance()
    test_zero_theta_is_a_noop()
    test_tilt_penalises_vulnerable_assets_more()
    test_higher_theta_reduces_quantum_exposure()
    test_gamma_calibration_is_sane_and_scales_with_volatility()
    test_taxonomy_is_well_formed()
    test_mixed_universe_spans_the_spectrum()
    test_report_covers_every_asset()
    print("\nAll crypto/PQC tests passed.")
