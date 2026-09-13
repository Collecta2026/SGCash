# Deploying to Render

## The service must point at the right repository

The `sgcash` service on Render was building from **Collecta2026 / Time**, not
**Collecta2026 / SGCash**. `Time` holds Release 1, so every deploy rebuilt
Release 1 no matter what was uploaded to SGCash.

Fix it once, in Render:

1. **Settings** in the left sidebar → the **Build & Deploy** section.
2. **Repository** → **Edit** → choose `Collecta2026/SGCash`, branch `main`.
   If the field is locked because the service is **Blueprint managed**, open the
   blueprint from the link at the top of the service page and change the
   repository there, or disconnect the blueprint first.
3. **Manual Deploy → Deploy latest commit**.

## Every release after that

1. Unzip the release. **Open** the `SGCash` folder and upload what is inside it,
   not the folder itself, so `app.py` lands at the top level of the repository.
   On GitHub: **Add file → Upload files**, drag everything in, commit.
   On Windows, tick **Hidden items** in File Explorer's *View* ribbon first, or
   `.gitignore`, `.env.example` and `.python-version` will be left behind.
2. Render rebuilds within a minute or two. Watch **Events**; two to three
   minutes for the build.
3. Check what is live: `https://sgcash.onrender.com/healthz`

   * `{"status":"ok","version":"4.0"}` — the new release is serving.
   * an older number — the deploy has not run, or it failed. Look at **Events**.

   The footer of every page shows the same version once you are signed in.
4. Press **Ctrl+F5** on the site to drop the cached stylesheet.

Old files left behind in the repository do no harm on their own — an orphaned
template is never rendered — but it is tidier to delete them. Emptying the
repository is only necessary if you want a clean slate.

## What Release 4 looks like

**Dashboard** — available cash as a traffic light, the cash position with the
weeks either side, a cash position line chart and money-in/money-out bars, the
headline totals, and any shortfall.

**Weekly Position** (new menu entry, next to the dashboard) — the latest
recorded balances for NBE, CIB, InstaPay, Vodafone Cash and head office cash;
the six-week table split into EGP, USD and the EGP equivalent; the shortfall
table; and the Confirm the week button that sends the review to the MD and CFO.

The menu reads: **Dashboard · Weekly Position · Weekly Forecast · Data Tables ▾
· Bank & Cash Accounts · Opening Balances · Reports ▾ · Trend Forecast · ⚙
Admin** — and shows each user only what the scheme of delegation permits.

The seven bank and cash accounts are created automatically at zero on first run.
Enter the real balances on **Bank & Cash Accounts**; they appear on Weekly
Position straight away.
