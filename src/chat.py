"""Post IPO demand updates to a Google Chat space via an incoming webhook."""

from __future__ import annotations

import requests

from nse import DemandSnapshot

GROUP_LABELS = {
    "Retail": "Retail (RII)",
    "Employee": "Employees",
    "Shareholder": "Shareholders",
    "bHNI": "bHNI (NII > ₹10L)",
    "sHNI": "sHNI (NII ₹2L–10L)",
}


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _fmt_times(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}x"


def _fmt_pct(part: float | None, whole: float | None) -> str:
    if part is None or not whole:
        return "-"
    return f"{part / whole * 100:.1f}%"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Render as a fixed-width table inside a monospace block (Chat has no HTML tables)."""
    widths = [max(len(r[i]) for r in [headers, *rows]) for i in range(len(headers))]
    sep = ["-" * w for w in widths]

    def fmt_row(row: list[str]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(row, widths))

    return "```\n" + "\n".join(fmt_row(r) for r in [headers, sep, *rows]) + "\n```"


def build_message(
    snapshot: DemandSnapshot,
    fetched_at: str,
    lot_sizes: dict[str, float] | None = None,
    sheet_url: str | None = None,
) -> str:
    lot_sizes = lot_sizes or {}
    buckets = snapshot.bucket_rows()

    lines = [
        f"*IPO Demand — {snapshot.company or snapshot.symbol} ({snapshot.symbol})*",
        f"_NSE update: {snapshot.update_time or 'n/a'} | fetched: {fetched_at} IST_",
        "",
    ]

    group_rows: list[list[str]] = []
    for group, label in GROUP_LABELS.items():
        rows = buckets.get(group)
        if not rows:
            continue
        total = rows.get("Total")
        cutoff, price_bid = rows.get("Cut-off"), rows.get("Price bid")
        applications = "-"
        lot_size = lot_sizes.get(group, 0)
        if total and lot_size and total.shares_bid:
            applications = _fmt_num(round(total.shares_bid / lot_size))
        group_rows.append(
            [
                label,
                _fmt_times(total.subscription_times) if total else "-",
                _fmt_pct(cutoff.shares_bid if cutoff else None, total.shares_bid if total else None),
                _fmt_pct(price_bid.shares_bid if price_bid else None, total.shares_bid if total else None),
                applications,
            ]
        )
    if group_rows:
        lines.append(
            _table(["Group", "Sub(x)", "Cut-off%", "Price-bid%", "Applications"], group_rows)
        )
        lines.append("")

    if snapshot.total_shares_bid is not None:
        lines.append(f"_Total shares bid (demand curve): {_fmt_num(snapshot.total_shares_bid)}_")

    if snapshot.price_ladder:
        # "Below cap"'s cum_qty is the total priced (non-cut-off) demand; use it as the
        # percentage base so Cap + Below cap sum to 100%. Cut-off is a separate pool
        # (not priced), so it gets no percentage.
        total = next((lvl.cum_qty for lvl in snapshot.price_ladder if lvl.price == "Below cap"), None)
        price_rows = [
            [
                lvl.price,
                _fmt_num(lvl.qty_at_price),
                _fmt_pct(lvl.qty_at_price, total) if lvl.price != "Cut-off" else "-",
            ]
            for lvl in snapshot.price_ladder
        ]
        lines.append("")
        lines.append("*Bid-price curve*")
        lines.append(_table(["Bucket", "Qty", "% of priced total"], price_rows))

    if sheet_url:
        lines.append("")
        lines.append(f"<{sheet_url}|Full data in Google Sheets>")

    return "\n".join(lines)


def post_message(webhook_url: str, text: str, thread_key: str | None = None) -> None:
    params = {"threadKey": thread_key} if thread_key else None
    response = requests.post(
        webhook_url,
        params=params,
        json={"text": text},
        timeout=20,
    )
    response.raise_for_status()
