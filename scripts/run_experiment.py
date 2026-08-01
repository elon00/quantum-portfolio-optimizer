#!/usr/bin/env python3
"""
End-to-end dynamic portfolio optimization study.

Runs locally with no IBM Quantum account:

    python scripts/run_experiment.py --portfolio ibex
    python scripts/run_experiment.py --portfolio nifty

Add --submit to send the *same* problem instance to the real Qiskit Function
on a QPU (requires Premium/Flex access + a Global Data Quantum licence):

    python scripts/run_experiment.py --portfolio ibex --submit \
        --backend ibm_torino --nt 4 --nq 2

Outputs land in results/ (figures, JSON, CSV, markdown summary).
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
from qpo.data import IBEX_PORTFOLIO, NIFTY_PORTFOLIO, load_prices  # noqa: E402
from qpo.qubo import QUBOSettings, build_dpo_problem  # noqa: E402
from qpo.solvers import (  # noqa: E402
    solve_bruteforce,
    solve_random,
    solve_simulated_annealing,
    solve_vqe_de,
)

PORTFOLIOS = {
    "ibex": (IBEX_PORTFOLIO, "2022-11-01", "2023-04-01"),
    "nifty": (NIFTY_PORTFOLIO, "2024-01-01", "2024-12-31"),
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--portfolio", choices=list(PORTFOLIOS), default="ibex")
    p.add_argument("--assets", nargs="*", default=None,
                   help="override the ticker list")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--nt", type=int, default=4, help="rebalancing time steps")
    p.add_argument("--nq", type=int, default=2, help="resolution qubits per variable")
    p.add_argument("--dt", type=int, default=30, help="days per time step")
    p.add_argument("--max-investment", type=float, default=5.0, help="K")
    p.add_argument("--risk-aversion", type=float, default=1000.0, help="gamma")
    p.add_argument("--transaction-fee", type=float, default=0.01, help="nu")
    p.add_argument("--restriction-coeff", type=float, default=1.0, help="rho")

    p.add_argument("--local-na", type=int, default=3,
                   help="assets used for the local statevector VQE run "
                        "(the full instance still goes to the QPU)")
    p.add_argument("--local-nt", type=int, default=2)
    p.add_argument("--ansatz", default="optimized_real_amplitudes")
    p.add_argument("--generations", type=int, default=20)
    p.add_argument("--population", type=int, default=None)
    p.add_argument("--seed", type=int, default=7)

    p.add_argument("--submit", action="store_true", help="run on real IBM hardware")
    p.add_argument("--backend", default="ibm_torino")
    p.add_argument("--no-scaling", action="store_true", help="skip the scaling sweep")
    p.add_argument("--max-sweep-qubits", type=int, default=18,
                   help="largest instance in the scaling sweep (statevector cost "
                        "roughly doubles per qubit)")
    p.add_argument("--outdir", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    symbols, d0, d1 = PORTFOLIOS[args.portfolio]
    symbols = args.assets or symbols
    start, end = args.start or d0, args.end or d1

    out = Path(args.outdir or ROOT / "results" / args.portfolio)
    figs = out / "figures"
    out.mkdir(parents=True, exist_ok=True)

    print("=" * 74)
    print(f"  DYNAMIC PORTFOLIO OPTIMIZATION  -  portfolio: {args.portfolio}")
    print("=" * 74)

    # ---------------------------------------------------------------- data
    prices, source = load_prices(symbols, start, end, cache_dir=ROOT / "data")
    prices.to_csv(out / "prices.csv")

    # ------------------------------------------------- full (QPU) instance
    settings = QUBOSettings(
        nt=args.nt, nq=args.nq, dt=args.dt,
        max_investment=args.max_investment,
        risk_aversion=args.risk_aversion,
        transaction_fee=args.transaction_fee,
        restriction_coeff=args.restriction_coeff,
    )
    problem = build_dpo_problem(prices, settings)
    print("\n" + problem.summary())

    plots.plot_prices(prices, problem, figs)
    plots.plot_qubo_matrix(problem, figs)

    # --------------------------------------------- local tractable instance
    # Statevector simulation caps out around 24 qubits, so the local quantum
    # run uses a reduced instance. The QPU path below uses the full one.
    local_assets = problem.assets[: args.local_na]
    local_settings = QUBOSettings(
        nt=args.local_nt, nq=args.nq, dt=args.dt,
        max_investment=args.max_investment,
        risk_aversion=args.risk_aversion,
        transaction_fee=args.transaction_fee,
        restriction_coeff=args.restriction_coeff,
    )
    local = build_dpo_problem(prices, local_settings, assets=local_assets)
    print("\n[local] " + local.summary().replace("\n", "\n[local] "))

    results = {}

    print("\n--- baselines ---")
    results["Random"] = solve_random(local, n_samples=20000, seed=args.seed)
    print(f"  random   : mean={results['Random'].info['mean_cost']:+.5f}  "
          f"best={results['Random'].best_cost:+.5f}")

    t0 = time.time()
    results["Simulated annealing"] = solve_simulated_annealing(
        local, n_restarts=40, n_sweeps=5000, seed=args.seed)
    print(f"  anneal   : best={results['Simulated annealing'].best_cost:+.5f}  "
          f"({time.time()-t0:.1f}s)")

    exact_cost = None
    if local.num_qubits <= 22:
        t0 = time.time()
        results["Exact (brute force)"] = solve_bruteforce(local)
        exact_cost = results["Exact (brute force)"].best_cost
        print(f"  exact    : best={exact_cost:+.5f}  "
              f"({2**local.num_qubits:,} states, {time.time()-t0:.1f}s)")

    print("\n--- local VQE (Differential Evolution) ---")
    t0 = time.time()
    vqe = solve_vqe_de(
        local, ansatz=args.ansatz, reps=2,
        num_generations=args.generations,
        population_size=args.population,
        seed=args.seed,
    )
    vqe_secs = time.time() - t0
    results[vqe.name] = vqe
    print(f"  {vqe.name}: best={vqe.best_cost:+.5f}  ({vqe_secs:.1f}s)")
    print(f"    params={vqe.info['n_params']}  pop={vqe.info['population_size']}  "
          f"circuits={vqe.info['circuit_evaluations']}")

    ref = exact_cost if exact_cost is not None else results["Simulated annealing"].best_cost
    rmean = results["Random"].info["mean_cost"]
    ratio = (rmean - vqe.best_cost) / (rmean - ref) if rmean != ref else float("nan")
    print(f"    approximation ratio vs classical reference: {ratio:.4f}")

    # -------------------------------------------------------------- figures
    print("\n--- figures ---")
    plots.plot_cost_distributions(results, figs, exact_cost=exact_cost)
    plots.plot_convergence(
        {k: v for k, v in results.items() if v.history}, figs)
    plots.plot_strategy(local, vqe.best_x, figs,
                        f"Quantum-optimised strategy ({args.portfolio.upper()})")
    plots.plot_risk_return(local, results, figs, seed=args.seed)

    # ------------------------------------------------------- scaling sweep
    scaling_rows = []
    if not args.no_scaling:
        print("\n--- scaling sweep ---")
        for na, nt in [(2, 2), (3, 2), (2, 4), (4, 2), (3, 3), (5, 2), (4, 3), (6, 2)]:
            n_qubits = na * nt * args.nq
            if n_qubits > args.max_sweep_qubits:
                continue
            try:
                st = QUBOSettings(
                    nt=nt, nq=args.nq, dt=args.dt,
                    max_investment=args.max_investment,
                    risk_aversion=args.risk_aversion,
                    transaction_fee=args.transaction_fee,
                    restriction_coeff=args.restriction_coeff,
                )
                pr = build_dpo_problem(prices, st, assets=problem.assets[:na])
            except ValueError as e:
                print(f"  skip na={na} nt={nt}: {e}")
                continue

            ex = solve_bruteforce(pr)
            rnd = solve_random(pr, n_samples=8000, seed=args.seed)
            t0 = time.time()
            v = solve_vqe_de(pr, ansatz=args.ansatz, reps=2,
                             num_generations=args.generations,
                             population_size=args.population, seed=args.seed)
            secs = time.time() - t0
            rm = rnd.info["mean_cost"]
            r = (rm - v.best_cost) / (rm - ex.best_cost) if rm != ex.best_cost else 1.0
            scaling_rows.append({
                "na": na, "nt": nt, "nq": args.nq, "n_qubits": pr.num_qubits,
                "exact": ex.best_cost, "vqe": v.best_cost,
                "random_mean": rm, "approx_ratio": r,
                "optimal_found": bool(np.isclose(v.best_cost, ex.best_cost, atol=1e-9)),
                "vqe_seconds": secs,
            })
            print(f"  {pr.num_qubits:2d} qubits (na={na},nt={nt}): "
                  f"ratio={r:.4f} optimal={scaling_rows[-1]['optimal_found']} "
                  f"({secs:.1f}s)")

        if scaling_rows:
            sdf = pd.DataFrame(scaling_rows).sort_values("n_qubits")
            sdf.to_csv(out / "scaling.csv", index=False)
            plots.plot_scaling(sdf, figs)

    # --------------------------------------------------------- persistence
    summary = {
        "portfolio": args.portfolio,
        "assets": problem.assets,
        "data_source": source,
        "date_range": [start, end],
        "qpu_instance": {
            "na": problem.na, "nt": problem.nt, "nq": problem.nq,
            "n_qubits": problem.num_qubits,
            "qubo_settings": settings.to_function_dict(),
        },
        "local_instance": {
            "na": local.na, "nt": local.nt, "nq": local.nq,
            "n_qubits": local.num_qubits,
        },
        "results": {k: v.report(local) for k, v in results.items()},
        "approximation_ratio": ratio,
        "vqe_info": vqe.info,
        "vqe_seconds": vqe_secs,
        "best_strategy": local.strategy_dict(vqe.best_x),
        "scaling": scaling_rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    rows = [v.report(local) for v in results.values()]
    df = pd.DataFrame(rows)[
        ["solver", "objective_cost", "return", "risk", "sharpe_ratio",
         "transaction_cost", "rest_breach_pct"]
    ].sort_values("objective_cost")
    df.to_csv(out / "solver_comparison.csv", index=False)

    print("\n" + "=" * 74)
    print(df.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print("=" * 74)

    print("\nOptimal strategy (weights per rebalancing date):")
    w = local.weights(vqe.best_x)
    wdf = pd.DataFrame(w, columns=local.assets,
                       index=[f"t{t} ({d})" for t, d in enumerate(local.rebalance_dates)])
    wdf["TOTAL"] = wdf.sum(axis=1)
    print(wdf.to_string(float_format=lambda v: f"{v:.1%}"))
    wdf.to_csv(out / "optimal_strategy.csv")

    # ------------------------------------------------------------- QPU run
    if args.submit:
        from qpo.ibm_runner import FunctionConfig, estimate_workload, submit

        cfg = FunctionConfig(
            backend_name=args.backend,
            ansatz=args.ansatz,
            num_generations=args.generations,
            population_size=args.population or 40,
            tags=["dpo", args.portfolio],
        )
        check = estimate_workload(problem, cfg)
        print("\n--- QPU pre-flight ---")
        print(json.dumps(check, indent=2))
        if check["ok"]:
            submit(problem, cfg, out_dir=out)
        else:
            print("Pre-flight failed; not submitting.")
    else:
        print("\n[note] Local run only. Add --submit to execute the full "
              f"{problem.num_qubits}-qubit instance on {args.backend}.")

    print(f"\nArtifacts -> {out}")


if __name__ == "__main__":
    main()
