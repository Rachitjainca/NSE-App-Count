"""Fetch IPO bid/demand data from the NSE public APIs."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

BASE = "https://www.nseindia.com"
# Price-vs-cumulative-quantity ladder; also carries the authoritative timestamp.
DEMAND_URL = f"{BASE}/api/ipo-chart-demand"
# Consolidated, category-wise bid details (QIB / NII / Retail / Employee / Shareholder).
# Replaces the now-defunct `ipo-active-category`, which returns an empty "Total: 0" shell
# for newer IPOs (confirmed empty for JPL); `ipo-bid-details` is what the live issue-
# information page actually renders its category table from, and needs `series`.
BID_DETAILS_URL = f"{BASE}/api/ipo-bid-details"
# Exchanges to try, in order, when the requested one has no data yet (e.g. `ALL` can lag
# behind `NSE` until both exchanges' order books are merged).
EXCHANGE_FALLBACKS = ["ALL", "NSE", "BSE"]

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

# Reserved-category groups that can be bucketed by bid type (Retail/Employee/Shareholder
# may bid at cut-off; bHNI/sHNI bid at a specific price only), keyed by the bid-details
# API's top-level `srNo` (e.g. "3(a)" for a Retail sub-row still roots to "3").
GROUP_BY_SR_ROOT = {
    "3": "Retail",
    "4": "Employee",
    "5": "Shareholder",
    "2.1": "bHNI",
    "2.2": "sHNI",
}
_BUCKET_LABELS = {"cut off": "Cut-off", "price bids": "Price bid", "price bid": "Price bid"}


def _sr_root(sr_no: str) -> str:
    match = re.match(r"\d+(?:\.\d+)?", sr_no)
    return match.group(0) if match else ""


@dataclass
class CategoryRow:
    sr_no: str
    category: str
    shares_offered: float | None
    shares_bid: float | None
    subscription_times: float | None

    @property
    def group(self) -> str | None:
        """Retail / Employee / Shareholder / bHNI / sHNI, or None for QIB/other rows."""
        return GROUP_BY_SR_ROOT.get(_sr_root(self.sr_no))

    @property
    def is_retail(self) -> bool:
        return self.group == "Retail"

    @property
    def is_top_level(self) -> bool:
        """True for a group's own row (e.g. "3", "2.1"), false for its sub-rows/breakdowns."""
        return self.sr_no == _sr_root(self.sr_no)

    @property
    def bucket(self) -> str | None:
        """'Total', 'Cut-off', or 'Price bid' within its group.

        None for rows that aren't price buckets at all (e.g. the Corporates/
        Individuals/Others investor-type breakdown under QIB or bHNI/sHNI).
        """
        if self.group is None:
            return None
        label = _BUCKET_LABELS.get(self.category.strip().lower())
        if label:
            return label
        return "Total" if self.sr_no == _sr_root(self.sr_no) else None


@dataclass
class PriceLevel:
    """One price point on the aggregate demand curve (all categories combined)."""

    price: str  # a numeric price string, or "Cut-off"
    cum_qty: float | None  # cumulative demand at this price-or-higher (None for Cut-off)
    qty_at_price: float | None  # incremental demand bucketed at this exact price point


@dataclass
class DemandSnapshot:
    symbol: str
    company: str
    update_time: str
    total_shares_bid: float | None
    cutoff_shares_bid: float | None = None
    categories: list[CategoryRow] = field(default_factory=list)
    price_ladder: list[PriceLevel] = field(default_factory=list)

    def retail(self) -> CategoryRow | None:
        return next(
            (c for c in self.categories if c.group == "Retail" and c.bucket == "Total"), None
        )

    def bucket_rows(self) -> dict[str, dict[str, CategoryRow]]:
        """Bid-price bucket rows per reserved-category group, e.g. buckets['Retail']['Cut-off']."""
        buckets: dict[str, dict[str, CategoryRow]] = {}
        for row in self.categories:
            if row.group is None or row.bucket is None:
                continue
            buckets.setdefault(row.group, {})[row.bucket] = row
        return buckets

    def price_summary(self) -> list[PriceLevel]:
        """Collapse the full per-price ladder into 3 buckets: Cut-off, At-cap, Below-cap.

        NSE's raw curve has a long tail of discrete prices below the cap with
        near-zero increments (most bids cluster at cut-off/cap), so this summary is
        more useful for a quick read than the full ladder (which is still kept as-is
        in `price_ladder` / the IPO Price Ladder sheet for history).
        """
        cutoff = next((lvl for lvl in self.price_ladder if lvl.price == "Cut-off"), None)
        priced = [lvl for lvl in self.price_ladder if lvl.price != "Cut-off"]
        levels = [cutoff] if cutoff else []
        if not priced:
            return levels
        cap, floor = priced[0], priced[-1]
        below_cap_qty = (
            floor.cum_qty - cap.cum_qty
            if floor.cum_qty is not None and cap.cum_qty is not None
            else None
        )
        levels.append(PriceLevel(price=f"Cap ₹{cap.price}", cum_qty=cap.cum_qty, qty_at_price=cap.cum_qty))
        levels.append(PriceLevel(price="Below cap", cum_qty=floor.cum_qty, qty_at_price=below_cap_qty))
        return levels


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

    The curve is aggregate across all categories; NSE publishes no per-category price
    ladder. Retail, Employee, and Shareholder quotas may bid at cut-off (NII/HNI may not),
    so this is only a proxy for combined cut-off demand, not retail alone.
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


def _parse_price_ladder(payload: Any) -> list[PriceLevel]:
    """Bucket the demand curve by bid price.

    `cumQty` is cumulative demand at that price-or-higher as price descends from the
    cap; "Cut-off" is a separate bucket (bids that chose cut-off, not part of the
    cumulative price chain). Incremental demand at each price = its cumQty minus the
    previous, higher price's cumQty. Kept at full per-price detail for the sheet;
    `DemandSnapshot.price_summary()` collapses this to 3 buckets for the Chat message.
    """
    rows = payload.get("dataList", payload) if isinstance(payload, dict) else payload
    levels: list[PriceLevel] = []
    prev_cum = 0.0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        price = str(row.get("price") or "").strip()
        cum_qty = _to_float(row.get("cumQty"))
        if not price or price == "-" or cum_qty is None:
            continue
        if price.lower() == "cut-off":
            levels.append(PriceLevel(price="Cut-off", cum_qty=None, qty_at_price=cum_qty))
            continue
        levels.append(PriceLevel(price=price, cum_qty=cum_qty, qty_at_price=cum_qty - prev_cum))
        prev_cum = cum_qty

    return levels


def _parse_categories(payload: Any) -> list[CategoryRow]:
    """Parse the `ipo-bid-details` response: `{"data": [...], "updateTime": ...}`."""
    rows = payload.get("data", []) if isinstance(payload, dict) else (payload or [])
    categories: list[CategoryRow] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        sr_no = str(row.get("srNo") or "").strip()
        category = str(row.get("category") or "").strip()
        # The header row and the final "Total" summary row both lack a real srNo.
        if not category or category == "Category" or sr_no in {"", "Sr.No."}:
            continue
        categories.append(
            CategoryRow(
                sr_no=sr_no,
                category=category,
                shares_offered=_to_float(row.get("noOfSharesOffered")),
                shares_bid=_to_float(row.get("noOfsharesBid")),
                subscription_times=_to_float(row.get("noOfTime")),
            )
        )

    return categories


def fetch_snapshot(
    symbol: str, exchange: str = "ALL", series: str = "EQ", retries: int = 3
) -> DemandSnapshot:
    """Return the current demand curve + category-wise bid snapshot for an IPO symbol."""
    last_error: Exception | None = None

    for attempt in range(retries):
        session = build_session()
        try:
            company, timestamp, peak, cutoff, curve = "", "", None, None, None
            exchanges_to_try = [exchange] + [e for e in EXCHANGE_FALLBACKS if e != exchange]
            for ex in exchanges_to_try:
                curve = _get_json(session, DEMAND_URL, {"symbol": symbol, "exchange": ex})
                company, timestamp, peak, cutoff = _parse_curve(curve)
                if peak is not None:
                    break
            categories = _get_json(
                session, BID_DETAILS_URL, {"symbol": symbol, "series": series}
            )
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
            continue

        return DemandSnapshot(
            symbol=symbol,
            company=company,
            update_time=timestamp,
            total_shares_bid=peak,
            cutoff_shares_bid=cutoff,
            categories=_parse_categories(categories),
            price_ladder=_parse_price_ladder(curve),
        )

    raise RuntimeError(f"Failed to fetch NSE demand for {symbol}: {last_error}")
