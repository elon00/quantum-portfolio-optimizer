"""
Dynamic Portfolio Optimization (DPO) -> QUBO construction.

Faithful implementation of the formulation used by Global Data Quantum's
"Quantum Portfolio Optimizer" Qiskit Function, as published in

    Á. Nodar, I. De León et al.,
    "Scaling the Variational Quantum Eigensolver for Dynamic Portfolio
     Optimization", arXiv:2412.19150v2 (2025).

Key equations
-------------
Return          F(Ω) = Σ_t  μ_t^T ω_t                                   (Eq. 3)
                μ_{t,a} = log( P_{t+1,a} / P_{t,a} )                    (Eq. 4)
Risk            R(Ω) = Σ_t  ω_t^T Σ_t ω_t                               (Eq. 5)
Transaction     C(Ω) = ν Σ_t |ω_t - ω_{t-1}|                            (Eq. 6)
                     ≈ ν λ Σ_t (ω̃_t - ω̃_{t-1})²,  λ = 2^(1/3)·K/K'     (Eq. 14)
Constraint      Σ_a ω̃_{t,a} = 1  ∀t   -> penalty ρ(Σ_a ω̃_{t,a} - 1)²   (Eq. 12)

Full objective (minimisation form, Eq. 15):

    Q = Σ_t [ -μ_t^T ω̃_t + (γ/2) ω̃_t^T Σ_t ω̃_t
              + ν λ (ω̃_t - ω̃_{t-1})²  + ρ (Σ_a ω̃_{t,a} - 1)² ]

Binary encoding (Eq. 16):

    ω̃_{t,a} = (1/K) Σ_r 2^r x_{t,a,r},     x ∈ {0,1}

with K = `max_investment` and K' = 2^nq - 1 (max spend on a single asset).
The number of binary variables (= qubits) is  na * nt * nq.

Covariance (Appendix D): Σ_t is computed from the *daily* log-returns
inside the window between investment t and t+1, with a 1/(Δt - 1) prefactor.

Variable ordering
-----------------
Index of x_{t,a,r} is  t*(na*nq) + a*nq + r.
This groups all bits of one asset contiguously, and all assets of one time
step contiguously - matching the `asset_order` semantics of the function
output (`metadata.asset_order` gives the per-time-step asset ordering).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Sequence

import numpy as np
import pandas as pd

__all__ = ["QUBOSettings", "DPOProblem", "build_dpo_problem"]


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@dataclass
class QUBOSettings:
    """Mirrors the `qubo_settings` dict accepted by the Qiskit Function.

    Attributes
    ----------
    nt :
        Number of rebalancing time steps.
    nq :
        Number of resolution qubits per (asset, time) investment variable.
    max_investment :
        K, the maximum number of invested currency units across all assets.
        The maximum fraction investable in a single asset is
        ``(2**nq - 1) / max_investment``.
    dt :
        Time window (in rows of the price table, i.e. days) per time step.
    risk_aversion :
        γ, risk-aversion coefficient.
    transaction_fee :
        ν, per-unit transaction fee.
    restriction_coeff :
        ρ, Lagrange multiplier enforcing the budget constraint.
    """

    nt: int
    nq: int
    max_investment: float
    dt: int = 30
    risk_aversion: float = 1000.0
    transaction_fee: float = 0.01
    restriction_coeff: float = 1.0

    def to_function_dict(self) -> dict:
        """Exact payload expected by `dpo_solver.run(qubo_settings=...)`."""
        return {
            "nt": int(self.nt),
            "nq": int(self.nq),
            "dt": int(self.dt),
            "max_investment": float(self.max_investment),
            "risk_aversion": float(self.risk_aversion),
            "transaction_fee": float(self.transaction_fee),
            "restriction_coeff": float(self.restriction_coeff),
        }

    # --- derived quantities -------------------------------------------------
    @property
    def k_prime(self) -> float:
        """K' = 2^nq - 1, max currency units investable in a single asset."""
        return float(2 ** self.nq - 1)

    @property
    def lambda_tc(self) -> float:
        """λ = 2^(1/3) · K / K'  (Eq. 14, minimises the |x| ≈ λx² error)."""
        return float(np.cbrt(2.0) * self.max_investment / self.k_prime)

    @property
    def max_weight_per_asset(self) -> float:
        """Largest normalised weight a single asset can take."""
        return self.k_prime / self.max_investment

    def validate(self, n_assets: int, n_rows: int) -> None:
        if self.nt < 1:
            raise ValueError("nt must be >= 1")
        if self.nq < 1:
            raise ValueError("nq must be >= 1")
        if self.max_investment <= 0:
            raise ValueError("max_investment must be > 0")
        needed = (self.nt + 1) * self.dt
        if n_rows < needed:
            raise ValueError(
                f"Price history too short: need at least (nt+1)*dt = {needed} "
                f"rows, got {n_rows}. Extend the date range or lower nt/dt."
            )
        if self.max_weight_per_asset * n_assets < 1.0:
            raise ValueError(
                "Infeasible budget: even investing the maximum in every asset "
                f"({n_assets} x {self.max_weight_per_asset:.3f} = "
                f"{n_assets * self.max_weight_per_asset:.3f}) cannot reach the "
                "required total weight of 1. Increase nq or lower max_investment."
            )


# --------------------------------------------------------------------------
# Problem container
# --------------------------------------------------------------------------
@dataclass
class DPOProblem:
    """A fully-specified DPO instance plus its QUBO matrix."""

    assets: list[str]
    settings: QUBOSettings
    mu: np.ndarray  # (nt, na)      period log-returns, Eq. 4
    sigma: np.ndarray  # (nt, na, na)  covariances,        Eq. 5 / App. D
    Q: np.ndarray  # (n, n)        upper-triangular QUBO matrix
    offset: float  # constant term (from the ρ penalty)
    prices: pd.DataFrame = field(repr=False, default=None)
    rebalance_dates: list[str] = field(default_factory=list)

    # --- sizes --------------------------------------------------------------
    @property
    def na(self) -> int:
        return len(self.assets)

    @property
    def nt(self) -> int:
        return self.settings.nt

    @property
    def nq(self) -> int:
        return self.settings.nq

    @property
    def num_qubits(self) -> int:
        """na * nt * nq - the qubit count the Qiskit Function will use."""
        return self.na * self.nt * self.nq

    def index(self, t: int, a: int, r: int) -> int:
        """Flat index of binary variable x_{t,a,r}."""
        return t * (self.na * self.nq) + a * self.nq + r

    # --- evaluation ---------------------------------------------------------
    def energy(self, x: np.ndarray) -> float:
        """QUBO energy x^T Q x + offset for a binary vector x."""
        x = np.asarray(x, dtype=float).ravel()
        return float(x @ self.Q @ x + self.offset)

    def weights(self, x: np.ndarray) -> np.ndarray:
        """Decode a bitstring into the (nt, na) normalised weight matrix ω̃."""
        x = np.asarray(x, dtype=float).ravel()
        pow2 = 2.0 ** np.arange(self.nq)
        w = np.zeros((self.nt, self.na))
        for t in range(self.nt):
            for a in range(self.na):
                lo = self.index(t, a, 0)
                w[t, a] = np.dot(pow2, x[lo : lo + self.nq])
        return w / self.settings.max_investment

    # --- financial metrics --------------------------------------------------
    def metrics(self, x: np.ndarray) -> dict:
        """Financial report for a candidate solution.

        Returns the same quantities the Qiskit Function reports in
        `metadata.all_samples_metrics`: objective cost, return, Sharpe ratio,
        transaction cost and maximum constraint deviation (`rest_breaches`).
        """
        s = self.settings
        w = self.weights(x)

        # Return F and risk R
        F = float(np.sum(self.mu * w))
        R = float(sum(w[t] @ self.sigma[t] @ w[t] for t in range(self.nt)))

        # True (L1) transaction cost, Eq. 6 - not the quadratic surrogate
        prev = np.zeros(self.na)
        C_true = 0.0
        for t in range(self.nt):
            C_true += float(np.sum(np.abs(w[t] - prev)))
            prev = w[t]
        C_true *= s.transaction_fee

        # Budget deviation per time step; `rest_breaches` = worst-case, in %
        dev = np.abs(w.sum(axis=1) - 1.0)
        rest_breach = float(dev.max() * 100.0)

        sharpe = F / np.sqrt(R) if R > 0 else float("inf")

        return {
            "objective_cost": self.energy(x),
            "return": F,
            "risk": R,
            "sharpe_ratio": sharpe,
            "transaction_cost": C_true,
            "rest_breach_pct": rest_breach,
            "budget_per_step": w.sum(axis=1).tolist(),
            "max_single_weight": float(w.max()),
        }

    def strategy_dict(self, x: np.ndarray) -> dict:
        """Same shape as the function's `result` dict: time_step_t -> {asset: w}."""
        w = self.weights(x)
        return {
            f"time_step_{t}": {a: float(w[t, i]) for i, a in enumerate(self.assets)}
            for t in range(self.nt)
        }

    def summary(self) -> str:
        s = self.settings
        return (
            f"DPO instance: {self.na} assets x {self.nt} steps x {self.nq} bits "
            f"= {self.num_qubits} qubits\n"
            f"  assets           : {', '.join(self.assets)}\n"
            f"  rebalance dates  : {', '.join(self.rebalance_dates)}\n"
            f"  K (max_investment)={s.max_investment:g}, K'={s.k_prime:g}, "
            f"lambda={s.lambda_tc:.4f}\n"
            f"  max weight/asset : {s.max_weight_per_asset:.1%}\n"
            f"  gamma={s.risk_aversion:g}, nu={s.transaction_fee:g}, "
            f"rho={s.restriction_coeff:g}, dt={s.dt}"
        )


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
def _period_returns_and_cov(
    prices: pd.DataFrame, nt: int, dt: int
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Compute μ (Eq. 4) and Σ_t (Appendix D) for each rebalancing step."""
    P = prices.to_numpy(dtype=float)
    n_rows, na = P.shape

    mu = np.zeros((nt, na))
    sigma = np.zeros((nt, na, na))
    dates: list[str] = []

    # Daily log returns, used for the covariance windows.
    daily = np.log(P[1:] / P[:-1])  # (n_rows-1, na)

    for t in range(nt):
        i0 = t * dt
        i1 = (t + 1) * dt
        if i1 >= n_rows:
            raise ValueError(
                f"Not enough price rows for time step {t}: need index {i1}, "
                f"have {n_rows - 1}."
            )
        # Eq. 4: period log-return between consecutive rebalancing dates
        mu[t] = np.log(P[i1] / P[i0])
        dates.append(str(prices.index[i0]))

        # Appendix D: covariance of the daily log-returns inside the window,
        # normalised by 1/(dt - 1).
        window = daily[i0:i1]  # dt rows
        centred = window - window.mean(axis=0, keepdims=True)
        sigma[t] = (centred.T @ centred) / max(dt - 1, 1)

    return mu, sigma, dates


def build_dpo_problem(
    prices: pd.DataFrame,
    settings: QUBOSettings,
    assets: Sequence[str] | None = None,
) -> DPOProblem:
    """Build the QUBO matrix for a dynamic portfolio optimization instance.

    Parameters
    ----------
    prices :
        DataFrame of closing prices, index = date strings (already gap-filled
        so every asset shares the same date index), columns = tickers.
    settings :
        QUBO settings (mirrors the Qiskit Function's `qubo_settings`).
    assets :
        Optional subset / explicit ordering of columns to use.

    Returns
    -------
    DPOProblem
        Container with μ, Σ, the upper-triangular QUBO matrix and helpers to
        decode bitstrings into investment strategies.
    """
    if assets is not None:
        prices = prices[list(assets)]
    prices = prices.astype(float)

    if prices.isna().to_numpy().any():
        raise ValueError(
            "Price table contains NaNs. Forward/backward-fill non-trading days "
            "before building the problem (see qpo.data.load_prices)."
        )

    asset_names = list(prices.columns)
    na = len(asset_names)
    settings.validate(n_assets=na, n_rows=len(prices))

    nt, nq = settings.nt, settings.nq
    K = float(settings.max_investment)
    gamma = float(settings.risk_aversion)
    nu = float(settings.transaction_fee)
    rho = float(settings.restriction_coeff)
    lam = settings.lambda_tc

    mu, sigma, dates = _period_returns_and_cov(prices, nt, settings.dt)

    n = na * nt * nq
    Q = np.zeros((n, n))
    offset = 0.0
    pow2 = 2.0 ** np.arange(nq)

    def idx(t: int, a: int, r: int) -> int:
        return t * (na * nq) + a * nq + r

    # ---- 1. Return term:  -Σ_t μ_t^T ω̃_t  (linear -> diagonal) -------------
    for t in range(nt):
        for a in range(na):
            for r in range(nq):
                Q[idx(t, a, r), idx(t, a, r)] += -mu[t, a] * pow2[r] / K

    # ---- 2. Risk term:  (γ/2) Σ_t ω̃_t^T Σ_t ω̃_t  (quadratic) --------------
    coef = gamma / 2.0
    for t in range(nt):
        for a in range(na):
            for b in range(na):
                c = coef * sigma[t, a, b] / (K * K)
                if c == 0.0:
                    continue
                for r in range(nq):
                    for s_ in range(nq):
                        i, j = idx(t, a, r), idx(t, b, s_)
                        v = c * pow2[r] * pow2[s_]
                        if i == j:
                            Q[i, i] += v
                        elif i < j:
                            Q[i, j] += v
                        else:
                            Q[j, i] += v

    # ---- 3. Transaction cost:  ν λ Σ_t (ω̃_t - ω̃_{t-1})²  ------------------
    # ω̃_{-1} = 0 (no initial holdings). Expanding the square gives
    #   ω̃_t² - 2 ω̃_t ω̃_{t-1} + ω̃_{t-1}².
    tc = nu * lam
    for t in range(nt):
        for a in range(na):
            for r in range(nq):
                for s_ in range(nq):
                    v = tc * pow2[r] * pow2[s_] / (K * K)
                    # + ω̃_t²
                    i, j = idx(t, a, r), idx(t, a, s_)
                    if i == j:
                        Q[i, i] += v
                    elif i < j:
                        Q[i, j] += v
                    else:
                        Q[j, i] += v
                    if t > 0:
                        # + ω̃_{t-1}²
                        i, j = idx(t - 1, a, r), idx(t - 1, a, s_)
                        if i == j:
                            Q[i, i] += v
                        elif i < j:
                            Q[i, j] += v
                        else:
                            Q[j, i] += v
                        # - 2 ω̃_t ω̃_{t-1}
                        i, j = idx(t, a, r), idx(t - 1, a, s_)
                        lo, hi = (i, j) if i < j else (j, i)
                        Q[lo, hi] += -2.0 * v

    # ---- 4. Budget penalty:  ρ Σ_t (Σ_a ω̃_{t,a} - 1)²  --------------------
    # Expand: ρ[ (Σ ω̃)² - 2 Σ ω̃ + 1 ]
    for t in range(nt):
        offset += rho
        for a in range(na):
            for r in range(nq):
                i = idx(t, a, r)
                # linear part: -2 ρ ω̃
                Q[i, i] += -2.0 * rho * pow2[r] / K
                # quadratic part: ρ (Σ ω̃)²
                # Every ordered pair (i, j) is visited, so folding both
                # orderings into the upper triangle reproduces the 2·ρ·ω̃_i·ω̃_j
                # cross terms of the expanded square.
                for b in range(na):
                    for s_ in range(nq):
                        j = idx(t, b, s_)
                        v = rho * pow2[r] * pow2[s_] / (K * K)
                        if i == j:
                            Q[i, i] += v
                        elif i < j:
                            Q[i, j] += v
                        else:
                            Q[j, i] += v

    return DPOProblem(
        assets=asset_names,
        settings=settings,
        mu=mu,
        sigma=sigma,
        Q=Q,
        offset=offset,
        prices=prices,
        rebalance_dates=dates,
    )
