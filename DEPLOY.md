# Deployment runbook — Neon → GitHub → Render → awspro.uk

Follow these six parts in order. Nothing here needs a command line except
Part 2, and there is a point-and-click alternative for that too. Allow about
an hour for the first deployment, most of which is waiting for DNS.

Throughout, the example subdomain is **cashflow.awspro.uk** — substitute
whatever you prefer.

---

## Before you start

You need four accounts or logins to hand:

| What | Why | Cost |
|---|---|---|
| Neon (neon.com) | The Postgres database | Free tier is enough to start |
| GitHub (github.com) | Holds the code Render deploys from | Free, private repository |
| Render (render.com) | Runs the application | Starter plan, about $7/month |
| names.co.uk | DNS for awspro.uk | Already held |

One decision to make now: **use the Starter plan on Render, not Free.** A
free web service spins down after 15 minutes without traffic and takes about
a minute to wake up, which a finance team will read as the system being
broken. This is the same choice made for Collecta.

---

## Part 1 — Create the database on Neon

1. Sign in at **neon.com** and click **New Project**.
2. Name it `sgcash` (or `scientific-gate-cashflow`).
3. Set the region to **AWS eu-central-1 (Frankfurt)**. This matters: Render
   will run in Frankfurt too, and putting the database in the same region
   keeps every page fast. A database in the US behind an app in Europe adds a
   noticeable delay to every screen.
4. Leave the Postgres version at the default and click **Create**.
5. On the project dashboard, click **Connect**. The *Connect to your
   database* panel opens.
6. Leave **Connection pooling** switched **on**, and copy the connection
   string. It looks like this:

   ```
   postgresql://neondb_owner:PASSWORD@ep-something-12345678-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   The `-pooler` in the hostname is what you want — it lets the application's
   two worker processes share connections cleanly.

7. Paste it somewhere safe for the moment. **Do not put it in the repository
   or email it.** It contains the database password in plain text.

You do not need to create any tables. The application builds its own schema
the first time it starts.

---

## Part 2 — Put the code on GitHub

### Option A — upload through the website (no command line)

1. At **github.com**, click **New repository**.
2. Name it `sgcash`, set it to **Private**, and create it without a README.
3. On the empty repository page, click **uploading an existing file**.
4. Unzip the delivery pack on your computer, open the `sgcash` folder, select
   **everything inside it** — `app.py`, `models.py`, `templates`, `static`,
   and the rest — and drag it all onto the page. Drag the *contents* of the
   folder, not the folder itself, or Render will not find `app.py`.
5. Type a message such as "Initial release" and click **Commit changes**.

### Option B — from the command line

```bash
cd sgcash
git init
git add .
git commit -m "Scientific Gate Cash Flow Budgeting System — initial release"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/sgcash.git
git push -u origin main
```

Either way, check afterwards that `app.py` and `requirements.txt` sit at the
**top level** of the repository, not inside a nested `sgcash` folder. This is
the single most common cause of a failed first deploy.

The `.gitignore` already excludes `.env` and the local database file, so no
credentials travel to GitHub.

---

## Part 3 — Deploy on Render

The repository contains `render.yaml`, so Render can build the service for
you. Use the blueprint route unless you have a reason not to.

### Blueprint route

1. Sign in at **render.com**, click **New**, then **Blueprint**.
2. Connect your GitHub account when prompted and pick the `sgcash`
   repository.
3. Render reads `render.yaml` and shows the service it is about to create:
   a Python web service named `sgcash`, Starter plan, Frankfurt.
4. It will ask you for the one value the file deliberately leaves blank:
   **DATABASE_URL**. Paste the Neon connection string from Part 1.
5. Click **Apply**. `SECRET_KEY` is generated for you automatically.

### Manual route

If you would rather create the service by hand: **New > Web Service**, connect
the repository, then set

* **Language / Runtime:** Python 3
* **Region:** Frankfurt
* **Branch:** main
* **Build command:** `pip install -r requirements.txt`
* **Start command:** `gunicorn -c gunicorn.conf.py app:app`
* **Instance type:** Starter
* **Health check path:** `/healthz`

and add three environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string |
| `SECRET_KEY` | a long random string — click *Generate* |
| `PYTHON_VERSION` | `3.12.6` |

### Watch the first build

The build takes two to four minutes. In the **Logs** tab you should see the
dependencies install, then gunicorn start and report it is listening. When the
service shows **Live**, open the `.onrender.com` URL Render gives you and add
`/healthz` to it. You should see:

```json
{"status": "ok", "version": "1.0"}
```

That single line confirms the application started *and* reached the database.

> If the build fails complaining about the Python version, delete the
> `PYTHON_VERSION` variable and redeploy — Render will use its own default,
> which this application is happy with.

---

## Part 4 — First run and configuration

Open the service URL. The first page is the setup wizard; it only ever appears
once, when no user exists.

1. Enter the organisation name — **Scientific Gate Co.**
2. Create the administrator account. Use a strong password: this account can
   see and change everything.
3. Choose the default language, English or العربية. Every user can switch for
   themselves afterwards.
4. **Leave "load a worked example" unticked.** That option fills the system
   with sample hospitals and invented figures. It is there for demonstrations,
   not for the live system.

Then work through the settings in this order, because each one affects the
numbers the next screen shows:

1. **Settings** — confirm the working week is *Sunday – Thursday*, set the
   USD→EGP rate, the forecast horizon, and the minimum cash buffer for each
   currency that should trigger a warning.
2. **Revenue & cost categories** — adjust the shipped list to match Scientific
   Gate's own chart of accounts. Everything analyses against these, so it is
   worth getting right before any data is entered.
3. **Bank & cash accounts** — enter each account with its real balance and the
   date that balance was struck. This is the opening position the whole
   forecast is built on.
4. **Scheme of delegation** — set what each role may do, and which roles may
   approve. The shipped default is a reasonable starting point.
5. **Users** — create the real accounts and assign roles.

Only then start entering forecast data.

---

## Part 5 — Point the awspro.uk subdomain at it

This is two halves: tell Render the name, then tell DNS where to send it.

### On Render

1. Open the service, go to **Settings > Custom Domains**.
2. Click **Add Custom Domain** and enter `cashflow.awspro.uk`.
3. Render shows you the CNAME target — your service's own address, something
   like `sgcash-xxxx.onrender.com`. Copy it exactly.

### On names.co.uk

1. Sign in, open the DNS management for **awspro.uk**.
2. Add a record:

   | Field | Value |
   |---|---|
   | Type | **CNAME** |
   | Host / Name | `cashflow` |
   | Points to / Target | `sgcash-xxxx.onrender.com` |
   | TTL | leave at the default |

3. Save.

A CNAME, not an A record and not AAAA. This is worth double-checking — the
record type was set to AAAA by mistake during the Collecta go-live and cost an
afternoon.

### Verify

Go back to Render and click **Verify** next to the domain. If it fails, the
DNS has not propagated yet; wait a few minutes and try again. Once verified,
Render issues the TLS certificate automatically — usually within a few
minutes, occasionally up to an hour. When the padlock appears on
`https://cashflow.awspro.uk`, you are live. All http traffic redirects to
https on its own.

---

## Part 6 — After go-live

**Deploying an update.** Push to the `main` branch, or upload the changed
files through GitHub's website. Render rebuilds and redeploys automatically.
The database is untouched by a deployment — the application only ever adds
missing columns, it never drops or rewrites existing data.

**Backups.** Neon keeps point-in-time history on its own. Before any
significant change, take a named branch of the database in the Neon console
(*Branches > New branch*) so you have a labelled restore point.

**Monitoring.** `/healthz` is a machine-readable check if you want to point an
uptime monitor at it.

**Watch the logs on the first working day.** Render's Logs tab shows every
request and any error, which is the fastest way to catch a configuration
problem while people are first using the system.

---

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| Build fails: `ModuleNotFoundError` or "no such file app.py" | The repository has a nested folder. `app.py` must be at the top level. |
| Build fails on the Python version | Delete the `PYTHON_VERSION` variable and redeploy. |
| Service starts then crashes; logs mention the database | `DATABASE_URL` is wrong or was pasted with a line break. Re-copy it from Neon's Connect panel in one piece. |
| Pages load but everything is empty, and the setup wizard appears again | The service is pointing at a different, empty database. Check `DATABASE_URL`. |
| Signed out on every page | `SECRET_KEY` is not set. Add it in Render and redeploy. |
| Domain will not verify | Wrong record type (must be CNAME), or the host field holds the full domain instead of just `cashflow`. |
| Site is slow to load the first time each morning | The service is on the Free plan and sleeping. Move it to Starter. |
| Figures look wrong after entry | Check the working week and the non-working-day rule in Settings, then open a week's **Detail** view — it shows each movement's nominal date and the date it was shifted to. |
