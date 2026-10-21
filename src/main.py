"""Entry point: fetch NSE IPO demand, log it to Google Sheets, notify Google Chat."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import chat
import sheets
from nse import DemandSnapshot, fetch_snapshot

IST = ZoneInfo("Asia/Kolkata")


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default or "")
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _estimated_applications(shares_bid: float | None, lot_size: float) -> float | None:
    if not shares_bid or lot_size <= 0:
        return None
    return shares_bid / lot_size


def _lot_size_for_group(group: str | None, lot_sizes: dict[str, float]) -> float:
    return lot_sizes.get(group or "", 0)


def build_rows(
    snapshot: DemandSnapshot, fetched_at: str, lot_sizes: dict[str, float]
) -> list[list[object]]:
    rows: list[list[object]] = []
    for row in snapshot.categories:
        lot_size = _lot_size_for_group(row.group, lot_sizes)
        applications = _estimated_applications(row.shares_bid, lot_size)
        rows.append(
            [
                fetched_at,
                snapshot.symbol,
                snapshot.company,
                snapshot.update_time,
                row.sr_no,
                row.category,
                row.shares_offered if row.shares_offered is not None else "",
                row.shares_bid if row.shares_bid is not None else "",
                row.subscription_times if row.subscription_times is not None else "",
                round(applications) if applications is not None else "",
                snapshot.cutoff_shares_bid if snapshot.cutoff_shares_bid is not None else "",
            ]
        )
    return rows


def build_bucket_rows(
    snapshot: DemandSnapshot, fetched_at: str, lot_sizes: dict[str, float]
) -> list[list[object]]:
    """One row per (group, bid-price bucket): Retail/Shareholder/Employee split by
    Cut-off vs Price bid, and bHNI/sHNI as a single Total bucket (no cut-off allowed).
    """
    rows: list[list[object]] = []
    for group, buckets in snapshot.bucket_rows().items():
        lot_size = _lot_size_for_group(group, lot_sizes)
        for bucket_label, row in buckets.items():
            applications = _estimated_applications(row.shares_bid, lot_size)
            rows.append(
                [
                    fetched_at,
                    snapshot.symbol,
                    snapshot.company,
                    snapshot.update_time,
                    group,
                    bucket_label,
                    row.shares_offered if row.shares_offered is not None else "",
                    row.shares_bid if row.shares_bid is not None else "",
                    row.subscription_times if row.subscription_times is not None else "",
                    round(applications) if applications is not None else "",
                ]
            )
    return rows


def build_price_ladder_rows(snapshot: DemandSnapshot, fetched_at: str) -> list[list[object]]:
    """One row per discrete bid price on the aggregate demand curve (all categories
    combined; NSE does not publish a per-category price ladder).
    """
    rows: list[list[object]] = []
    for level in snapshot.price_ladder:
        rows.append(
            [
                fetched_at,
                snapshot.symbol,
                snapshot.company,
                snapshot.update_time,
                level.price,
                level.cum_qty if level.cum_qty is not None else "",
                level.qty_at_price if level.qty_at_price is not None else "",
            ]
        )
    return rows


def main() -> int:
    symbol = _env("IPO_SYMBOL", "JPL")
    exchange = _env("IPO_EXCHANGE", "ALL")
    series = _env("IPO_SERIES", "EQ")
    retail_lot_size = float(_env("RETAIL_LOT_SIZE", "12") or 0)
    # Employee/Shareholder quotas conventionally share the retail lot size for this IPO.
    # bHNI/sHNI apply in much larger minimum lot multiples (to clear the >Rs10L / Rs2L-10L
    # thresholds): 900 and 180 shares respectively for this IPO's 12-share retail lot.
    lot_sizes = {
        "Retail": retail_lot_size,
        "Employee": float(_env("EMPLOYEE_LOT_SIZE", "") or retail_lot_size),
        "Shareholder": float(_env("SHAREHOLDER_LOT_SIZE", "") or retail_lot_size),
        "bHNI": float(_env("BHNI_LOT_SIZE", "") or 900),
        "sHNI": float(_env("SHNI_LOT_SIZE", "") or 180),
    }

    fetched_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    snapshot = fetch_snapshot(symbol, exchange, series)

    if not snapshot.categories:
        print(f"No demand data returned for {symbol}; nothing to publish.")
        return 0

    rows = build_rows(snapshot, fetched_at, lot_sizes)
    bucket_rows = build_bucket_rows(snapshot, fetched_at, lot_sizes)
    price_ladder_rows = build_price_ladder_rows(snapshot, fetched_at)

    spreadsheet_id = _env("GSHEET_ID")
    service_account = _env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if spreadsheet_id and service_account:
        sheets.append_rows(
            service_account,
            spreadsheet_id,
            _env("GSHEET_WORKSHEET", "IPO Demand"),
            rows,
        )
        print(f"Appended {len(rows)} row(s) to Google Sheet.")
        if bucket_rows:
            sheets.append_bucket_rows(
                service_account,
                spreadsheet_id,
                _env("GSHEET_BUCKET_WORKSHEET", "IPO Bid Buckets"),
                bucket_rows,
            )
            print(f"Appended {len(bucket_rows)} bucket row(s) to Google Sheet.")
        if price_ladder_rows:
            sheets.append_price_ladder_rows(
                service_account,
                spreadsheet_id,
                _env("GSHEET_PRICE_LADDER_WORKSHEET", "IPO Price Ladder"),
                price_ladder_rows,
            )
            print(f"Appended {len(price_ladder_rows)} price-ladder row(s) to Google Sheet.")
    else:
        print("Google Sheets not configured; skipping sheet update.")

    webhook = _env("GCHAT_WEBHOOK_URL")
    if webhook:
        chat.post_message(
            webhook,
            chat.build_message(snapshot, fetched_at, lot_sizes),
            thread_key=_env("GCHAT_THREAD_KEY") or None,
        )
        print("Posted update to Google Chat space.")
    else:
        print("GCHAT_WEBHOOK_URL not set; skipping Chat notification.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
