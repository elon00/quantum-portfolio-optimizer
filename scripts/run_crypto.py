#!/usr/bin/env python3
"""
Dynamic portfolio optimization for cryptocurrency portfolios, with an explicit
post-quantum-cryptography (PQC) risk dimension.

Three universes:
  --universe pqc      seven quantum-resistant / PQC-focused chains
  --universe majors   seven classical large caps (ECDSA/EdDSA)
  --universe mixed    spans the vulnerability spectrum (default)

The quantum-risk tilt is controlled by --theta: the price, in per-period
log-return, charged to a maximally Shor-vulnerable asset. It is applied as a
drag on the price series, which is mathematically identical to penalising
vulnerable weights in the QUBO - and therefore passes straight through the
hosted Qiskit Function with no API change.

Examples
--------
  python scripts/run_crypto.py --universe mixed --theta 0.05
  python scripts/run_crypto.py --universe pqc --theta 0
  python scripts/run_crypto.py --universe mixed --theta-sweep 0 0.02 0.05 0.1 0.2
  python scripts/run_crypto.py --universe mixed --submit --backend ibm_torino
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qpo import plots  # noqa: E402
from qpo.crypto import (  # noqa: E402
    MAJORS_UNIVERSE,
    MIXED_UNIVERSE,
    PQC_UNIVERSE,
    apply_quantum_risk_adjustment,
    calibrate_risk_aversion,
    pqc_report,
    vulnerability_vector,
)
from qpo.data import load_prices  # noqa: E402
from qpo.qubo import QUBOSettings, build_dpo_problem  # noqa: E402
from qpo.solvers import (  # noqa: E402
    solve_bruteforce,
    solve_random,
    solve_simulated_annealing,
    solve_vqe_de,
)

UNIVERSES = {"pqc": PQC_UNIVERSE, "majors": MAJORS_UNIVERSE, "mixed": MIXED_UNIVERSE}


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--universe", choices=list(UNIVERSES), default="mixed")
    p.add_argument("--assets", nargs="*", default=None)
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2026-07-30")

    p.add_argument("--nt", type=int, default=4)
    p.add_argument("--nq", type=int, default=2)
    p.add_argument("--dt", type=int, default=30)
    p.add_argument("--max-investment", type=float, default=5.0)
    p.add_argument("--risk-aversion", type=float, default=None,
                   help="gamma; auto-calibrated from crypto volatility if omitted")
    p.add_argument("--transaction-fee", type=float, default=0.01)
    p.add_argument("--restriction-coeff", type=float, default=2.0,
                   help="rho; crypto needs a stronger budget penalty than the "
                        "equity default of 1.0 (verified by sweep)")

    p.add_argument("--theta", type=float, default=0.05,
                   help="price of quantum risk (per-period log-return units)")
    p.add_argument("--theta-sweep", nargs="*", type=float, default=None,
                   help="sweep theta and plot the quantum-safety frontier")

    p.add_argument("--local-na", type=int, default=4)
    p.add_argument("--local-select", choices=["spread", "first"], default="spread",
                   help="'spread' picks a local subset spanning the vulnerability "
                        "spectrum so the quantum tilt has something to trade off; "
                        "'first' just takes the first --local-na tickers")
    p.add_argument("--local-nt", type=int, default=2)
    p.add_argument("--ansatz", default="optimized_real_amplitudes")
    p.add_argument("--generations", type=int, default=20)
    p.add_argument("--population", type=int, default=None)
    p.add_argument("--seed", type=int, default=7)

    p.add_argument("--submit", action="store_true")
    p.add_argument("--backend", default="ibm_torino")
    p.add_argument("--outdir", default=None)
    return p.parse_args()


def exposure(problem, x, v) -> float:
    """Average quantum-vulnerability exposure of a strategy, in [0,1]."""
    w = problem.weights(x)
    tot = w.sum()
    return float((w @ v).sum() / tot) if tot > 0 else 0.0


def main():
    args = parse_args()
    symbols = args.assets or UNIVERSES[args.universe]
    out = Path(args.outdir or ROOT / "results" / f"crypto_{args.universe}")
    figs = out / "figures"
    out.mkdir(parents=True, exist_ok=True)

    print("=" * 76)
    print(f"  CRYPTO DPO  -  universe: {args.universe}  |  theta = {args.theta}")
    print("=" * 76)

    # ------------------------------------------------------------- taxonomy
    rep = pqc_report(symbols)
    rep.to_csv(out / "pqc_taxonomy.csv", index=False)
    print("\n--- cryptographic posture ---")
    print(rep[["ticker", "name", "tier", "signature", "vulnerability"]]
          .to_string(index=False))

    # ----------------------------------------------------------------- data
    prices, source = load_prices(symbols, args.start, args.end, cache_dir=ROOT / "data")
    prices.to_csv(out / "prices_raw.csv")

    vol = np.log(prices / prices.shift(1)).std().mul(np.sqrt(365)).mul(100)
    print("\n--- annualised volatility (%) ---")
    print(vol.round(1).to_string())

    # --------------------------------------------------- gamma calibration
    if args.risk_aversion is None:
        gamma = calibrate_risk_aversion(prices, args.nt, args.dt)
        print(f"\n[gamma] auto-calibrated to {gamma:.1f} "
              f"(equity default 1000 is mis-scaled for crypto volatility)")
    else:
        gamma = args.risk_aversion
        print(f"\n[gamma] user-specified: {gamma:.1f}")

    # ------------------------------------------------- quantum-risk tilt
    adj = apply_quantum_risk_adjustment(prices, args.theta, args.dt)
    adj.to_csv(out / "prices_quantum_adjusted.csv")
    v = vulnerability_vector(symbols)

    def make_settings(nt):
        return QUBOSettings(
            nt=nt, nq=args.nq, dt=args.dt,
            max_investment=args.max_investment,
            risk_aversion=gamma,
            transaction_fee=args.transaction_fee,
            restriction_coeff=args.restriction_coeff,
        )

    problem = build_dpo_problem(adj, make_settings(args.nt))
    print("\n" + problem.summary())

    plots.plot_prices(prices, problem, figs)
    plots.plot_qubo_matrix(problem, figs)

    # ------------------------------------------- local (simulatable) instance
    # The local subset must span the vulnerability spectrum, otherwise the
    # quantum tilt has nothing to trade off against and looks like a no-op.
    if args.local_select == "spread" and args.local_na < len(symbols):
        vv = vulnerability_vector(symbols)
        order = np.argsort(vv)                       # safest -> most exposed
        picks = np.linspace(0, len(order) - 1, args.local_na).round().astype(int)
        chosen = sorted({int(order[i]) for i in picks})
        # top up if rounding collapsed duplicates
        for i in order:
            if len(chosen) >= args.local_na:
                break
            if int(i) not in chosen:
                chosen.append(int(i))
        local_assets = [symbols[i] for i in sorted(chosen)]
    else:
        local_assets = symbols[: args.local_na]
    local = build_dpo_problem(adj, make_settings(args.local_nt), assets=local_assets)
    local_plain = build_dpo_problem(prices, make_settings(args.local_nt),
                                    assets=local_assets)
    v_local = vulnerability_vector(local_assets)
    print(f"\n[local] {local.num_qubits} qubits: {', '.join(local_assets)}")
    print(f"[local] vulnerability span: {v_local.min():.2f} -> {v_local.max():.2f}")

    # ------------------------------------------------------------- solvers
    results = {}
    print("\n--- baselines ---")
    results["Random"] = solve_random(local, n_samples=20000, seed=args.seed)
    print(f"  random  : mean={results['Random'].info['mean_cost']:+.5f}")
    results["Simulated annealing"] = solve_simulated_annealing(
        local, n_restarts=40, n_sweeps=5000, seed=args.seed)
    print(f"  anneal  : best={results['Simulated annealing'].best_cost:+.5f}")

    exact_cost = None
    if local.num_qubits <= 22:
        ex = solve_bruteforce(local)
        results["Exact (brute force)"] = ex
        exact_cost = ex.best_cost
        print(f"  exact   : best={exact_cost:+.5f}")

    print("\n--- local VQE (Differential Evolution) ---")
    t0 = time.time()
    vqe = solve_vqe_de(local, ansatz=args.ansatz, reps=2,
                       num_generations=args.generations,
                       population_size=args.population, seed=args.seed)
    secs = time.time() - t0
    results[vqe.name] = vqe
    print(f"  {vqe.name}: best={vqe.best_cost:+.5f} ({secs:.1f}s)")

    ref = exact_cost if exact_cost is not None else results["Simulated annealing"].best_cost
    rmean = results["Random"].info["mean_cost"]
    ratio = (rmean - vqe.best_cost) / (rmean - ref) if rmean != ref else float("nan")
    print(f"  approximation ratio: {ratio:.4f}")

    # ------------------------------- quantum-aware vs quantum-blind comparison
    print("\n--- quantum-aware vs quantum-blind ---")
    blind = solve_bruteforce(local_plain) if local_plain.num_qubits <= 22 else \
        solve_simulated_annealing(local_plain, n_restarts=40, n_sweeps=5000, seed=args.seed)
    aware_x = vqe.best_x

    e_blind = exposure(local, blind.best_x, v_local)
    e_aware = exposure(local, aware_x, v_local)
    # Score both strategies on the *unadjusted* prices, so the comparison is
    # in real economic terms rather than in tilted units.
    m_blind = local_plain.metrics(blind.best_x)
    m_aware = local_plain.metrics(aware_x)

    cmp = pd.DataFrame([
        {"strategy": "quantum-blind (theta=0)", "quantum_exposure": e_blind,
         "return": m_blind["return"], "sharpe": m_blind["sharpe_ratio"],
         "risk": m_blind["risk"]},
        {"strategy": f"quantum-aware (theta={args.theta})", "quantum_exposure": e_aware,
         "return": m_aware["return"], "sharpe": m_aware["sharpe_ratio"],
         "risk": m_aware["risk"]},
    ])
    print(cmp.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    cmp.to_csv(out / "quantum_aware_vs_blind.csv", index=False)
    print(f"\n  exposure reduced by {(e_blind - e_aware):+.3f} "
          f"({(e_blind - e_aware) / e_blind * 100 if e_blind else 0:+.1f}%), "
          f"return change {m_aware['return'] - m_blind['return']:+.4f}")

    # -------------------------------------------------------------- figures
    print("\n--- figures ---")
    plots.plot_cost_distributions(results, figs, exact_cost=exact_cost)
    plots.plot_convergence({k: r for k, r in results.items() if r.history}, figs)
    plots.plot_strategy(local, vqe.best_x, figs,
                        f"Quantum-optimised crypto strategy ({args.universe})")
    plots.plot_risk_return(local, results, figs, seed=args.seed)
    plots.plot_pqc_exposure(local, vqe.best_x, v_local, local_assets, figs)

    # --------------------------------------------------------- theta sweep
    sweep_rows = []
    thetas = args.theta_sweep if args.theta_sweep else []
    if thetas:
        print("\n--- quantum-safety frontier (theta sweep) ---")
        for th in thetas:
            pr_th = apply_quantum_risk_adjustment(prices, th, args.dt)
            prob_th = build_dpo_problem(pr_th, make_settings(args.local_nt),
                                        assets=local_assets)
            s = solve_bruteforce(prob_th) if prob_th.num_qubits <= 22 else \
                solve_simulated_annealing(prob_th, n_restarts=30, n_sweeps=4000,
                                          seed=args.seed)
            e = exposure(prob_th, s.best_x, v_local)
            m = local_plain.metrics(s.best_x)  # score on untilted prices
            sweep_rows.append({"theta": th, "quantum_exposure": e,
                               "return": m["return"], "sharpe": m["sharpe_ratio"],
                               "risk": m["risk"]})
            print(f"  theta={th:<6.3f} exposure={e:.3f} "
                  f"return={m['return']:+.4f} sharpe={m['sharpe_ratio']:.2f}")
        sdf = pd.DataFrame(sweep_rows)
        sdf.to_csv(out / "theta_sweep.csv", index=False)
        plots.plot_quantum_frontier(sdf, figs)

    # ----------------------------------------------------------- persistence
    summary = {
        "universe": args.universe,
        "assets": symbols,
        "data_source": source,
        "date_range": [args.start, args.end],
        "theta": args.theta,
        "gamma": gamma,
        "annualised_vol_pct": vol.round(2).to_dict(),
        "qpu_instance": {"n_qubits": problem.num_qubits,
                         "qubo_settings": problem.settings.to_function_dict()},
        "local_instance": {"assets": local_assets, "n_qubits": local.num_qubits},
        "approximation_ratio": ratio,
        "results": {k: r.report(local) for k, r in results.items()},
        "quantum_exposure": {"blind": e_blind, "aware": e_aware},
        "best_strategy": local.strategy_dict(vqe.best_x),
        "theta_sweep": sweep_rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    df = pd.DataFrame([r.report(local) for r in results.values()])[
        ["solver", "objective_cost", "return", "risk", "sharpe_ratio",
         "transaction_cost", "rest_breach_pct"]].sort_values("objective_cost")
    df.to_csv(out / "solver_comparison.csv", index=False)
    print("\n" + "=" * 76)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("=" * 76)

    w = local.weights(vqe.best_x)
    wdf = pd.DataFrame(w, columns=local_assets,
                       index=[f"t{t} ({d})" for t, d in enumerate(local.rebalance_dates)])
    wdf["TOTAL"] = wdf.sum(axis=1)
    print("\nOptimal strategy:")
    print(wdf.to_string(float_format=lambda x: f"{x:.1%}"))
    wdf.to_csv(out / "optimal_strategy.csv")

    # ---------------------------------------------------------------- QPU
    if args.submit:
        from qpo.ibm_runner import FunctionConfig, estimate_workload, submit
        cfg = FunctionConfig(backend_name=args.backend, ansatz=args.ansatz,
                             num_generations=args.generations,
                             population_size=args.population or 40,
                             tags=["dpo", "crypto", args.universe])
        check = estimate_workload(problem, cfg)
        print("\n--- QPU pre-flight ---")
        print(json.dumps(check, indent=2))
        if check["ok"]:
            submit(problem, cfg, out_dir=out)
    else:
        print(f"\n[note] Local run only. Add --submit to run the full "
              f"{problem.num_qubits}-qubit instance on {args.backend}.")

    print(f"\nArtifacts -> {out}")


if __name__ == "__main__":
    main()
