"""Figures for the DPO study."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .qubo import DPOProblem  # noqa: E402
from .solvers import SolveResult  # noqa: E402

plt.rcParams.update(
    {
        "figure.dpi": 130,
        "savefig.dpi": 160,
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

PALETTE = {
    "quantum": "#c8102e",
    "classical": "#1f4e79",
    "random": "#8c8c8c",
    "exact": "#111111",
}


def _save(fig, out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"[plot] {path}")
    return path


def plot_prices(prices: pd.DataFrame, problem: DPOProblem, out: Path, name="01_prices.png"):
    fig, ax = plt.subplots(figsize=(8, 3.6))
    norm = prices / prices.iloc[0]
    for col in norm.columns:
        ax.plot(range(len(norm)), norm[col], lw=1.3, label=col)
    for t, d in enumerate(problem.rebalance_dates):
        ax.axvline(t * problem.settings.dt, color="k", ls=":", lw=0.9, alpha=0.5)
        ax.text(
            t * problem.settings.dt, ax.get_ylim()[1], f" t{t}", va="top", fontsize=7.5
        )
    step = max(len(norm) // 8, 1)
    ax.set_xticks(range(0, len(norm), step))
    ax.set_xticklabels([norm.index[i] for i in range(0, len(norm), step)], rotation=45,
                       ha="right", fontsize=7)
    ax.set_ylabel("price (normalised to t=0)")
    ax.set_title("Asset price evolution and rebalancing dates")
    ax.legend(ncol=4, fontsize=7.5, frameon=False)
    return _save(fig, out, name)


def plot_cost_distributions(
    results: dict[str, SolveResult],
    out: Path,
    exact_cost: float | None = None,
    name="02_cost_distributions.png",
):
    """Reproduces Fig. 1 of the paper: normalised cost distributions."""
    fig, ax = plt.subplots(figsize=(7.5, 4.0))

    for label, res in results.items():
        if not res.costs:
            continue
        c = np.round(np.asarray(res.costs), 2)
        vals, counts = np.unique(c, return_counts=True)
        y = counts / counts.max()
        color = (
            PALETTE["quantum"]
            if "VQE" in label
            else PALETTE["random"]
            if "Random" in label
            else PALETTE["classical"]
        )
        ax.plot(vals, y, lw=1.6, label=label, color=color)
        ax.fill_between(vals, y, alpha=0.12, color=color)

    if "Random" in results and results["Random"].costs:
        ax.axvline(
            float(np.mean(results["Random"].costs)),
            color=PALETTE["random"],
            ls="--",
            lw=1.2,
            label="random mean (offset)",
        )
    if exact_cost is not None:
        ax.axvline(
            exact_cost, color=PALETTE["exact"], ls="--", lw=1.4, label="classical optimum"
        )

    ax.set_xlabel("objective cost   (← better)")
    ax.set_ylabel("normalised occurrence")
    ax.set_title("Objective-cost distributions: quantum vs classical vs random")
    ax.legend(fontsize=7.5, frameon=False)
    return _save(fig, out, name)


def plot_convergence(results: dict[str, SolveResult], out: Path, name="03_convergence.png"):
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for label, res in results.items():
        if res.history:
            ax.plot(res.history, lw=1.6, label=label,
                    color=PALETTE["quantum"] if "VQE" in label else PALETTE["classical"])
    ax.set_xlabel("generation / restart")
    ax.set_ylabel("best objective cost")
    ax.set_title("Optimizer convergence")
    ax.legend(fontsize=8, frameon=False)
    return _save(fig, out, name)


def plot_strategy(problem: DPOProblem, x, out: Path, title: str, name="04_strategy.png"):
    w = problem.weights(x)
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(10, 3.8), gridspec_kw={"width_ratios": [1.5, 1]}
    )

    bottom = np.zeros(problem.nt)
    cmap = plt.get_cmap("tab10")
    for i, a in enumerate(problem.assets):
        ax1.bar(range(problem.nt), w[:, i], bottom=bottom, label=a, color=cmap(i % 10))
        bottom += w[:, i]
    ax1.axhline(1.0, color="k", ls="--", lw=1.1, label="budget = 100%")
    ax1.set_xticks(range(problem.nt))
    ax1.set_xticklabels([f"t{t}\n{d}" for t, d in enumerate(problem.rebalance_dates)],
                        fontsize=7.5)
    ax1.set_ylabel("portfolio weight")
    ax1.set_title(title)
    ax1.legend(ncol=2, fontsize=7, frameon=False, loc="upper left",
               bbox_to_anchor=(1.02, 1.0))

    im = ax2.imshow(w.T, cmap="RdYlGn", aspect="auto", vmin=0)
    ax2.set_yticks(range(problem.na))
    ax2.set_yticklabels(problem.assets, fontsize=7.5)
    ax2.set_xticks(range(problem.nt))
    ax2.set_xticklabels([f"t{t}" for t in range(problem.nt)], fontsize=8)
    ax2.set_title("weight heat map")
    ax2.grid(False)
    for t in range(problem.nt):
        for a in range(problem.na):
            if w[t, a] > 0:
                ax2.text(t, a, f"{w[t,a]:.0%}", ha="center", va="center", fontsize=6.5)
    fig.colorbar(im, ax=ax2, fraction=0.046)
    return _save(fig, out, name)


def plot_risk_return(problem: DPOProblem, results: dict[str, SolveResult], out: Path,
                     n_cloud: int = 3000, seed: int = 0, name="05_risk_return.png"):
    """Risk/return scatter of random portfolios with the solvers overlaid."""
    rng = np.random.default_rng(seed)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))

    cloud_r, cloud_f = [], []
    for _ in range(n_cloud):
        x = rng.integers(0, 2, size=problem.num_qubits)
        m = problem.metrics(x)
        cloud_r.append(m["risk"])
        cloud_f.append(m["return"])
    ax.scatter(np.sqrt(cloud_r), cloud_f, s=4, alpha=0.18, color=PALETTE["random"],
               label="random portfolios")

    markers = {"quantum": ("*", 260), "classical": ("D", 70), "exact": ("s", 70)}
    for label, res in results.items():
        if "Random" in label:
            continue
        m = problem.metrics(res.best_x)
        key = "quantum" if "VQE" in label else "exact" if "Exact" in label else "classical"
        mk, sz = markers[key]
        ax.scatter(np.sqrt(m["risk"]), m["return"], marker=mk, s=sz,
                   color=PALETTE[key], edgecolor="white", linewidth=0.8, zorder=5,
                   label=f"{label} (Sharpe {m['sharpe_ratio']:.2f})")

    ax.set_xlabel(r"risk  $\sqrt{R(\Omega)}$")
    ax.set_ylabel(r"return  $F(\Omega)$")
    ax.set_title("Risk / return landscape")
    ax.legend(fontsize=7.5, frameon=False)
    return _save(fig, out, name)


def plot_qubo_matrix(problem: DPOProblem, out: Path, name="06_qubo_matrix.png"):
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    Q = problem.Q + problem.Q.T - np.diag(np.diag(problem.Q))
    v = np.abs(Q).max()
    im = ax.imshow(Q, cmap="coolwarm", vmin=-v, vmax=v)
    n_per_t = problem.na * problem.nq
    for t in range(1, problem.nt):
        ax.axhline(t * n_per_t - 0.5, color="k", lw=0.8)
        ax.axvline(t * n_per_t - 0.5, color="k", lw=0.8)
    ax.set_title(f"QUBO matrix ({problem.num_qubits} binary variables)\n"
                 "block structure = time steps")
    ax.grid(False)
    fig.colorbar(im, ax=ax, fraction=0.046)
    return _save(fig, out, name)


def plot_scaling(df: pd.DataFrame, out: Path, name="07_scaling.png"):
    """Qubit-count scaling table -> figure."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 3.6))
    ax1.plot(df["n_qubits"], df["approx_ratio"], "o-", color=PALETTE["quantum"], lw=1.6)
    ax1.axhline(1.0, color="k", ls="--", lw=1.0)
    ax1.set_xlabel("number of qubits (na · nt · nq)")
    ax1.set_ylabel("approximation ratio vs classical")
    ax1.set_title("Solution quality vs problem size")
    ax1.set_ylim(0, 1.08)

    ax2.plot(df["n_qubits"], df["vqe_seconds"], "o-", color=PALETTE["classical"], lw=1.6)
    ax2.set_xlabel("number of qubits")
    ax2.set_ylabel("local VQE wall time (s)")
    ax2.set_yscale("log")
    ax2.set_title("Simulation cost (why hardware is needed)")
    return _save(fig, out, name)


# --------------------------------------------------------------------------
# PQC / crypto-specific figures
# --------------------------------------------------------------------------
def plot_pqc_exposure(problem, x, vulnerability, assets, out: Path,
                      name="08_pqc_exposure.png"):
    """Allocation vs quantum vulnerability, and exposure over time."""
    w = problem.weights(x)
    avg_w = w.mean(axis=0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))

    order = np.argsort(vulnerability)
    colors = plt.get_cmap("RdYlGn_r")(np.asarray(vulnerability)[order])
    ax1.barh([assets[i] for i in order], avg_w[order], color=colors,
             edgecolor="k", linewidth=0.4)
    for rank, i in enumerate(order):
        ax1.text(avg_w[i] + 0.005, rank, f"v={vulnerability[i]:.2f}",
                 va="center", fontsize=7)
    ax1.set_xlabel("average allocation")
    ax1.set_title("Capital vs quantum vulnerability\n(green = post-quantum secure)")
    ax1.set_xlim(0, max(avg_w.max() * 1.35, 0.1))

    per_t = [float(w[t] @ vulnerability / w[t].sum()) if w[t].sum() > 0 else 0.0
             for t in range(problem.nt)]
    ax2.plot(range(problem.nt), per_t, "o-", color=PALETTE["quantum"], lw=1.8)
    ax2.axhline(float(np.mean(vulnerability)), color="k", ls="--", lw=1.0,
                label="equal-weight exposure")
    ax2.fill_between(range(problem.nt), per_t, alpha=0.15, color=PALETTE["quantum"])
    ax2.set_xticks(range(problem.nt))
    ax2.set_xticklabels([f"t{t}" for t in range(problem.nt)])
    ax2.set_ylabel("portfolio quantum vulnerability")
    ax2.set_ylim(0, 1)
    ax2.set_title("Quantum exposure per rebalancing date")
    ax2.legend(fontsize=7.5, frameon=False)
    return _save(fig, out, name)


def plot_quantum_frontier(df: pd.DataFrame, out: Path, name="09_quantum_frontier.png"):
    """The trade-off curve: what quantum safety costs in return."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))

    ax1.plot(df["theta"], df["quantum_exposure"], "o-",
             color=PALETTE["quantum"], lw=1.8)
    ax1.set_xlabel(r"$\theta$  (price of quantum risk)")
    ax1.set_ylabel("portfolio quantum exposure")
    ax1.set_title("Higher $\\theta$ buys quantum safety")
    ax1.set_ylim(0, 1)

    sc = ax2.scatter(df["quantum_exposure"], df["return"], c=df["theta"],
                     cmap="viridis", s=90, edgecolor="k", linewidth=0.5, zorder=5)
    ax2.plot(df["quantum_exposure"], df["return"], "-", color="grey", lw=1.0, alpha=0.6)
    for _, r in df.iterrows():
        ax2.annotate(f"θ={r['theta']:g}", (r["quantum_exposure"], r["return"]),
                     textcoords="offset points", xytext=(6, 5), fontsize=7)
    ax2.set_xlabel("portfolio quantum exposure")
    ax2.set_ylabel("realised return (untilted prices)")
    ax2.set_title("Quantum-safety frontier")
    fig.colorbar(sc, ax=ax2, label=r"$\theta$", fraction=0.046)
    return _save(fig, out, name)
