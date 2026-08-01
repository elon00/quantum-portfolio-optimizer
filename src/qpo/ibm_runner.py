"""
Submission layer for Global Data Quantum's `quantum-portfolio-optimizer`
Qiskit Function on IBM Quantum Platform.

This is the only module that touches IBM's cloud. It is deliberately thin and
side-effect-free until you call `submit()`, so the rest of the project stays
runnable without an account.

Access requirements (as of the current docs)
--------------------------------------------
Qiskit Functions are in preview and are limited to IBM Quantum **Premium**,
**Flex** or **On-Prem** plans, and this particular function additionally needs
a licence from Global Data Quantum. The Open (free) plan cannot run it.

Credentials are read from the environment so nothing secret lands in git:

    export IBM_QUANTUM_TOKEN="<44-character API key>"
    export IBM_QUANTUM_CRN="<your instance CRN>"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .data import to_function_payload
from .qubo import DPOProblem

__all__ = ["FunctionConfig", "build_payload", "estimate_workload", "submit", "load_result"]

FUNCTION_NAME = "global-data-quantum/quantum-portfolio-optimizer"

# Ansatzes the function accepts (per the API reference).
VALID_ANSATZE = {"real_amplitudes", "cyclic", "optimized_real_amplitudes", "tailored"}


@dataclass
class FunctionConfig:
    """Everything that is *not* the QUBO definition."""

    backend_name: str = "ibm_torino"
    ansatz: str = "optimized_real_amplitudes"
    multiple_passmanager: bool = False
    dd_enable: bool = False

    num_generations: int = 20
    population_size: int = 40
    recombination: float = 0.4
    max_parallel_jobs: int = 5
    max_batchsize: int = 4
    mutation_range: tuple[float, float] = (0.0, 0.25)

    estimator_shots: int = 25_000
    estimator_precision: float | None = None
    sampler_shots: int = 100_000

    apply_postprocess: bool = True
    tags: list[str] | None = None
    previous_session_id: list[str] | None = None

    def ansatz_settings(self) -> dict:
        if self.ansatz not in VALID_ANSATZE:
            raise ValueError(
                f"ansatz must be one of {sorted(VALID_ANSATZE)}, got '{self.ansatz}'"
            )
        return {
            "ansatz": self.ansatz,
            "multiple_passmanager": self.multiple_passmanager,
            "dd_enable": self.dd_enable,
        }

    def optimizer_settings(self) -> dict:
        return {
            "de_optimizer_settings": {
                "num_generations": self.num_generations,
                "population_size": self.population_size,
                "recombination": self.recombination,
                "max_parallel_jobs": self.max_parallel_jobs,
                "max_batchsize": self.max_batchsize,
                "mutation_range": list(self.mutation_range),
            },
            "optimizer": "differential_evolution",
            "primitive_settings": {
                "estimator_shots": self.estimator_shots,
                "estimator_precision": self.estimator_precision,
                "sampler_shots": self.sampler_shots,
            },
        }


# --------------------------------------------------------------------------
def estimate_workload(problem: DPOProblem, cfg: FunctionConfig) -> dict:
    """Pre-flight sanity check - run this *before* spending QPU time.

    Verifies the documented Qiskit Runtime job limits and the circuit budget:

      * total circuits = (num_generations + 1) * population_size
      * sampler_shots <= 10_000_000
      * max_batchsize * estimator_shots * observable_size <= 10_000_000
        (observable_size = 1 here: all terms of the Hamiltonian commute)
      * the docs advise not exceeding population_size 120 / generations 20
      * the `tailored` ansatz is only valid for ibm_torino with 7 assets,
        4 time steps and 4 resolution qubits
    """
    total_circuits = (cfg.num_generations + 1) * cfg.population_size
    warnings: list[str] = []
    errors: list[str] = []

    if cfg.sampler_shots > 10_000_000:
        errors.append(f"sampler_shots={cfg.sampler_shots} exceeds the 10M limit")

    est_load = cfg.max_batchsize * cfg.estimator_shots * 1
    if est_load > 10_000_000:
        errors.append(
            f"max_batchsize * estimator_shots = {est_load} exceeds the 10M limit"
        )

    if cfg.population_size > 120:
        warnings.append(f"population_size={cfg.population_size} > 120 (docs advise against)")
    if cfg.num_generations > 20:
        warnings.append(f"num_generations={cfg.num_generations} > 20 (docs advise against)")

    # Docs' rule of thumb for the real_amplitudes family.
    min_pop = int(0.8 * problem.num_qubits)
    if cfg.ansatz == "real_amplitudes" and cfg.population_size < min_pop:
        warnings.append(
            f"population_size={cfg.population_size} is below the recommended "
            f"minimum ~0.8*n_qubits = {min_pop} for real_amplitudes"
        )

    if cfg.ansatz == "tailored":
        ok = (
            cfg.backend_name == "ibm_torino"
            and problem.na == 7
            and problem.nt == 4
            and problem.nq == 4
        )
        if not ok:
            errors.append(
                "the 'tailored' ansatz requires ibm_torino with exactly "
                f"7 assets / 4 time steps / 4 resolution qubits; this instance is "
                f"{problem.na}/{problem.nt}/{problem.nq} on {cfg.backend_name}"
            )

    needed_rows = (problem.nt + 1) * problem.settings.dt
    if problem.prices is not None and len(problem.prices) < needed_rows:
        errors.append(
            f"price history has {len(problem.prices)} rows, needs (nt+1)*dt = {needed_rows}"
        )

    return {
        "n_qubits": problem.num_qubits,
        "total_circuits": total_circuits,
        "estimator_load": est_load,
        "warnings": warnings,
        "errors": errors,
        "ok": not errors,
    }


def build_payload(problem: DPOProblem, cfg: FunctionConfig) -> dict:
    """Assemble the exact kwargs for `dpo_solver.run(...)`."""
    return {
        "assets": to_function_payload(problem.prices),
        "qubo_settings": problem.settings.to_function_dict(),
        "optimizer_settings": cfg.optimizer_settings(),
        "ansatz_settings": cfg.ansatz_settings(),
        "backend_name": cfg.backend_name,
        "previous_session_id": cfg.previous_session_id or [],
        "apply_postprocess": cfg.apply_postprocess,
        "tags": cfg.tags or [],
    }


def _catalog():
    from qiskit_ibm_catalog import QiskitFunctionsCatalog

    token = os.getenv("IBM_QUANTUM_TOKEN")
    crn = os.getenv("IBM_QUANTUM_CRN")
    if not token:
        raise RuntimeError(
            "IBM_QUANTUM_TOKEN is not set. Export your 44-character API key:\n"
            "  export IBM_QUANTUM_TOKEN=...\n"
            "  export IBM_QUANTUM_CRN=...   # your instance CRN"
        )
    kwargs: dict[str, Any] = {"channel": "ibm_quantum_platform", "token": token}
    if crn:
        kwargs["instance"] = crn
    return QiskitFunctionsCatalog(**kwargs)


def submit(
    problem: DPOProblem,
    cfg: FunctionConfig,
    out_dir: str | os.PathLike = "results/data",
    poll_seconds: int = 60,
    wait: bool = True,
) -> dict:
    """Submit the instance to the real Qiskit Function and persist the result.

    Returns the function's `{"result": ..., "metadata": ...}` dictionary.
    """
    check = estimate_workload(problem, cfg)
    for w in check["warnings"]:
        print(f"[warn] {w}")
    if not check["ok"]:
        for e in check["errors"]:
            print(f"[error] {e}")
        raise ValueError("Pre-flight checks failed; refusing to spend QPU time.")

    print(
        f"[ibm] {check['n_qubits']} qubits, {check['total_circuits']} circuits, "
        f"backend={cfg.backend_name}, ansatz={cfg.ansatz}"
    )

    catalog = _catalog()
    solver = catalog.load(FUNCTION_NAME)
    payload = build_payload(problem, cfg)

    job = solver.run(**payload)
    job_id = getattr(job, "job_id", None)
    print(f"[ibm] submitted job_id={job_id}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "last_job_id.txt").write_text(str(job_id))

    if not wait:
        return {"job_id": job_id, "job": job}

    while True:
        status = job.status()
        print(f"[ibm] status={status}")
        if str(status).upper() in {"DONE", "ERROR", "CANCELED", "COMPLETED"}:
            break
        time.sleep(poll_seconds)

    result = job.result()
    path = out_dir / f"qpu_result_{job_id}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    print(f"[ibm] saved -> {path}")

    # The session id lets you resume this optimisation later with a larger
    # num_generations, without re-running the circuits already executed.
    sid = result.get("metadata", {}).get("session_id")
    if sid:
        print(f"[ibm] session_id={sid}  (pass in previous_session_id to resume)")
    return result


def load_result(path: str | os.PathLike) -> dict:
    """Read a saved QPU result and normalise `all_samples_metrics` to a frame."""
    data = json.loads(Path(path).read_text())
    metrics = data.get("metadata", {}).get("all_samples_metrics")
    if metrics:
        data["metrics_frame"] = pd.DataFrame(metrics)
    return data
