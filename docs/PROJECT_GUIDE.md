# Project guide — taking this from local run to finished deliverable

This is the practical companion to the README: what to do, in what order, and
what to do when something breaks.

---

## Milestones

### M1 — Validate the formulation (done, no account needed)

```bash
python tests/test_qubo.py
```

Seven tests. The important one asserts that the QUBO matrix energy equals the
analytically evaluated objective (Eq. 15) for 300 random bitstrings. If you
change anything in `qubo.py`, this is the test that tells you whether you broke
the physics.

**Evidence for your report:** the test output, plus `06_qubo_matrix.png`, which
shows the block-diagonal structure — one block per time step, with the
off-diagonal blocks being exactly the transaction-cost coupling that makes the
problem *dynamic* rather than a set of independent single-period problems.

### M2 — Local benchmark study (done)

```bash
python scripts/run_experiment.py --portfolio ibex
python scripts/run_experiment.py --portfolio nifty
```

Produces the solver comparison, the cost distributions, and the scaling sweep.

**Evidence:** `02_cost_distributions.png` is your version of Fig. 1 in the
paper — the quantum distribution concentrated at low cost, the random baseline
broad and centred high, the classical optimum as a dashed line.

### M3 — QPU dry run (needs no QPU time)

```bash
python scripts/submit_qpu.py --portfolio ibex --dry-run
```

Confirms the payload matches the documented API and that you are inside IBM's
runtime limits. Save `results/<portfolio>/submission_payload.json` — it is
proof the integration is correct even if hardware access is pending.

### M4 — Hardware execution

```bash
python scripts/submit_qpu.py --portfolio ibex --nt 4 --nq 2
```

Budget roughly 55 minutes on a Heron r2 processor for the 56-qubit tutorial
instance (IBM's own estimate). The result JSON is written to
`results/<portfolio>/qpu_result_<job_id>.json`.

**Record the `session_id`.** It lets you resume with more generations later
instead of paying for the same circuits twice.

### M5 — Analysis

Compare the QPU result against your local classical references:

```python
from qpo.ibm_runner import load_result
import pandas as pd

data = load_result("results/ibex/qpu_result_<job_id>.json")
df = data["metrics_frame"]           # all_samples_metrics as a DataFrame
best = df.loc[df["objective_costs"].idxmin()]
print(best[["objective_costs", "sharpe_ratios", "returns", "rest_breaches"]])
```

The honest framing for a write-up: at 56 qubits a classical solver still wins
on wall-clock. The scientific claim is *scaling behaviour and solution
quality*, not present-day supremacy. Say so explicitly — it is the difference
between a credible project and an overclaimed one.

---

## Tuning guide

**Constraint breaches too high** (`rest_breaches` large)
Raise `restriction_coeff` (ρ). Watch for it dominating: if returns collapse and
every strategy has exactly 100% budget, ρ is too high.

**All weight in one asset**
`max_investment` (K) is too low relative to `2^nq − 1`. Max single-asset weight
is `(2^nq − 1)/K`, so `nq=2, K=5` caps any asset at 60%. Raise K to diversify.

**Poor optimisation quality on hardware**
1. Increase `population_size` — the docs' floor is ~0.8 × qubits for
   `real_amplitudes`, and `optimized_real_amplitudes` needs more (≈40 at 56
   qubits, ≈120 at 84).
2. Keep `apply_postprocess=True` — the SQD-based noise-aware post-processing is
   doing real work on noisy output.
3. Try `dd_enable=True` for dynamical decoupling on longer circuits.

**Optimisation died part-way**
Resubmit with identical arguments plus `--resume <session_id>`. Loading
previous sessions can take up to an hour of *classical* time but consumes no
QPU time.

**Job rejected for exceeding limits**
`max_batchsize × estimator_shots ≤ 10,000,000` and `sampler_shots ≤ 10,000,000`.
The pre-flight check catches both before submission.

---

## Choosing an ansatz

| Ansatz | When to use |
|---|---|
| `real_amplitudes` | baseline; cheapest parameter count, scales furthest |
| `optimized_real_amplitudes` | default; best quality up to ~100 qubits, more parameters so more circuits |
| `cyclic` | ring entanglement variant, worth an ablation |
| `tailored` | **only** `ibm_torino`, 7 assets, 4 steps, 4 bits (112 qubits) — the paper's best XXL result |

A good ablation for a report: run the same instance under all three general
ansatzes and plot the cost distributions together.

---

## Extending the project

Strong additions, roughly in order of value per effort:

1. **Backtest the trajectory.** Apply the optimised weights to the *following*
   period's realised prices and compare against equal-weight and
   buy-and-hold. This turns an optimisation result into a finance result.
2. **Ansatz ablation** across `real_amplitudes` / `cyclic` /
   `optimized_real_amplitudes` at fixed circuit budget.
3. **γ sweep → efficient frontier.** Vary `risk_aversion` over a decade and
   plot the resulting risk/return locus.
4. **Post-processing ablation:** `apply_postprocess` True vs False on the same
   session, to quantify what SQD recovers from noise.
5. **Sector-constrained portfolios:** add a second penalty term capping
   exposure per sector; it slots into `build_dpo_problem` the same way the
   budget penalty does.

---

## Reporting checklist

- [ ] State the qubit count formula and your instance size (`na × nt × nq`)
- [ ] Show the QUBO validation test passing — it is your correctness argument
- [ ] Report the approximation ratio against an exact/classical reference
- [ ] Include the random baseline; without it, cost numbers mean nothing
- [ ] Report `rest_breaches` alongside return and Sharpe — a great return with
      a 40% budget breach is not a valid strategy
- [ ] Name the backend, ansatz, generations, population and shot counts
- [ ] State the data source and date range
- [ ] Be explicit that this is a scaling/feasibility study, not a claim of
      quantum advantage

---

## Common pitfalls

**Ragged date indices.** The function requires every asset to expose the same
dates. Assets from different exchanges have different holidays. `load_prices`
reindexes onto a full daily calendar and forward/backward-fills; if you supply
your own data, do the same or the function will reject it.

**Too little history.** You need at least `(nt + 1) × dt` rows. With `nt=4,
dt=30` that is 150 days. `QUBOSettings.validate` raises a clear error rather
than letting you discover this after submission.

**Infeasible budgets.** If `n_assets × (2^nq − 1)/K < 1`, no allocation can
satisfy the budget constraint. This is also caught up front.

**Sharpe ratios that look absurd.** These are per-period, un-annualised, and
computed on a QUBO-scaled objective — the paper's own example reports a Sharpe
of 24.82. Don't compare them to industry Sharpe ratios without annualising.

---

## Crypto / PQC track

### Tuning θ (the price of quantum risk)

θ is charged in per-period log-return units against an asset of vulnerability
v = 1.0. There is no "correct" value — it encodes how urgently you price the
quantum threat, so report it as an assumption and sweep it.

| θ | Reading |
|---|---|
| 0 | quantum-blind; ordinary mean-variance DPO |
| 0.05–0.1 | mild tilt; only breaks ties between similar assets |
| 0.2–0.5 | material; the optimizer starts rotating into PQC chains |
| > 0.8 | dominant; quantum safety overrides return almost entirely |

If a sweep shows a flat line, the local subset probably lacks vulnerability
spread — use `--local-select spread` (the default) rather than `first`, so the
subset spans safe *and* exposed assets. A tilt with nothing to rotate into is
a no-op, which is correct behaviour but an uninformative experiment.

### Why γ is recalibrated for crypto

`calibrate_risk_aversion` sets γ so risk and return contribute comparably,
which is the same reasoning the paper used to land on γ = 1000 for equities.
Crypto volatility is roughly an order of magnitude higher, so reusing 1000
makes the risk term dominate and the optimizer degenerates toward the
minimum-variance corner. Observed calibrated values: ~143 (majors), ~167
(mixed), ~244 (pqc). Override with `--risk-aversion` if you want the paper's
exact setting for a comparison.

### Why ρ is 2.0 rather than 1.0

Swept explicitly: at ρ=1 the crypto instances settled on an 80% budget in the
second period (a 20% breach); ρ≥2 enforces Σ_a ω̃ = 1 exactly at every step
with no further benefit above 2. Always report `rest_breach_pct` — a strategy
with a great Sharpe ratio and a 20% budget breach is not a valid strategy.

### Extending the PQC track

1. **Migration-event scenario analysis.** Model a discrete "quantum break"
   date and compare strategies chosen under different θ against a scenario
   where vulnerable chains repriced sharply.
2. **Time-varying θ.** The current tilt is constant; a θ that ramps with the
   time index expresses a threat that grows over the investment horizon and
   still passes through the price-drag trick.
3. **Refine the taxonomy.** The scores in `PQC_PROFILES` are judgement calls
   on published posture. Anchor them to a rubric — signature scheme, share of
   supply on PQ-secured addresses, migration governance — and cite sources.
4. **Liquidity constraints.** Several PQC chains are thin; a turnover cap or
   per-asset ceiling tied to volume would make the result tradeable.

### Honest framing for a write-up

Say plainly that θ is an assumption, not a market-implied quantity, and that
the sample window drove the sign of the returns. The defensible claim is
methodological: *dynamic portfolio optimization can absorb a forward-looking
cryptographic risk factor that price history alone cannot express, and the
resulting trade-off can be quantified.* That is interesting on its own and does
not require pretending to forecast when quantum hardware breaks ECDSA.
