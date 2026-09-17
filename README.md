# NSE IPO Demand Tracker

Polls the NSE IPO APIs every 15 minutes, appends each category snapshot (with an IST
timestamp) to a Google Sheet, and posts a summary — highlighting the Retail (RII)
status — to the **NSE IPO Industry Count** Google Chat space.

Data sources:

- `https://www.nseindia.com/api/ipo-chart-demand?symbol=<SYMBOL>&exchange=ALL` — price
  vs. cumulative-quantity demand curve, and the authoritative NSE update timestamp.
- `https://www.nseindia.com/api/ipo-active-category?symbol=<SYMBOL>` — category-wise
  live bid status (QIB / NII / Retail / Employees), which is where the retail numbers
  come from. The chart-demand endpoint alone does not break demand down by category.

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
| `IPO_SYMBOL` | `NSE` | IPO symbol to track |
| `IPO_EXCHANGE` | `ALL` | `ALL`, `NSE`, or `BSE` |
| `RETAIL_LOT_SIZE` | `0` | Retail lot size in shares; when set, estimated applications = retail shares bid ÷ lot size |
| `GSHEET_WORKSHEET` | `IPO Demand` | Worksheet tab name (created automatically) |
| `GCHAT_THREAD_KEY` | *(unset)* | Set to e.g. the symbol to keep all updates in one Chat thread |

## Google Sheets access

1. In Google Cloud, create a service account and enable the **Google Sheets API**.
2. Create a JSON key and store its contents in `GOOGLE_SERVICE_ACCOUNT_JSON`.
3. Share the target spreadsheet with the service account's `client_email` as **Editor**.

## Sheet columns

`fetched_at_ist`, `symbol`, `company`, `nse_update_time`, `sr_no`, `category`,
`shares_offered`, `shares_bid`, `subscription_times`, `est_applications`,
`cutoff_shares_bid`

One row per category (including NSE's sub-categories such as `2.1`, `1(a)`). The
header row is written automatically on first run.

`cutoff_shares_bid` is the aggregate quantity bid at cut-off price. NSE publishes no
per-category price ladder, so there is no retail-specific bid price; only retail and
employees may bid at cut-off, making this a close proxy for retail demand.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:IPO_SYMBOL = "NSE"
$env:RETAIL_LOT_SIZE = "0"
$env:GCHAT_WEBHOOK_URL = "<webhook url>"
$env:GSHEET_ID = "<spreadsheet id>"
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content .\service-account.json -Raw

python src\main.py
```

Sheets and Chat are each skipped with a log message if their config is absent, so you
can dry-run the fetch on its own.

## Notes

- GitHub's scheduled runners are best-effort; a `*/15` cron can drift by several
  minutes under load.
- The schedule runs every 15 minutes between 09:00 and 19:00 IST on weekdays
  (`*/15 3-13 * * 1-5` in UTC), matching NSE's bidding window. Use **Run workflow**
  for an off-schedule run.
- NSE occasionally rate-limits; the client warms up cookies and retries three times.
- `est_applications` is an estimate (retail shares bid ÷ lot size) and is only filled
  in when `RETAIL_LOT_SIZE` is configured.
