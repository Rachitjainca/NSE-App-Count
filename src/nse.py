"""Fetch IPO bid/demand data from the NSE public APIs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

BASE = "https://www.nseindia.com"
# Price-vs-cumulative-quantity ladder; also carries the authoritative timestamp.
DEMAND_URL = f"{BASE}/api/ipo-chart-demand"
# Category-wise live bid status (QIB / NII / Retail / Employee).
CATEGORY_URL = f"{BASE}/api/ipo-active-category"

# NSE rejects requests that do not look like a real browser session.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/market-data/all-upcoming-issues-ipo",
    "Connection": "keep-alive",
}

# Warm-up pages that hand out the cookies the API endpoints require.
WARMUP_PATHS = ["/", "/market-data/all-upcoming-issues-ipo"]


@dataclass
class CategoryRow:
    sr_no: str
    category: str
    shares_offered: float | None
    shares_bid: float | None
    subscription_times: float | None

    @property
    def is_retail(self) -> bool:
        return "retail" in self.category.lower()


@dataclass
class DemandSnapshot:
    symbol: str
    company: str
    update_time: str
    total_shares_bid: float | None
    cutoff_shares_bid: float | None = None
    categories: list[CategoryRow] = field(default_factory=list)

    def retail(self) -> CategoryRow | None:
        return next((c for c in self.categories if c.is_retail), None)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    for path in WARMUP_PATHS:
        try:
            session.get(f"{BASE}{path}", timeout=20)
        except requests.RequestException:
            pass
    return session


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    if not cleaned or cleaned in {"-", "--", "NA", "N/A"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _get_json(session: requests.Session, url: str, params: dict[str, str]) -> Any:
    response = session.get(url, params=params, timeout=25)
    response.raise_for_status()
    return response.json()


def _parse_curve(payload: Any) -> tuple[str, str, float | None, float | None]:
    """Return (company, timestamp, peak cumulative qty, cut-off qty) from the demand curve.

    The curve is aggregate across all categories; NSE publishes no per-category ladder.
    Only retail and employees may bid at cut-off, so that row approximates retail demand.
    """
    rows = payload.get("dataList", payload) if isinstance(payload, dict) else payload
    company = ""
    timestamp = ""
    peak: float | None = None
    cutoff: float | None = None

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        company = company or str(row.get("company") or "").strip()
        timestamp = timestamp or str(row.get("timestamp") or "").strip()
        qty = _to_float(row.get("cumQty"))
        if qty is None:
            continue
        if str(row.get("price") or "").strip().lower() == "cut-off":
            cutoff = qty
        if peak is None or qty > peak:
            peak = qty

    return company, timestamp, peak, cutoff


def _parse_categories(payload: Any) -> list[CategoryRow]:
    rows = payload.get("dataList", []) if isinstance(payload, dict) else (payload or [])
    categories: list[CategoryRow] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        sr_no = str(row.get("srNo") or "").strip()
        category = str(row.get("category") or "").strip()
        # dataList[0] is a header row describing the columns, not data.
        if not category or category == "Category" or sr_no in {"", "Sr.No."}:
            continue
        categories.append(
            CategoryRow(
                sr_no=sr_no,
                category=category,
                shares_offered=_to_float(row.get("noOfShareOffered")),
                shares_bid=_to_float(row.get("noOfSharesBid")),
                subscription_times=_to_float(row.get("noOfTotalMeant")),
            )
        )

    return categories


def fetch_snapshot(symbol: str, exchange: str = "ALL", retries: int = 3) -> DemandSnapshot:
    """Return the current demand curve + category-wise bid snapshot for an IPO symbol."""
    last_error: Exception | None = None

    for attempt in range(retries):
        session = build_session()
        try:
            curve = _get_json(session, DEMAND_URL, {"symbol": symbol, "exchange": exchange})
            categories = _get_json(session, CATEGORY_URL, {"symbol": symbol})
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
            continue

        company, timestamp, peak, cutoff = _parse_curve(curve)
        return DemandSnapshot(
            symbol=symbol,
            company=company,
            update_time=timestamp,
            total_shares_bid=peak,
            cutoff_shares_bid=cutoff,
            categories=_parse_categories(categories),
        )

    raise RuntimeError(f"Failed to fetch NSE demand for {symbol}: {last_error}")
