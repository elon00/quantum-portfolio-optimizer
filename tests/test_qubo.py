"""Correctness tests for the QUBO construction.

The critical invariant: the QUBO matrix energy must equal the analytically
evaluated objective (Eq. 15) for *any* bitstring. If that holds, everything
downstream (VQE, annealing, the real Qiskit Function) is optimising the right
thing.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qpo.qubo import QUBOSettings, build_dpo_problem  # noqa: E402
from qpo.solvers import qubo_to_ising, solve_bruteforce, solve_vqe_de  # noqa: E402


def _toy_prices(na=3, days=125, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=days, freq="D").strftime("%Y-%m-%d")
    p = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, size=(days, na)), axis=0))
    return pd.DataFrame(p, index=idx, columns=[f"A{i}" for i in range(na)])


def _objective_direct(problem, x):
    """Evaluate Eq. 15 term-by-term, independently of the matrix build."""
    s = problem.settings
    w = problem.weights(x)
    nt, na = problem.nt, problem.na

    F = np.sum(problem.mu * w)
    R = sum(w[t] @ problem.sigma[t] @ w[t] for t in range(nt))

    prev = np.zeros(na)
    C = 0.0
    for t in range(nt):
        C += np.sum((w[t] - prev) ** 2)
        prev = w[t]
    C *= s.transaction_fee * s.lambda_tc

    P = sum((w[t].sum() - 1.0) ** 2 for t in range(nt))

    return -F + (s.risk_aversion / 2.0) * R + C + s.restriction_coeff * P


def test_qubo_matches_analytic_objective():
    prices = _toy_prices()
    st = QUBOSettings(nt=3, nq=2, dt=30, max_investment=4, risk_aversion=1000.0)
    prob = build_dpo_problem(prices, st)

    rng = np.random.default_rng(0)
    for _ in range(300):
        x = rng.integers(0, 2, size=prob.num_qubits)
        assert np.isclose(prob.energy(x), _objective_direct(prob, x), rtol=1e-9, atol=1e-9)
    print("PASS  QUBO matrix == analytic Eq.15 objective (300 random bitstrings)")


def test_ising_mapping_is_faithful():
    prices = _toy_prices(na=2, days=95, seed=3)
    st = QUBOSettings(nt=2, nq=2, dt=30, max_investment=3)
    prob = build_dpo_problem(prices, st)
    h, J, c = qubo_to_ising(prob.Q, prob.offset)

    rng = np.random.default_rng(1)
    for _ in range(200):
        x = rng.integers(0, 2, size=prob.num_qubits)
        z = 1.0 - 2.0 * x
        e_ising = h @ z + z @ J @ z + c
        assert np.isclose(e_ising, prob.energy(x), rtol=1e-9, atol=1e-9)
    print("PASS  QUBO -> Ising mapping is energy-preserving")


def test_qubit_count_formula():
    prices = _toy_prices(na=4, days=160)
    st = QUBOSettings(nt=4, nq=2, dt=30, max_investment=5)
    prob = build_dpo_problem(prices, st)
    assert prob.num_qubits == 4 * 4 * 2 == 32
    assert prob.Q.shape == (32, 32)
    print("PASS  qubit count == na*nt*nq")


def test_decoding_respects_resolution_bounds():
    prices = _toy_prices(na=3, days=125)
    st = QUBOSettings(nt=3, nq=2, dt=30, max_investment=5)
    prob = build_dpo_problem(prices, st)
    x = np.ones(prob.num_qubits, dtype=int)  # all bits set = max spend
    w = prob.weights(x)
    expected = (2**st.nq - 1) / st.max_investment
    assert np.allclose(w, expected)
    print(f"PASS  max decoded weight == K'/K == {expected:.2%}")


def test_metrics_are_consistent():
    prices = _toy_prices()
    st = QUBOSettings(nt=3, nq=2, dt=30, max_investment=4)
    prob = build_dpo_problem(prices, st)
    x = np.zeros(prob.num_qubits, dtype=int)
    m = prob.metrics(x)
    # Empty portfolio: no return, no risk, no cost, 100% constraint breach.
    assert np.isclose(m["return"], 0.0)
    assert np.isclose(m["transaction_cost"], 0.0)
    assert np.isclose(m["rest_breach_pct"], 100.0)
    assert np.isclose(m["objective_cost"], st.restriction_coeff * st.nt)
    print("PASS  metrics consistent on the empty-portfolio edge case")


def test_infeasible_settings_are_rejected():
    prices = _toy_prices(na=2, days=95, seed=3)
    # nq=1 -> K'=1, K=10: even both assets at max reach only 0.2 < 1.
    try:
        build_dpo_problem(prices, QUBOSettings(nt=2, nq=1, dt=30, max_investment=10))
    except ValueError as e:
        assert "Infeasible budget" in str(e)
        print("PASS  infeasible budget configuration rejected up front")
        return
    raise AssertionError("expected ValueError for infeasible budget")


def test_vqe_beats_random_and_finds_good_solutions():
    """The real end-to-end check: VQE-DE should approach the exact optimum."""
    prices = _toy_prices(na=3, days=125)
    st = QUBOSettings(nt=2, nq=2, dt=30, max_investment=4)
    prob = build_dpo_problem(prices, st)  # 3*2*2 = 12 qubits

    exact = solve_bruteforce(prob)
    vqe = solve_vqe_de(prob, num_generations=15, population_size=24, reps=2, seed=5)

    rng = np.random.default_rng(0)
    rnd = rng.integers(0, 2, size=(5000, prob.num_qubits)).astype(float)
    rnd_mean = float(np.mean(np.einsum("mi,ij,mj->m", rnd, prob.Q, rnd) + prob.offset))

    # Approximation ratio measured against the random offset, which is how the
    # paper frames solution quality (raw relative gap is meaningless when the
    # optimum sits near zero).
    ratio = (rnd_mean - vqe.best_cost) / (rnd_mean - exact.best_cost)
    print(
        f"      exact={exact.best_cost:.5f}  vqe={vqe.best_cost:.5f}  "
        f"random_mean={rnd_mean:.5f}  approx_ratio={ratio:.4f}"
    )
    assert vqe.best_cost < rnd_mean, "VQE must beat the random baseline"
    assert ratio > 0.98, f"VQE should recover >98% of the achievable gain, got {ratio:.4f}"
    print("PASS  VQE-DE beats random and lands near the exact optimum")


def test_all_ansatzes_build_and_are_distinct():
    """Every advertised ansatz must build, and they must not be the same circuit."""
    from qpo.solvers import _build_ansatz

    shapes = {}
    for kind in ("real_amplitudes", "cyclic", "optimized_real_amplitudes"):
        qc, n_params = _build_ansatz(6, 2, kind)
        assert qc.num_qubits == 6 and n_params > 0
        assert len(qc.parameters) == n_params, f"{kind} parameter count mismatch"
        shapes[kind] = (n_params, qc.depth(), dict(qc.count_ops()))

    assert shapes["cyclic"][2]["cx"] > shapes["real_amplitudes"][2]["cx"], \
        "cyclic entanglement should add the wrap-around CX"
    assert shapes["optimized_real_amplitudes"][0] > shapes["real_amplitudes"][0], \
        "optimized variant should carry more parameters"
    assert "rz" in shapes["optimized_real_amplitudes"][2]

    try:
        _build_ansatz(6, 2, "not_an_ansatz")
    except ValueError:
        print(f"PASS  all ansatzes build and differ {shapes['real_amplitudes'][0]}/"
              f"{shapes['cyclic'][0]}/{shapes['optimized_real_amplitudes'][0]} params; "
              "unknown ansatz rejected")
        return
    raise AssertionError("unknown ansatz should raise ValueError")


def test_solver_guardrails_refuse_oversized_instances():
    """Local solvers must fail loudly rather than hang on huge instances."""
    from qpo.solvers import solve_bruteforce, solve_vqe_de

    prices = _toy_prices(na=7, days=200)
    big = build_dpo_problem(prices, QUBOSettings(nt=4, nq=2, dt=30, max_investment=5))
    assert big.num_qubits == 56

    for fn, label in ((solve_bruteforce, "brute force"), (solve_vqe_de, "VQE")):
        try:
            fn(big)
        except ValueError:
            continue
        raise AssertionError(f"{label} should refuse a {big.num_qubits}-qubit instance")
    print("PASS  brute force and VQE both refuse oversized instances")


def test_function_payload_is_rectangular_and_json_safe():
    """The Qiskit Function requires identical date keys across every asset."""
    import json

    from qpo.data import to_function_payload

    prices = _toy_prices(na=4, days=160)
    payload = to_function_payload(prices)

    assert set(payload) == set(prices.columns)
    lengths = {len(v) for v in payload.values()}
    assert len(lengths) == 1, "assets have ragged date indices"
    for series in payload.values():
        assert all(isinstance(k, str) for k in series)
        assert all(isinstance(v, float) for v in series.values())
    json.dumps(payload)  # must not raise
    print(f"PASS  payload rectangular ({len(payload)} assets x {lengths.pop()} dates), "
          "JSON-serialisable")


if __name__ == "__main__":
    test_qubo_matches_analytic_objective()
    test_ising_mapping_is_faithful()
    test_qubit_count_formula()
    test_decoding_respects_resolution_bounds()
    test_metrics_are_consistent()
    test_infeasible_settings_are_rejected()
    test_vqe_beats_random_and_finds_good_solutions()
    test_all_ansatzes_build_and_are_distinct()
    test_solver_guardrails_refuse_oversized_instances()
    test_function_payload_is_rectangular_and_json_safe()
    print("\nAll tests passed.")
