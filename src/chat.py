"""Post IPO demand updates to a Google Chat space via an incoming webhook."""

from __future__ import annotations

import requests

from nse import CategoryRow, DemandSnapshot


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _fmt_times(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}x"


def build_message(
    snapshot: DemandSnapshot,
    fetched_at: str,
    retail_applications: float | None,
) -> str:
    retail: CategoryRow | None = snapshot.retail()

    lines = [
        f"*IPO Demand — {snapshot.company or snapshot.symbol} ({snapshot.symbol})*",
        f"_NSE update: {snapshot.update_time or 'n/a'} | fetched: {fetched_at} IST_",
        "",
    ]

    if retail:
        lines.append(f"*Retail (RII): {_fmt_times(retail.subscription_times)}*")
        lines.append(f"  • Shares bid: {_fmt_num(retail.shares_bid)}")
        lines.append(f"  • Shares offered: {_fmt_num(retail.shares_offered)}")
        if retail_applications is not None:
            lines.append(f"  • Est. applications: {_fmt_num(retail_applications)}")
        if snapshot.cutoff_shares_bid is not None:
            lines.append(f"  • Bid at cut-off (incl. employees): {_fmt_num(snapshot.cutoff_shares_bid)}")
        lines.append("")

    lines.append("*All categories*")
    for row in snapshot.categories:
        # Sub-categories use sr_no like "2.1" or "1(a)"; show only top-level rows.
        if not row.sr_no.isdigit():
            continue
        lines.append(f"  • {row.category}: {_fmt_times(row.subscription_times)}")

    if snapshot.total_shares_bid is not None:
        lines.append("")
        lines.append(f"_Total shares bid (demand curve): {_fmt_num(snapshot.total_shares_bid)}_")

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
