# Verification & audit report

Date: 2026-07-31. Everything below was executed, not asserted.

## Summary

| Area | Status |
|---|---|
| Test suite (18 tests) | ✅ all pass |
| QUBO correctness | ✅ confirmed by a second independent implementation |
| Published README numbers | ✅ reproduced exactly |
| CLI entry points | ✅ all 3 run |
| IBM submission path | ⚠️ verified against a **mock**, never a real QPU |
| Credentials / secrets | ✅ env-only, none committed |

---

## 1. Test suite — 18 tests, all passing

`tests/test_qubo.py` (11):

```
PASS  QUBO matrix == analytic Eq.15 objective (300 random bitstrings)
PASS  QUBO -> Ising mapping is energy-preserving
PASS  qubit count == na*nt*nq
PASS  max decoded weight == K'/K == 60.00%
PASS  metrics consistent on the empty-portfolio edge case
PASS  infeasible budget configuration rejected up front
PASS  VQE-DE beats random and lands near the exact optimum
PASS  all ansatzes build and differ 18/18/30 params; unknown ansatz rejected
PASS  brute force and VQE both refuse oversized instances
PASS  payload rectangular (4 assets x 160 dates), JSON-serialisable
```

`tests/test_crypto.py` (8): tilt exactness, θ=0 no-op, monotonicity in
vulnerability, exposure reduction, γ calibration, taxonomy well-formedness,
universe spread, report coverage.

## 2. Independent re-derivation of the QUBO

The matrix builder was checked against a **separately written** triple-loop
evaluation of Eq. 15 (no shared code path), on a non-default configuration
(γ=777, ν=0.023, ρ=1.7, K=6):

```
2000/2000 bitstrings agree, mismatches=0
```

## 3. Reproducibility of published claims

Crypto frontier (`mixed`, 16 qubits) — reran, matches the README row for row:

```
theta=0.000 exposure=0.845   theta=0.300 exposure=0.762
theta=0.050 exposure=0.839   theta=0.500 exposure=0.425
theta=0.100 exposure=0.839   theta=0.800 exposure=0.200
theta=0.200 exposure=0.778   theta=1.200 exposure=0.162
approximation ratio: 1.0000
```

Equity (IBEX, live Yahoo data): random mean +0.36948, exact +0.02506, VQE
+0.02506, ratio 1.0000. Scaling ratios 1.0000 / 1.0000 / 0.9810 / 0.9980 /
1.0000 — all as published.

## 4. Ansatz differentiation

An earlier run showed all three ansatzes returning identical costs, which
looked like a copy-paste bug. Investigated: the instance was simply trivial.
On a 16-qubit instance they diverge, and the circuits are structurally
distinct:

| Ansatz | Params | Depth | Ops |
|---|---:|---:|---|
| `real_amplitudes` | 18 | 10 | ry 18, cx 10 |
| `cyclic` | 18 | 15 | ry 18, cx 12 |
| `optimized_real_amplitudes` | 30 | 12 | ry 18, rz 12, cx 10 |

## 5. IBM submission path — mock-executed

`submit()` cannot be run for real without a Premium/Flex licence, so it was
executed against a stubbed `qiskit_ibm_catalog`. Verified: catalog auth kwargs,
function name `global-data-quantum/quantum-portfolio-optimizer`, all 8 payload
keys matching the documented API exactly, status polling, result persistence,
`last_job_id.txt`, and session-id capture for resuming.

Pre-flight guards confirmed to block: `tailored` ansatz on a non-conforming
instance, and `max_batchsize × estimator_shots` over the 10M limit.

## 6. Robustness

- Offline fallback: with Yahoo and the remote CSV forced to fail, the pipeline
  still completes on deterministic synthetic data (0 NaNs, reproducible).
- `allow_synthetic=False` raises rather than silently fabricating data.
- Guardrails refuse >22-qubit brute force and >24-qubit statevector VQE.
- No hardcoded credentials; token/CRN read from environment only.

## 7. Known limitations (explicitly not done)

1. **Never run on real quantum hardware.** Every quantum result is noiseless
   statevector simulation ≤20 qubits. The 56- and 112-qubit instances have
   only been validated as payloads.
2. **No SQD post-processing locally.** `apply_postprocess` is passed to the
   function; the noise-aware step is theirs, not reproduced here.
3. **Local VQE is noiseless.** No device noise model, so it is an upper bound
   on hardware behaviour, not a prediction of it.
4. **θ and vulnerability scores are modelling choices**, not market-implied or
   externally certified values.
5. **Returns in the crypto tables are negative** — the 2025 window was a
   drawdown. The exposure trade-off is the finding, not the return sign.
6. **No backtest** of the chosen trajectory against a held-out period yet.
