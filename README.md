# Scientific Gate — Cash Flow Budgeting System

Release 2. A bilingual (English / العربية) weekly cash flow forecasting and
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

The engine is covered by 107 tests in `tests/`, each asserting figures computed
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

**Roles** — three seats work the figures: Finance Manager, CFO and Managing
Director, alongside the System Administrator. Credit Control and the Sales
Administrator are data-entry seats: they reach the customer collections table
(contract instalments, down payments, advances and installation instalments)
and nothing else — no cash position, no forecast, no reports, no bank balances,
not even the exchange rate. For them the menu hides what they cannot use; for
every other role it is dimmed with the reason, so people can see the shape of
the system and ask for access.

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

## Still to come

* **Release 3** — Render / AWS Pro deployment (the runbook and deployment files
  are already in the repository), operating manual and user training guide.
