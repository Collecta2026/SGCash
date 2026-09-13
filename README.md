# Scientific Gate — Cash Flow Budgeting System

Release 4. A bilingual (English / العربية) weekly cash flow forecasting and
budgeting system for Scientific Gate Co., built on the same architecture as
Collecta: Flask + SQLAlchemy, Neon/Postgres in production, a role and
capability matrix enforced server-side, an approvals queue and a full audit
log.

## Running it locally

```bash
pip install -r requirements.txt
python run_app.py            # http://127.0.0.1:5000
```

The first page is a setup wizard: it creates the administrator, names the
organisation, sets the default language and can load a worked example so the
forecast is populated from the start.

With no `DATABASE_URL` set it uses a local SQLite file, so it runs on a laptop
with nothing else installed. Set `DATABASE_URL` to a Neon connection string for
the hosted deployment (see `.env.example`).

## What is in Release 1

**Ten independently editable tables**, each with its own page, its own fields,
its own filters and its own place in the scheme of delegation:

| Cash in | Cash out |
|---|---|
| Customer collections (instalments, down payments, advances, invoice settlements, retention releases — with customer number, contract and instalment number, and a weekly planner view) | Bank loan instalments |
| Ad hoc cash inflows | Supplier repayment instalments |
| One-off items (in or out) | Cheques payable (issue date, payable date, payee, cheque state) |
| | Customer refunds |
| | Petty cash replenishments (by employee) |
| | Fixed weekly costs |
| | Fixed monthly costs |

**Bank and cash accounts** in EGP and USD, each with a balance, an as-at date
and an overdraft limit, and a switch to include or exclude it from the forecast.

**Opening balances** roll forward automatically from the previous week's
closing balance, and any week can be pinned to an actual bank figure — the
override then rolls on from there.

**Dashboard** — the next six weeks (configurable) per currency with the EGP
equivalent, the lowest forecast balance, and shortage alerts both for a
negative balance and for falling below a set minimum buffer.

**Scheme of delegation** — eight roles, five actions per table (view, enter,
edit, delete, review & approve) plus thirteen core capabilities, all editable
in the app. Entries move draft → submitted → approved; only approved entries
count in the forecast unless the setting says otherwise, and nobody may
approve an entry they created themselves.

**Analysis** — revenue by type, cost elements as a percentage of total revenue
and of total cost, over any window of weeks.

**Rolling trend forecast** — takes the previous six completed weeks (configurable),
measures each revenue type and cost category week by week, and rolls it forward
by least-squares trend or flat average. It shows the weekly average, the trend
per week, the projection for the coming weeks, what is already booked in the
forward forecast, and the variance between the two — plus a projected cash
position running alongside the booked one. Recurring costs generate their own
historical occurrences, so a fixed weekly wage is measured even though no past
entry exists for it.

**Working week** — Sunday to Thursday by default, with Friday and Saturday
non-working and items on those days moving to the next working day. A
Saturday-to-Thursday preset (Friday off only) is available in settings if
Saturday is worked.

## Accuracy

The engine is covered by 169 tests in `tests/`, each asserting figures computed
independently of the code under test:

```bash
python -m pytest -q
```

The design decisions that protect accuracy are documented at the top of
`services.py`: weeks tile the calendar exactly, recurrences are generated as
nominal dates and only then shifted and placed, all money is `Decimal` with a
single rounding point, and the two currencies never mix.

## Release 2 additions

**Branding** — the corporate mark and its palette throughout; the logo is
replaceable in the admin console.

**Dashboard** — the cash position leads in bold, with last week, this week and
the coming weeks alongside it, any shortfall against the minimum buffer, and the
six-week table split into separate EGP and USD sections plus the EGP equivalent.

**Reports** — ten printable reports replacing the old analysis page: cash flow
position, money in and out, revenue analysis, cost elements, collections and
payables schedules, shortfall and funding gap, data completeness, audit trail,
and file uploads. Charts are inline SVG drawn on the server — no library, no
CDN, and they print.

**Roles** — seven: Super Administrator, Administrator, Managing Director, CFO,
Finance Manager, Credit Controller and Sales Administrator. The three seats that
work the figures are the Finance Manager, CFO and MD. Credit Control and the
Sales Administrator are data-entry seats: they reach the customer collections
table (contract instalments, down payments, advances and installation
instalments) and nothing else — no cash position, no forecast, no reports, no
bank balances, not even the exchange rate. Only the Super Administrator rewrites
the scheme of delegation or the financial settings.

**The menu shows exactly what the scheme of delegation permits**, for every
role. Nothing is dimmed, because nothing a user cannot open is listed.

**Available cash** leads the dashboard as a traffic light: green when cash on
hand comfortably covers what is already committed to go out, orange when the
headroom is 15% or less (configurable), red when a shortage is forecast.

**Approvals** are exercised on the table rows themselves rather than through a
separate queue page — entries still cannot reach the forecast unreviewed, and
the count waiting is shown on the dashboard.

**Admin console** — administrator only: the USD rate, every system setting,
users, the scheme of delegation, revenue and cost categories, branding, mail
settings and the audit log.

**Supplier payment instalments** — payment type (contract instalment, deposit,
final payment, letter of credit, retention, invoice), supplier and contract
references, and a weekly planner alongside the one for collections. Customer
collections gained an installation-instalment type.

**Weekly notifications** — a request email goes to whoever owns a table with
gaps before each week opens; when the Account Manager confirms the week, the
cash review goes automatically to the MD and CFO.

**Uploads** — every table has an Excel template, and uploads are previewed row
by row, with each rejection explained, before anything is written.

## Release 3 additions

**Bank and cash balances lead the dashboard.** The first thing on the page is a
table of every account — NBE and CIB in both currencies, InstaPay, Vodafone Cash
and head office cash — with its type, its latest recorded balance, the EGP
equivalent at the current rate and the date that balance was taken, then the EGP
total, the USD total and the combined EGP equivalent. Accounts excluded from the
forecast are still listed but not counted, and are marked as such. The standard
accounts are created automatically on first run at zero; re-running the seed
never touches a balance that has been entered.

**The drop-down menus are readable.** The white `nav.main a` rule was winning on
specificity over the drop-down rule, so the entries under Data Tables and
Reports were being painted white on a white panel — present in the page, but
invisible. Both menus also open on keyboard focus now, not hover alone, so they
work on a tablet. Two tests guard the rule order, and a contrast check covers
every menu entry in both languages.

**Dates read correctly in Arabic.** Dates are wrapped so right-to-left text no
longer reorders "13 Sep 2026" into "Sep 2026 13".

**Deployment** — Render / AWS Pro runbook and deployment files are in the
repository (`DEPLOY.md`, `render.yaml`, `Procfile`, `gunicorn.conf.py`).

**Acceptance tests** — `tests/test_acceptance.py` holds one test per
requirement asked for, named after it: the ten tables, the two currencies, the
Arabic direction, the Sunday-to-Thursday week, the seven roles, the entry-only
seats, the sticky menu, the readable drop-downs, the removed Analysis and
Approvals pages, the reports and their graphs, the upload templates, the
traffic light at 15% headroom, the named banks and the balances table at the top
of the dashboard. If any of it is ever dropped, the test that names it fails.

The suite now stands at 169 tests.

**Version** — `/healthz` and the page footer report the release, so you can
always tell which build is actually live. See `UPDATE_RENDER.md`.

## Release 4 additions

**The dashboard is a summary again.** It carries the available-cash traffic
light, the cash position with the weeks either side of it, any shortfall, the
headline totals — and the same figures drawn: a cash position line across the
window with the confirmed part solid and the forecast dashed against the minimum
buffer, and grouped bars of money in against money out, week by week. Chart
titles and legends are translated, so the Arabic page is Arabic throughout.

**Weekly Position** is a new page holding the detail that used to crowd the
dashboard: the bank and cash balances, the six-week table split by currency,
the shortfall table and the weekly review with the Confirm the week button. It
sits on the menu next to the dashboard and follows the same permissions as the
forecast, so the data-entry seats never see it.
