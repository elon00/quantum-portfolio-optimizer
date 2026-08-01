"""
Solvers and baselines for the DPO QUBO.

Everything here runs locally, with no IBM Quantum account, so the whole
project is verifiable before a single second of QPU time is spent:

* `solve_random`          - uniform random bitstrings (the "offset" baseline
                            the paper compares against).
* `solve_bruteforce`      - exhaustive search, exact optimum (small instances).
* `solve_simulated_annealing` - strong classical heuristic reference.
* `solve_vqe_de`          - a *local* Variational Quantum Eigensolver that
                            mirrors the Qiskit Function: Ising Hamiltonian +
                            Real-Amplitudes-family ansatz + Differential
                            Evolution outer loop + sampling + CVaR-style
                            post-selection.

The VQE here is a statevector simulation, so it is limited to roughly 24
qubits. Above that, use the real Qiskit Function (see `qpo.ibm_runner`),
which is exactly what the hardware path is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .qubo import DPOProblem

__all__ = [
    "SolveResult",
    "qubo_to_ising",
    "solve_random",
    "solve_bruteforce",
    "solve_simulated_annealing",
    "solve_vqe_de",
]


# --------------------------------------------------------------------------
@dataclass
class SolveResult:
    """Uniform result container for every solver."""

    name: str
    best_x: np.ndarray
    best_cost: float
    costs: list[float] = field(default_factory=list)  # all sampled costs
    history: list[float] = field(default_factory=list)  # convergence curve
    info: dict = field(default_factory=dict)

    def report(self, problem: DPOProblem) -> dict:
        m = problem.metrics(self.best_x)
        m["solver"] = self.name
        return m


# --------------------------------------------------------------------------
# QUBO <-> Ising
# --------------------------------------------------------------------------
def qubo_to_ising(Q: np.ndarray, offset: float = 0.0):
    """Map x^T Q x + offset  (x∈{0,1})  to  Σ h_i Z_i + Σ J_ij Z_i Z_j + c.

    Substituting x_i = (1 - z_i)/2 with z_i = ±1.
    """
    n = Q.shape[0]
    Qs = (Q + Q.T) / 2.0  # symmetrise
    diag = np.diag(Qs).copy()
    offdiag = Qs - np.diag(diag)

    # x^T Q x = Σ_i Q_ii x_i + Σ_{i≠j} Q_ij x_i x_j
    J = offdiag / 4.0
    h = -diag / 2.0 - offdiag.sum(axis=1) / 2.0
    const = offset + diag.sum() / 2.0 + offdiag.sum() / 4.0
    return h, J, float(const)


def _ising_energy_from_bits(bits: np.ndarray, Q: np.ndarray, offset: float) -> np.ndarray:
    """Vectorised QUBO energy for a batch of bitstrings, shape (m, n)."""
    return np.einsum("mi,ij,mj->m", bits, Q, bits) + offset


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------
def solve_random(problem: DPOProblem, n_samples: int = 20000, seed: int = 0) -> SolveResult:
    """Uniform random sampling - the reference 'offset' distribution."""
    rng = np.random.default_rng(seed)
    n = problem.num_qubits
    bits = rng.integers(0, 2, size=(n_samples, n)).astype(float)
    costs = _ising_energy_from_bits(bits, problem.Q, problem.offset)
    k = int(np.argmin(costs))
    return SolveResult(
        name="Random",
        best_x=bits[k].astype(int),
        best_cost=float(costs[k]),
        costs=costs.tolist(),
        info={"n_samples": n_samples, "mean_cost": float(costs.mean())},
    )


def solve_bruteforce(problem: DPOProblem, max_qubits: int = 22) -> SolveResult:
    """Exact optimum by exhaustive enumeration (only for small instances)."""
    n = problem.num_qubits
    if n > max_qubits:
        raise ValueError(
            f"Brute force refused: {n} qubits > {max_qubits}. "
            "Use simulated annealing as the classical reference instead."
        )
    m = 1 << n
    idx = np.arange(m, dtype=np.uint64)
    bits = ((idx[:, None] >> np.arange(n, dtype=np.uint64)[::-1]) & 1).astype(float)
    costs = _ising_energy_from_bits(bits, problem.Q, problem.offset)
    k = int(np.argmin(costs))
    return SolveResult(
        name="Exact (brute force)",
        best_x=bits[k].astype(int),
        best_cost=float(costs[k]),
        costs=costs.tolist(),
        info={"n_states": m},
    )


def solve_simulated_annealing(
    problem: DPOProblem,
    n_restarts: int = 30,
    n_sweeps: int = 4000,
    seed: int = 0,
) -> SolveResult:
    """Classical benchmark: multi-restart simulated annealing with delta-energy
    updates (the role Gurobi/CPLEX plays in the paper)."""
    rng = np.random.default_rng(seed)
    Q = (problem.Q + problem.Q.T) / 2.0
    n = problem.num_qubits
    diag = np.diag(Q).copy()

    best_x, best_e = None, np.inf
    all_costs: list[float] = []
    history: list[float] = []

    for _ in range(n_restarts):
        x = rng.integers(0, 2, size=n).astype(float)
        e = float(x @ Q @ x + problem.offset)
        # local field: dE for flipping i is (1-2x_i)(Q_ii + 2 Σ_{j≠i} Q_ij x_j)
        field_ = 2.0 * (Q @ x) - 2.0 * diag * x

        T0, T1 = 2.0 * max(abs(diag).max(), 1e-9), 1e-4
        for s in range(n_sweeps):
            T = T0 * (T1 / T0) ** (s / max(n_sweeps - 1, 1))
            i = rng.integers(n)
            sign = 1.0 - 2.0 * x[i]
            dE = sign * (diag[i] + field_[i])
            if dE <= 0 or rng.random() < np.exp(-dE / T):
                x[i] += sign
                e += dE
                field_ += 2.0 * sign * Q[:, i]
                field_[i] -= 2.0 * sign * Q[i, i]
        all_costs.append(e)
        history.append(min(best_e, e))
        if e < best_e:
            best_e, best_x = e, x.copy()

    return SolveResult(
        name="Simulated annealing",
        best_x=best_x.astype(int),
        best_cost=float(best_e),
        costs=all_costs,
        history=history,
        info={"n_restarts": n_restarts, "n_sweeps": n_sweeps},
    )


# --------------------------------------------------------------------------
# Local VQE with Differential Evolution
# --------------------------------------------------------------------------
def _build_ansatz(n_qubits: int, reps: int, kind: str):
    """Real-Amplitudes-family ansatz, matching the function's options.

    `real_amplitudes`           - RY layers + linear CX entanglement.
    `optimized_real_amplitudes` - adds a second rotation axis per layer, the
                                  richer parameterisation the paper reports as
                                  best-performing below ~100 qubits (at the
                                  cost of more parameters).
    `cyclic`                    - ring entanglement.
    """
    from qiskit import QuantumCircuit
    from qiskit.circuit import ParameterVector

    kind = kind.lower()
    if kind == "real_amplitudes":
        n_params = n_qubits * (reps + 1)
    elif kind == "optimized_real_amplitudes":
        n_params = n_qubits * (reps + 1) + n_qubits * reps
    elif kind == "cyclic":
        n_params = n_qubits * (reps + 1)
    else:
        raise ValueError(f"Unknown ansatz '{kind}'")

    theta = ParameterVector("θ", n_params)
    qc = QuantumCircuit(n_qubits)
    p = 0

    for q in range(n_qubits):
        qc.ry(theta[p], q)
        p += 1

    for layer in range(reps):
        if kind == "cyclic":
            pairs = [(q, (q + 1) % n_qubits) for q in range(n_qubits)]
        else:
            pairs = [(q, q + 1) for q in range(n_qubits - 1)]
        for a, b in pairs:
            qc.cx(a, b)
        if kind == "optimized_real_amplitudes":
            for q in range(n_qubits):
                qc.rz(theta[p], q)
                p += 1
        for q in range(n_qubits):
            qc.ry(theta[p], q)
            p += 1

    return qc, n_params


def solve_vqe_de(
    problem: DPOProblem,
    ansatz: str = "optimized_real_amplitudes",
    reps: int = 2,
    num_generations: int = 20,
    population_size: int | None = None,
    recombination: float = 0.4,
    mutation_range: tuple[float, float] = (0.0, 0.25),
    sampler_shots: int = 20000,
    seed: int = 0,
    max_qubits: int = 24,
    callback: Callable[[int, float], None] | None = None,
) -> SolveResult:
    """Local statevector VQE driven by Differential Evolution.

    This mirrors the Qiskit Function's pipeline end-to-end:
      QUBO -> Ising Hamiltonian -> hardware-efficient ansatz
      -> DE minimisation of <H> -> final sampling -> best measured bitstring.

    Because it is a noiseless statevector simulation it is capped at
    `max_qubits`; the point is to validate the formulation and the workflow,
    not to compete with the QPU path.
    """
    from qiskit.quantum_info import Statevector

    n = problem.num_qubits
    if n > max_qubits:
        raise ValueError(
            f"Local VQE refused: {n} qubits > {max_qubits} (statevector limit). "
            "Reduce nt/nq/assets for the local run, or submit this instance to "
            "the real Qiskit Function via qpo.ibm_runner."
        )

    rng = np.random.default_rng(seed)
    qc, n_params = _build_ansatz(n, reps, ansatz)

    if population_size is None:
        # Docs' rule of thumb: population_size ≈ 0.8 * n_qubits (min), and the
        # optimized ansatz needs more. Keep it bounded for local runtime.
        base = max(12, int(0.8 * n))
        population_size = base * 2 if ansatz == "optimized_real_amplitudes" else base
        population_size = min(population_size, 60)

    # Pre-compute the diagonal cost vector: the QUBO Hamiltonian is diagonal in
    # the computational basis, so <H> is just  Σ_b |amp_b|² cost(b).
    m = 1 << n
    idx = np.arange(m, dtype=np.uint64)
    bits = ((idx[:, None] >> np.arange(n, dtype=np.uint64)[::-1]) & 1).astype(float)
    cost_vec = _ising_energy_from_bits(bits, problem.Q, problem.offset)

    def expectation(params: np.ndarray) -> float:
        bound = qc.assign_parameters(params)
        probs = np.abs(Statevector.from_instruction(bound).data) ** 2
        return float(probs @ cost_vec)

    # ---- Differential Evolution (rand/1/bin), as used by the function -------
    pop = rng.uniform(-np.pi, np.pi, size=(population_size, n_params))
    fitness = np.array([expectation(p) for p in pop])
    history = [float(fitness.min())]

    for gen in range(num_generations):
        for i in range(population_size):
            choices = [j for j in range(population_size) if j != i]
            a, b, c = rng.choice(choices, size=3, replace=False)
            F = rng.uniform(*mutation_range) if mutation_range[1] > 0 else 0.5
            mutant = pop[a] + F * (pop[b] - pop[c])
            cross = rng.random(n_params) < recombination
            if not cross.any():
                cross[rng.integers(n_params)] = True
            trial = np.where(cross, mutant, pop[i])
            f = expectation(trial)
            if f <= fitness[i]:
                pop[i], fitness[i] = trial, f
        history.append(float(fitness.min()))
        if callback is not None:
            callback(gen, float(fitness.min()))

    best_params = pop[int(np.argmin(fitness))]

    # ---- Final sampling from the optimised state ---------------------------
    bound = qc.assign_parameters(best_params)
    probs = np.abs(Statevector.from_instruction(bound).data) ** 2
    probs = np.clip(probs, 0, None)
    probs /= probs.sum()
    draws = rng.choice(m, size=sampler_shots, p=probs)
    uniq, counts = np.unique(draws, return_counts=True)

    sampled_costs = cost_vec[uniq]
    k = int(np.argmin(sampled_costs))
    best_state = int(uniq[k])
    best_x = bits[best_state].astype(int)

    # Expand counts into a cost distribution, like `all_samples_metrics`.
    costs = np.repeat(sampled_costs, counts).tolist()

    return SolveResult(
        name=f"VQE-DE ({ansatz})",
        best_x=best_x,
        best_cost=float(sampled_costs[k]),
        costs=costs,
        history=history,
        info={
            "n_qubits": n,
            "n_params": n_params,
            "population_size": population_size,
            "num_generations": num_generations,
            "reps": reps,
            "final_expectation": float(fitness.min()),
            "n_unique_sampled": int(len(uniq)),
            "circuit_evaluations": population_size * (num_generations + 1),
        },
    )
