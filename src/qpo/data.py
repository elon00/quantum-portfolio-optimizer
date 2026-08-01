"""
Price-data acquisition and conditioning for the DPO pipeline.

The Qiskit Function requires a strictly rectangular price table: every asset
must expose exactly the same set of dates (Global Data Quantum's docs are
explicit about this - "all dictionaries must have the same secondary key").
Assets from different exchanges have different holiday calendars, so we
reindex onto a full daily calendar and forward/backward-fill.

Sources are tried in order and the first one that yields a clean table wins:

1. a local CSV cache (so results are reproducible offline),
2. Yahoo Finance via `yfinance`,
3. the public fallback CSV that IBM's own tutorial falls back to,
4. a deterministic synthetic generator (geometric Brownian motion with a
   realistic correlation structure) so the whole pipeline is still runnable
   with no network at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = ["load_prices", "to_function_payload", "IBEX_PORTFOLIO", "NIFTY_PORTFOLIO"]

# The seven IBEX 35 tickers used by the official IBM tutorial.
IBEX_PORTFOLIO = ["ACS.MC", "ITX.MC", "FER.MC", "ELE.MC", "SCYR.MC", "AENA.MC", "AMS.MC"]

# An Indian large-cap portfolio spanning distinct sectors (NSE tickers).
NIFTY_PORTFOLIO = [
    "RELIANCE.NS",  # energy / conglomerate
    "TCS.NS",       # IT services
    "HDFCBANK.NS",  # banking
    "INFY.NS",      # IT services
    "ITC.NS",       # FMCG
    "LT.NS",        # infrastructure
    "SUNPHARMA.NS", # pharma
]

# Fallback used by IBM's tutorial when Yahoo Finance is unavailable.
_IBEX_FALLBACK_URL = (
    "https://raw.githubusercontent.com/Global-Data-Quantum/PortfolioData/"
    "1f3ebb95fdef245014a04d4273f688a2951e0061/data/qpo_tutorial_ibex.csv"
)


def _condition(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Reindex onto a full daily calendar and fill non-trading days."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    full = pd.date_range(start=start, end=end, freq="D")
    df = df.reindex(full).ffill().bfill()
    df.index = df.index.strftime("%Y-%m-%d")
    df.index.name = "date"
    return df


def _from_yahoo(symbols: list[str], start: str, end: str) -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError:
        return None

    series = []
    for sym in symbols:
        try:
            data = yf.download(
                sym, start=start, end=end, progress=False, auto_adjust=True
            )
        except Exception:
            return None
        if data is None or data.empty or "Close" not in data:
            return None
        close = data["Close"]
        if isinstance(close, pd.DataFrame):  # yfinance>=0.2 returns a frame
            close = close.iloc[:, 0]
        close.name = sym
        series.append(close)

    if not series:
        return None
    df = pd.concat(series, axis=1)
    if df.empty:
        return None
    return df


def _from_fallback_csv(symbols: list[str]) -> pd.DataFrame | None:
    """IBM's public backup table (IBEX only)."""
    try:
        df = pd.read_csv(_IBEX_FALLBACK_URL, index_col=0)
    except Exception:
        return None
    missing = [s for s in symbols if s not in df.columns]
    if missing:
        return None
    return df[symbols]


def _synthetic(symbols: list[str], start: str, end: str, seed: int = 7) -> pd.DataFrame:
    """Correlated geometric Brownian motion - deterministic, offline-safe."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start=start, end=end, freq="D")
    n, na = len(dates), len(symbols)

    # A plausible correlation structure: one market factor + idiosyncratic noise.
    beta = rng.uniform(0.5, 1.2, size=na)
    drift = rng.uniform(-0.0004, 0.0009, size=na)
    vol = rng.uniform(0.008, 0.022, size=na)

    market = rng.normal(0.0, 0.009, size=n)
    shocks = rng.normal(0.0, 1.0, size=(n, na))
    rets = drift + beta * market[:, None] * 0.6 + vol * shocks * 0.8

    prices = 100.0 * np.exp(np.cumsum(rets, axis=0))
    prices *= rng.uniform(0.4, 3.0, size=na)  # varied price levels
    df = pd.DataFrame(prices, index=dates, columns=symbols)
    return df


def load_prices(
    symbols: list[str],
    start: str,
    end: str,
    cache_dir: str | os.PathLike | None = None,
    allow_synthetic: bool = True,
    verbose: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Load a clean, rectangular closing-price table.

    Returns
    -------
    (prices, source)
        `prices` is indexed by "YYYY-MM-DD" strings with one column per symbol;
        `source` records which provider supplied the data.
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        tag = "_".join(s.replace(".", "-") for s in symbols)
        cache_path = cache_dir / f"{tag}_{start}_{end}.csv"
        if cache_path.exists():
            df = pd.read_csv(cache_path, index_col=0)
            if verbose:
                print(f"[data] loaded {df.shape} from cache {cache_path.name}")
            return df, "cache"

    for name, fn in (
        ("yahoo", lambda: _from_yahoo(symbols, start, end)),
        ("gdq-fallback-csv", lambda: _from_fallback_csv(symbols)),
    ):
        df = fn()
        if df is not None and not df.isna().all().any():
            df = _condition(df, start, end)
            if not df.isna().to_numpy().any():
                if verbose:
                    print(f"[data] source={name} shape={df.shape}")
                if cache_path is not None:
                    df.to_csv(cache_path)
                return df, name
        if verbose:
            print(f"[data] source={name} unavailable, trying next")

    if not allow_synthetic:
        raise RuntimeError("No price source available and synthetic data disabled.")

    df = _condition(_synthetic(symbols, start, end), start, end)
    if verbose:
        print(f"[data] source=synthetic (offline fallback) shape={df.shape}")
    if cache_path is not None:
        df.to_csv(cache_path)
    return df, "synthetic"


def to_function_payload(prices: pd.DataFrame) -> dict:
    """Convert the price table into the exact `assets` dict the Function wants.

    Shape: {ticker: {"YYYY-MM-DD": close, ...}, ...}
    """
    payload = prices.to_dict()
    for ticker, series in payload.items():
        payload[ticker] = {str(d): float(v) for d, v in series.items()}
    return payload
