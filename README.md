# NSE IPO Demand Tracker

Polls the NSE IPO APIs every 15 minutes, appends each category snapshot (with an IST
timestamp) to a Google Sheet, appends a bid-price bucket breakdown (Retail / Employee /
Shareholder / bHNI / sHNI) to a second sheet, and posts a summary to the
**NSE IPO Industry Count** Google Chat space. Currently tracking **Jio Platforms
Limited (JPL)** — retail lot size is 12 shares.

Data sources:

- `https://www.nseindia.com/api/ipo-chart-demand?symbol=<SYMBOL>&exchange=<EX>` — price
  vs. cumulative-quantity demand curve, and the authoritative NSE update timestamp.
  `exchange=ALL` can lag behind a single exchange before both order books are merged,
  so the client tries `ALL`, then `NSE`, then `BSE`, and keeps whichever has real data.
- `https://www.nseindia.com/api/ipo-active-category?symbol=<SYMBOL>` — consolidated,
  ALL-exchange category-wise bid details (QIB / NII / Retail / Employees /
  Shareholders), including a Cut-off vs. Price-bid split for Retail/Employee/
  Shareholder. This is what the live issue-information page's "Consolidated Bid
  Details" view renders (confirmed: its totals match the `ipo-chart-demand`
  ALL-exchange curve almost exactly). It returns an empty "Total: 0" shell before
  trading opens for the day, so `ipo-bid-details?symbol=<SYMBOL>&series=<SERIES>`
  (same shape, NSE-exchange-only scale, roughly 30-45% smaller once both exchanges
  are live) is used as a same-shape fallback when that happens.

## Layout

| Path | Purpose |
| --- | --- |
| [src/nse.py](src/nse.py) | NSE session warm-up, API fetch, response normalisation |
| [src/sheets.py](src/sheets.py) | Appends rows to Google Sheets via a service account |
| [src/chat.py](src/chat.py) | Builds and posts the Google Chat webhook message |
| [src/main.py](src/main.py) | Orchestration and configuration |
| [.github/workflows/nse-ipo-demand.yml](.github/workflows/nse-ipo-demand.yml) | 15-minute cron during the IST bidding window |

## Required repository secrets

Set these in `Settings → Secrets and variables → Actions → Secrets` on
`Rachitjainca/NSE-App-Count`:

| Secret | Value |
| --- | --- |
| `GCHAT_WEBHOOK_URL` | The full Google Chat incoming-webhook URL (including `key` and `token`) |
| `GSHEET_ID` | The spreadsheet ID from the sheet URL (`/spreadsheets/d/<ID>/edit`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full service-account JSON key, pasted as-is |

> The webhook URL embeds an API key and token — treat it as a credential. Keep it in
> a secret, never in the workflow file or committed code. If it has already been
> shared in plain text, delete and recreate the webhook in the Chat space.

## Optional repository variables

Set under `Settings → Secrets and variables → Actions → Variables`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `IPO_SYMBOL` | `JPL` | IPO symbol to track |
| `IPO_EXCHANGE` | `ALL` | `ALL`, `NSE`, or `BSE`; falls back automatically if empty |
| `IPO_SERIES` | `EQ` | Series param required by the `ipo-bid-details` fallback |
| `RETAIL_LOT_SIZE` | `12` | Retail lot size in shares; applications = shares bid ÷ lot size |
| `EMPLOYEE_LOT_SIZE` | *(= retail)* | Employee-quota lot size override |
| `SHAREHOLDER_LOT_SIZE` | *(= retail)* | Shareholder-quota lot size override |
| `BHNI_LOT_SIZE` | `900` | bHNI minimum application size in shares (75 lots of 12 for this IPO) |
| `SHNI_LOT_SIZE` | `180` | sHNI minimum application size in shares (15 lots of 12 for this IPO) |
| `GSHEET_WORKSHEET` | `IPO Demand` | Per-category worksheet tab name (created automatically) |
| `GSHEET_BUCKET_WORKSHEET` | `IPO Bid Buckets` | Bid-price bucket worksheet tab name (created automatically) |
| `GSHEET_PRICE_LADDER_WORKSHEET` | `IPO Price Ladder` | Per-price demand-curve worksheet tab name (created automatically) |
| `GCHAT_THREAD_KEY` | *(unset)* | Set to e.g. the symbol to keep all updates in one Chat thread |

## Google Sheets access

1. In Google Cloud, create a service account and enable the **Google Sheets API**.
2. Create a JSON key and store its contents in `GOOGLE_SERVICE_ACCOUNT_JSON`.
3. Share the target spreadsheet with the service account's `client_email` as **Editor**.

## Sheet columns

**`IPO Demand`** — one row per category (including NSE's sub-categories such as `2.1`,
`1(a)`):

`fetched_at_ist`, `symbol`, `company`, `nse_update_time`, `sr_no`, `category`,
`shares_offered`, `shares_bid`, `subscription_times`, `est_applications`,
`cutoff_shares_bid`, `overall_subscription_times`

`cutoff_shares_bid` is the aggregate quantity bid at cut-off price across the whole
issue (demand-curve endpoint; not per category). Retail, Employee, and Shareholder
quotas may bid at cut-off — NII/HNI may not. `overall_subscription_times` is the
whole-issue subscription multiple (NSE's own "Total" row), repeated on every row for
convenience.

**`IPO Bid Buckets`** — one row per (reserved-category group, bid-price bucket):

`fetched_at_ist`, `symbol`, `company`, `nse_update_time`, `group`, `bucket`,
`shares_offered`, `shares_bid`, `subscription_times`, `est_applications`

`group` is one of `QIB`, `Retail`, `Employee`, `Shareholder`, `bHNI` (NII bidding >₹10L),
`sHNI` (NII bidding ₹2L–10L). `bucket` is `Total`, `Cut-off`, or `Price bid` — only
Retail/Employee/Shareholder split into Cut-off/Price bid; QIB/bHNI/sHNI only have
`Total` since institutional/NII bids cannot be placed at cut-off.

**`IPO Price Ladder`** — 3 rows per fetch, bucketing the aggregate demand curve (all
categories combined; NSE does not publish a per-category price ladder):

`fetched_at_ist`, `symbol`, `company`, `nse_update_time`, `price`, `cum_qty`,
`qty_at_price`

`price` is one of `Cut-off`, `Cap ₹<price>` (the price-band cap), or `Below cap` (every
lower price, summed). NSE's raw curve lists a long tail of discrete prices below the
cap with near-zero increments — most bids cluster at cut-off/cap — so these 3 buckets
are more useful than one row per price. `cum_qty` is cumulative demand at/above that
bucket (blank for `Cut-off`, a separate pool — NII/HNI can't use it, only Retail/
Employee/Shareholder can). `qty_at_price` is the bucket's own quantity; `Cap` +
`Below cap` sum to the total priced (non-cut-off) demand.

All three sheets' header rows are written automatically on first run.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:IPO_SYMBOL = "JPL"
$env:IPO_SERIES = "EQ"
$env:RETAIL_LOT_SIZE = "12"
$env:GCHAT_WEBHOOK_URL = "<webhook url>"
$env:GSHEET_ID = "<spreadsheet id>"
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content .\service-account.json -Raw

python src\main.py
```

Sheets and Chat are each skipped with a log message if their config is absent, so you
can dry-run the fetch on its own.

## Notes

- GitHub's `schedule` trigger is unreliable at 15-minute granularity in practice — a
  `*/15` cron was observed firing only every 4-7 hours instead. The workflow now fires
  hourly instead (`5 3-13 * * 1-5` UTC, i.e. ~08:35-19:20 IST, offset 5 min past the
  hour to dodge GitHub's documented top-of-hour congestion) and loops the fetch/publish
  step 4x internally with deterministic ~14.5-minute sleeps, which is far more reliable
  than depending on GitHub to fire four separate 15-minute ticks.
- Use **Run workflow** for an off-schedule run; set its `iterations` input above `1` to
  test the internal loop without waiting for the next hourly tick.
- NSE occasionally rate-limits; the client warms up cookies and retries three times.
- `est_applications` is an estimate (shares bid ÷ lot size) filled in for Retail,
  Employee, Shareholder, bHNI, and sHNI rows/buckets once the relevant lot-size
  variable is configured (all default to `RETAIL_LOT_SIZE` if unset).
