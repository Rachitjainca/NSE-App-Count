"""Append demand snapshots to a Google Sheet using a service account."""

from __future__ import annotations

import json
from typing import Sequence

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

HEADER = [
    "fetched_at_ist",
    "symbol",
    "company",
    "nse_update_time",
    "sr_no",
    "category",
    "shares_offered",
    "shares_bid",
    "subscription_times",
    "est_applications",
    "cutoff_shares_bid",
]


def _client(service_account_json: str) -> gspread.Client:
    info = json.loads(service_account_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def append_rows(
    service_account_json: str,
    spreadsheet_id: str,
    worksheet_name: str,
    rows: Sequence[Sequence[object]],
) -> None:
    if not rows:
        return

    sheet = _client(service_account_json).open_by_key(spreadsheet_id)
    try:
        worksheet = sheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(HEADER))

    if not worksheet.get_all_values():
        worksheet.append_row(HEADER, value_input_option="USER_ENTERED")

    worksheet.append_rows(
        [list(row) for row in rows],
        value_input_option="USER_ENTERED",
    )
