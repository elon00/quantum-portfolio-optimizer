# Quantum Portfolio Optimizer — Dynamic Portfolio Optimization on IBM Quantum

A complete, working project built on **Global Data Quantum's Quantum Portfolio
Optimizer**, a Qiskit Function on IBM Quantum Platform.

It solves the **Dynamic Portfolio Optimization (DPO)** problem: choosing how to
split a budget across assets at several rebalancing dates so as to maximise
return, minimise risk, minimise transaction costs, and respect a budget
constraint — all at once.

The whole pipeline **runs locally today, with no IBM Quantum account**, and the
same problem instance can be pushed to a real QPU with one flag.

---

## Why this project is structured this way

The Qiskit Function is a managed black box: you hand it prices and settings, it
hands back trajectories. That is convenient, but it makes two things hard —
(a) knowing whether your problem is set up correctly *before* spending premium
QPU time, and (b) knowing whether the answer you got back is any good.

This project solves both by re-implementing the published formulation locally:

- a **verified QUBO builder** — unit tests assert the matrix energy equals the
  analytic objective of Eq. 15 for hundreds of random bitstrings;
- **local solvers** (exact brute force, simulated annealing, and a real
  VQE + Differential Evolution loop) that give you ground truth to compare the
  QPU output against;
- a **pre-flight validator** that checks IBM's documented runtime limits and
  the function's ansatz restrictions before submission.

So the QPU run is the *last* step, not the first.

---

## Results (already produced, reproducible)

Local VQE with Differential Evolution, benchmarked against exhaustive search:

| Qubits | Assets × steps | Approx. ratio | Found exact optimum |
|-------:|----------------|--------------:|---------------------|
| 8      | 2 × 2          | 1.0000        | ✅ |
| 12     | 3 × 2          | 1.0000        | ✅ |
| 16     | 2 × 4          | 0.9810        | — |
| 16     | 4 × 2          | 0.9980        | — |
| 18     | 3 × 3          | 1.0000        | ✅ |
| 20     | 5 × 2          | 0.9971        | — |

*Approximation ratio = (random_mean − VQE) / (random_mean − exact); 1.0 means
the exact optimum was recovered.*

On the IBEX instance the optimizer recovers the exact optimum while the random
baseline averages **+0.369** against the optimum's **+0.025** — the quantum
distribution is sharply concentrated at low cost, reproducing the qualitative
result of Fig. 1 of the paper.

Note the runtime column in `results/*/scaling.csv`: local statevector
simulation goes from 1.3 s at 8 qubits to **1079 s at 20 qubits**. That
exponential wall is precisely why the 56- and 112-qubit instances need real
hardware.

---

## Quick start

```bash
pip install -r requirements.txt

# verify the maths first (7 tests, ~5s)
python tests/test_qubo.py

# full local study on the IBEX 35 portfolio from the official tutorial
python scripts/run_experiment.py --portfolio ibex

# the same pipeline on an Indian large-cap (NSE) portfolio
python scripts/run_experiment.py --portfolio nifty
```

Artifacts land in `results/<portfolio>/`: seven figures, `summary.json`,
`solver_comparison.csv`, `optimal_strategy.csv`, `scaling.csv`.

---

## Running on a real QPU

Qiskit Functions require an IBM Quantum **Premium, Flex or On-Prem** plan, plus
a licence from Global Data Quantum. The Open (free) plan cannot run this
function.

```bash
export IBM_QUANTUM_TOKEN="<44-character API key>"
export IBM_QUANTUM_CRN="<your instance CRN>"

# ALWAYS dry-run first — validates everything, costs nothing
python scripts/submit_qpu.py --portfolio ibex --dry-run

# the official tutorial instance: 7 assets × 4 steps × 2 bits = 56 qubits
python scripts/submit_qpu.py --portfolio ibex --nt 4 --nq 2

# the paper's XXL instance: 112 qubits with the hardware-tailored ansatz
python scripts/submit_qpu.py --portfolio ibex --nt 4 --nq 4 \
    --max-investment 25 --ansatz tailored --population 120

# resume an interrupted optimisation without re-running finished circuits
python scripts/submit_qpu.py --portfolio ibex --resume <session_id>
```

The dry run prints the exact payload and a pre-flight report:

```
--- pre-flight ---
  qubits          : 56  (na*nt*nq)
  total circuits  : 840  = (generations+1) * population
  estimator load  : 100,000 / 10,000,000
  sampler shots   : 100,000 / 10,000,000
  status          : OK
```

It refuses to submit when something is wrong — for example the `tailored`
ansatz is only valid on `ibm_torino` with exactly 7 assets / 4 steps / 4
resolution qubits:

```
  [error] the 'tailored' ansatz requires ibm_torino with exactly 7 assets /
          4 time steps / 4 resolution qubits; this instance is 7/4/2
  status          : BLOCKED
```

---

## The problem being solved

Minimise, over binary investment variables:

```
Q = Σ_t [ −μ_tᵀ ω̃_t                    ← maximise return       (Eq. 3–4)
        + (γ/2) ω̃_tᵀ Σ_t ω̃_t          ← minimise risk         (Eq. 5)
        + ν λ (ω̃_t − ω̃_{t−1})²        ← transaction costs     (Eq. 6, 14)
        + ρ (Σ_a ω̃_{t,a} − 1)² ]       ← budget constraint     (Eq. 12)
```

with the investment encoded in binary (Eq. 16):

```
ω̃_{t,a} = (1/K) Σ_r 2^r x_{t,a,r}     →  qubits = n_assets × n_steps × n_bits
```

where `μ` are period log-returns, `Σ_t` the covariance of daily log-returns
inside each window, `γ` risk aversion, `ν` the transaction fee,
`λ = ∛2·K/K'` the quadratic-approximation factor for `|·|`, and `ρ` the
Lagrange multiplier. `K = max_investment`, `K' = 2^nq − 1`.

Reference: Á. Nodar, I. De León et al., *Scaling the Variational Quantum
Eigensolver for Dynamic Portfolio Optimization*,
[arXiv:2412.19150](https://arxiv.org/abs/2412.19150).

---

## Layout

```
src/qpo/
  data.py        price loading; Yahoo → GDQ fallback CSV → synthetic, always
                 gap-filled so every asset shares one date index
  qubo.py        DPO → QUBO (Eq. 15/16), decoding, financial metrics
  solvers.py     random / brute force / annealing / VQE+DE, QUBO↔Ising
  ibm_runner.py  Qiskit Function payload, pre-flight limits, submit & persist
  plots.py       the seven figures
scripts/
  run_experiment.py  full local study
  submit_qpu.py      hardware submission with dry-run
tests/
  test_qubo.py   correctness of the formulation
docs/
  PROJECT_GUIDE.md   milestones, tuning guide, troubleshooting
```

---

## Key parameters

| Parameter | Meaning | Effect |
|---|---|---|
| `nt` | rebalancing steps | ↑ qubits linearly |
| `nq` | bits per investment | ↑ qubits linearly; max single-asset weight = (2^nq−1)/K |
| `max_investment` (K) | budget in currency units | with `nq`, sets weight granularity |
| `risk_aversion` (γ) | risk penalty | 1000 puts risk and return on the same scale |
| `transaction_fee` (ν) | per-unit rebalancing cost | ↑ makes the strategy stickier |
| `restriction_coeff` (ρ) | budget-constraint multiplier | ↑ reduces breaches, can dominate the objective |
| `population_size` | DE population | ≥ 0.8 × qubits for `real_amplitudes`; more for `optimized_real_amplitudes` |

Total circuits = `(num_generations + 1) × population_size`. The docs advise
staying at or below 120 population and 20 generations (2520 circuits).

---

## Data note

`load_prices` tries Yahoo Finance, then the public Global Data Quantum fallback
CSV that IBM's own tutorial uses, then a deterministic synthetic generator, so
the project never dead-ends on a network failure. The source used is recorded
in `summary.json`. Results above were produced from live Yahoo Finance data.

---

## Cryptocurrency & post-quantum (PQC) portfolios

An extension that optimizes crypto portfolios **and** treats quantum
vulnerability as a first-class risk factor — using a quantum computer to
allocate toward quantum-*resistant* assets.

```bash
python tests/test_crypto.py                                    # 8 tests

python scripts/run_crypto.py --universe mixed  --theta 0.1     # spans the spectrum
python scripts/run_crypto.py --universe pqc    --theta 0.1     # PQC-focused chains
python scripts/run_crypto.py --universe majors --theta 0.1     # classical large caps

# the quantum-safety frontier
python scripts/run_crypto.py --universe mixed \
    --theta-sweep 0 0.05 0.1 0.2 0.3 0.5 0.8 1.2
```

### The three universes

| Universe | Assets |
|---|---|
| `pqc` | QRL, ALGO, CELL, ABEL, XX, IOTA, QNT |
| `majors` | BTC, ETH, SOL, XRP, BNB, ADA, LINK |
| `mixed` | BTC, ETH, SOL, ALGO, HBAR, QRL, CELL |

Each asset carries a documented cryptographic posture and a vulnerability
score `v ∈ [0,1]` (see `src/qpo/crypto.py`), e.g.:

| Asset | Tier | Signature scheme | v |
|---|---|---|---:|
| QRL | pqc | XMSS (hash-based, IETF RFC 8391) | 0.05 |
| Cellframe | pqc | Dilithium / Falcon / SPHINCS+ | 0.15 |
| Algorand | partial | Ed25519 + Falcon-1024 state proofs | 0.35 |
| Bitcoin | classical | ECDSA / Schnorr (secp256k1) | 0.95 |

Grounded in NIST's PQC standards (ML-KEM/FIPS 203, ML-DSA/FIPS 204,
SLH-DSA/FIPS 205, Falcon selected for future standardization), QRL's
IETF-specified XMSS, and Algorand's Falcon State Proofs (2022) plus its first
Falcon-1024 mainnet transaction (Nov 2025).

### How the quantum tilt reaches a black-box function

The hosted Qiskit Function accepts exactly seven QUBO settings — there is no
hook for a custom penalty term. But penalising vulnerable weights,

```
+ θ Σ_t Σ_a v_a ω̃_{t,a}
```

is algebraically identical to replacing `μ_{t,a}` with `μ_{t,a} − θ v_a`, which
in turn is exactly what a geometric drag on the price path produces:

```
P'_{s,a} = P_{s,a} · exp(−θ v_a s / dt)
```

So the tilt travels *inside the price data*. `test_crypto.py` verifies the
equivalence numerically: the return shift equals `θ·v` to 1e-12, and the
covariance matrix is unchanged to 1e-14. **No API extension needed.**

### Results

Quantum-safety frontier on the `mixed` universe (16-qubit local instance,
VQE approximation ratio **1.0000**):

| θ | quantum exposure | return |
|---:|---:|---:|
| 0.0 | 0.845 | −0.127 |
| 0.05 | 0.839 | −0.073 |
| 0.2 | 0.778 | −0.069 |
| 0.3 | 0.762 | −0.065 |
| 0.5 | 0.425 | −0.202 |
| 0.8 | 0.200 | −0.316 |
| 1.2 | 0.162 | −0.388 |

Exposure falls monotonically from 0.845 to 0.162 — the portfolio migrates from
ECDSA-secured majors into hash-based and lattice-based chains. On the `pqc`
universe the tilt cuts exposure by **26.5%**. On `majors` it correctly changes
nothing: all seven assets are classical, so there is nothing to rotate into.

Two crypto-specific calibrations, both verified by sweep rather than assumed:

- **γ is auto-calibrated** (`calibrate_risk_aversion`). Crypto runs at 44–141%
  annualised volatility, so the paper's equity value of γ=1000 lets the risk
  term swamp returns; the data-driven value lands near 140–250.
- **ρ defaults to 2.0** for crypto, not 1.0. At ρ=1 the optimizer tolerated a
  20% budget breach; ρ=2 enforces the constraint exactly.

### Interpreting this honestly

θ is a *modelling choice*, not a market price — nobody knows the arrival date
of cryptographically-relevant quantum hardware. The frontier's value is that it
makes the trade-off explicit and auditable: at each θ you can read off exactly
what quantum safety costs in realised return. Returns in the table are negative
because the 2025 sample window was a drawdown for these assets; the tilt's
effect on *exposure* is the finding, not the sign of the return. None of this
is investment advice.
