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


def build_message(
    snapshot: DemandSnapshot,
    fetched_at: str,
    lot_sizes: dict[str, float] | None = None,
) -> str:
    lot_sizes = lot_sizes or {}
    buckets = snapshot.bucket_rows()

    lines = [
        f"*IPO Demand — {snapshot.company or snapshot.symbol} ({snapshot.symbol})*",
        f"_NSE update: {snapshot.update_time or 'n/a'} | fetched: {fetched_at} IST_",
        "",
    ]

    for group, label in GROUP_LABELS.items():
        rows = buckets.get(group)
        if not rows:
            continue
        total = rows.get("Total")
        lines.append(f"*{label}: {_fmt_times(total.subscription_times) if total else '-'}*")
        if total:
            lot_size = lot_sizes.get(group, 0)
            if lot_size:
                applications = (total.shares_bid or 0) / lot_size if total.shares_bid else None
                if applications is not None:
                    lines.append(f"  • Est. applications: {_fmt_num(round(applications))}")
        cutoff, price_bid = rows.get("Cut-off"), rows.get("Price bid")
        if (cutoff or price_bid) and total and total.shares_bid:
            lines.append(
                f"  • Cut-off: {_fmt_pct(cutoff.shares_bid if cutoff else None, total.shares_bid)} | "
                f"Price bid: {_fmt_pct(price_bid.shares_bid if price_bid else None, total.shares_bid)}"
            )
        lines.append("")

    lines.append("*All categories*")
    for row in snapshot.categories:
        if not row.is_top_level:
            continue
        lines.append(f"  • {row.category}: {_fmt_times(row.subscription_times)}")

    if snapshot.total_shares_bid is not None:
        lines.append("")
        lines.append(f"_Total shares bid (demand curve): {_fmt_num(snapshot.total_shares_bid)}_")

    priced = [lvl for lvl in snapshot.price_ladder if lvl.price != "Cut-off"]
    if priced:
        cap, floor = priced[0], priced[-1]
        total = floor.cum_qty
        cap_pct = (
            f" ({cap.qty_at_price / total * 100:.1f}% of total)"
            if total and cap.qty_at_price is not None
            else ""
        )
        cutoff = next((lvl for lvl in snapshot.price_ladder if lvl.price == "Cut-off"), None)
        lines.append("")
        lines.append(f"*Bid-price curve*: {len(priced)} prices, ₹{floor.price}–₹{cap.price}")
        lines.append(f"  • At cap ₹{cap.price}: {_fmt_num(cap.qty_at_price)}{cap_pct}")
        if cutoff:
            lines.append(f"  • Cut-off bucket: {_fmt_num(cutoff.qty_at_price)}")
        lines.append("  • Full per-price bucketing: see the 'IPO Price Ladder' sheet")

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
