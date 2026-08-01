"""
qpo - Dynamic Portfolio Optimization with Global Data Quantum's
Quantum Portfolio Optimizer (a Qiskit Function on IBM Quantum Platform).

Modules
-------
data        price acquisition and conditioning
qubo        DPO -> QUBO construction (arXiv:2412.19150, Eq. 15/16)
crypto      crypto universes, PQC risk taxonomy, quantum-risk tilt
solvers     local baselines + a local VQE/Differential-Evolution solver
ibm_runner  submission layer for the real Qiskit Function
plots       figures
"""

from .crypto import (
    MAJORS_UNIVERSE,
    MIXED_UNIVERSE,
    PQC_PROFILES,
    PQC_UNIVERSE,
    apply_quantum_risk_adjustment,
    calibrate_risk_aversion,
    pqc_report,
    vulnerability_vector,
)
from .data import IBEX_PORTFOLIO, NIFTY_PORTFOLIO, load_prices, to_function_payload
from .qubo import DPOProblem, QUBOSettings, build_dpo_problem
from .solvers import (
    SolveResult,
    solve_bruteforce,
    solve_random,
    solve_simulated_annealing,
    solve_vqe_de,
)

__version__ = "1.0.0"

__all__ = [
    "PQC_PROFILES",
    "PQC_UNIVERSE",
    "MAJORS_UNIVERSE",
    "MIXED_UNIVERSE",
    "apply_quantum_risk_adjustment",
    "calibrate_risk_aversion",
    "pqc_report",
    "vulnerability_vector",
    "IBEX_PORTFOLIO",
    "NIFTY_PORTFOLIO",
    "load_prices",
    "to_function_payload",
    "DPOProblem",
    "QUBOSettings",
    "build_dpo_problem",
    "SolveResult",
    "solve_bruteforce",
    "solve_random",
    "solve_simulated_annealing",
    "solve_vqe_de",
]
