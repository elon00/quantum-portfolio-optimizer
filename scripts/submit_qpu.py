#!/usr/bin/env python3
"""
Submit a DPO instance to Global Data Quantum's Quantum Portfolio Optimizer
running on real IBM Quantum hardware.

Prerequisites
-------------
  * IBM Quantum Premium / Flex / On-Prem plan (Qiskit Functions are not
    available on the Open plan)
  * a licence for the Global Data Quantum function
  * credentials in the environment:

        export IBM_QUANTUM_TOKEN="<44-character API key>"
        export IBM_QUANTUM_CRN="<instance CRN>"

Usage
-----
  # 1) always dry-run first: validates limits, prints the exact payload,
  #    and spends zero QPU time
  python scripts/submit_qpu.py --portfolio ibex --dry-run

  # 2) reproduce the official tutorial instance (56 qubits, ibm_torino)
  python scripts/submit_qpu.py --portfolio ibex --nt 4 --nq 2

  # 3) the paper's XXL instance (112 qubits, tailored ansatz)
  python scripts/submit_qpu.py --portfolio ibex --nt 4 --nq 4 \
      --ansatz tailored --population 120

  # 4) resume an optimisation that failed part-way
  python scripts/submit_qpu.py --portfolio ibex --resume <session_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qpo.data import IBEX_PORTFOLIO, NIFTY_PORTFOLIO, load_prices  # noqa: E402
from qpo.ibm_runner import (  # noqa: E402
    FunctionConfig,
    build_payload,
    estimate_workload,
    submit,
)
from qpo.qubo import QUBOSettings, build_dpo_problem  # noqa: E402

PORTFOLIOS = {
    "ibex": (IBEX_PORTFOLIO, "2022-11-01", "2023-04-01"),
    "nifty": (NIFTY_PORTFOLIO, "2024-01-01", "2024-12-31"),
}


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--portfolio", choices=list(PORTFOLIOS), default="ibex")
    p.add_argument("--assets", nargs="*", default=None)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)

    p.add_argument("--nt", type=int, default=4)
    p.add_argument("--nq", type=int, default=2)
    p.add_argument("--dt", type=int, default=30)
    p.add_argument("--max-investment", type=float, default=5.0)
    p.add_argument("--risk-aversion", type=float, default=1000.0)
    p.add_argument("--transaction-fee", type=float, default=0.01)
    p.add_argument("--restriction-coeff", type=float, default=1.0)

    p.add_argument("--backend", default="ibm_torino")
    p.add_argument("--ansatz", default="optimized_real_amplitudes",
                   choices=["real_amplitudes", "cyclic",
                            "optimized_real_amplitudes", "tailored"])
    p.add_argument("--generations", type=int, default=20)
    p.add_argument("--population", type=int, default=40)
    p.add_argument("--sampler-shots", type=int, default=100_000)
    p.add_argument("--estimator-shots", type=int, default=25_000)
    p.add_argument("--max-parallel-jobs", type=int, default=5)
    p.add_argument("--max-batchsize", type=int, default=4)
    p.add_argument("--no-postprocess", action="store_true",
                   help="disable the SQD noise-aware post-processing")
    p.add_argument("--resume", nargs="*", default=None,
                   help="previous session id(s) to resume from")
    p.add_argument("--dry-run", action="store_true",
                   help="validate and print the payload without submitting")
    p.add_argument("--outdir", default=None)
    args = p.parse_args()

    symbols, d0, d1 = PORTFOLIOS[args.portfolio]
    symbols = args.assets or symbols
    start, end = args.start or d0, args.end or d1

    out = Path(args.outdir or ROOT / "results" / args.portfolio)
    out.mkdir(parents=True, exist_ok=True)

    prices, source = load_prices(symbols, start, end, cache_dir=ROOT / "data")
    settings = QUBOSettings(
        nt=args.nt, nq=args.nq, dt=args.dt,
        max_investment=args.max_investment,
        risk_aversion=args.risk_aversion,
        transaction_fee=args.transaction_fee,
        restriction_coeff=args.restriction_coeff,
    )
    problem = build_dpo_problem(prices, settings)
    print(problem.summary())
    print(f"[data] source={source}\n")

    cfg = FunctionConfig(
        backend_name=args.backend,
        ansatz=args.ansatz,
        num_generations=args.generations,
        population_size=args.population,
        sampler_shots=args.sampler_shots,
        estimator_shots=args.estimator_shots,
        max_parallel_jobs=args.max_parallel_jobs,
        max_batchsize=args.max_batchsize,
        apply_postprocess=not args.no_postprocess,
        previous_session_id=args.resume,
        tags=["dpo", args.portfolio, f"{problem.num_qubits}q"],
    )

    check = estimate_workload(problem, cfg)
    print("--- pre-flight ---")
    print(f"  qubits          : {check['n_qubits']}  (na*nt*nq)")
    print(f"  total circuits  : {check['total_circuits']}  "
          f"= (generations+1) * population")
    print(f"  estimator load  : {check['estimator_load']:,} / 10,000,000")
    print(f"  sampler shots   : {cfg.sampler_shots:,} / 10,000,000")
    for w in check["warnings"]:
        print(f"  [warn]  {w}")
    for e in check["errors"]:
        print(f"  [error] {e}")
    print(f"  status          : {'OK' if check['ok'] else 'BLOCKED'}\n")

    payload = build_payload(problem, cfg)
    preview = {k: v for k, v in payload.items() if k != "assets"}
    preview["assets"] = (
        f"<{len(payload['assets'])} tickers x "
        f"{len(next(iter(payload['assets'].values())))} dates>"
    )
    print("--- payload ---")
    print(json.dumps(preview, indent=2))

    (out / "submission_payload.json").write_text(
        json.dumps({"preview": preview, "preflight": check}, indent=2)
    )

    if args.dry_run:
        print("\n[dry-run] Nothing submitted. Drop --dry-run to execute on QPU.")
        return
    if not check["ok"]:
        print("\nPre-flight failed; refusing to submit.")
        sys.exit(1)

    est_min = check["total_circuits"] * 0.02
    print(f"\nSubmitting to {args.backend}. Expect roughly "
          f"{est_min:.0f}-{est_min*2:.0f} min of QPU+runtime.")
    result = submit(problem, cfg, out_dir=out)

    if isinstance(result, dict) and "result" in result:
        print("\n--- optimal strategy ---")
        print(json.dumps(result["result"], indent=2))


if __name__ == "__main__":
    main()
