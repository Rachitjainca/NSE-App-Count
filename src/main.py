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


def build_rows(
    snapshot: DemandSnapshot, fetched_at: str, lot_size: float
) -> list[list[object]]:
    rows: list[list[object]] = []
    for row in snapshot.categories:
        applications = (
            _estimated_applications(row.shares_bid, lot_size) if row.is_retail else None
        )
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


def main() -> int:
    symbol = _env("IPO_SYMBOL", "NSE")
    exchange = _env("IPO_EXCHANGE", "ALL")
    lot_size = float(_env("RETAIL_LOT_SIZE", "0") or 0)

    fetched_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    snapshot = fetch_snapshot(symbol, exchange)

    if not snapshot.categories:
        print(f"No demand data returned for {symbol}; nothing to publish.")
        return 0

    rows = build_rows(snapshot, fetched_at, lot_size)

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
    else:
        print("Google Sheets not configured; skipping sheet update.")

    webhook = _env("GCHAT_WEBHOOK_URL")
    if webhook:
        retail = snapshot.retail()
        applications = (
            _estimated_applications(retail.shares_bid, lot_size) if retail else None
        )
        chat.post_message(
            webhook,
            chat.build_message(
                snapshot,
                fetched_at,
                round(applications) if applications is not None else None,
            ),
            thread_key=_env("GCHAT_THREAD_KEY") or None,
        )
        print("Posted update to Google Chat space.")
    else:
        print("GCHAT_WEBHOOK_URL not set; skipping Chat notification.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
