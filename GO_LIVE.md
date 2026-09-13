# Going live on cashflow.awspro.uk

The application keeps running on Render. What changes is the address people
type: `cashflow.awspro.uk` instead of `sgcash.onrender.com`. It is the same
arrangement Collecta already uses — I checked, and `collecta.awspro.uk` is a
CNAME pointing at its Render service, so this path is proven on your own domain.

Nothing in the code needs changing. The application builds no absolute links,
so it works under any hostname, and the secure-cookie and proxy settings it
already switches on under Render are exactly what a custom domain needs.

---

## Before you start — get Release 4 live first

There is no point naming a door that opens on the wrong room. Confirm:

```
https://sgcash.onrender.com/healthz   →   {"status":"ok","version":"4.0"}
```

If it still reports an older version, finish `UPDATE_RENDER.md` first — in
particular, the Render service must be pointing at `Collecta2026/SGCash`, not
`Collecta2026/Time`.

---

## Step 1 — Tell Render the name

1. Open dashboard.render.com and click the **sgcash** service.
2. **Settings** in the left sidebar.
3. Scroll to **Custom Domains** and click **Add Custom Domain**.
4. Type `cashflow.awspro.uk` and save.
5. Render now shows the domain as **Unverified**, with the DNS record it wants.
   It will name a **CNAME** target — your service's own Render address,
   `sgcash.onrender.com`. Copy it exactly, without `https://` and without a
   trailing slash.

Leave this page open. You come back to it in Step 3.

---

## Step 2 — Point the DNS at it

Your DNS for `awspro.uk` is with names.co.uk (their name servers answer as
hosts.co.uk — same group, same control panel).

1. Sign in at names.co.uk.
2. Open **Domain names**, click **awspro.uk**, then **DNS** / **Manage DNS**.
   This is the same screen you used when Collecta went live — you should see
   the existing `collecta` record there, which is a useful sanity check that you
   are in the right place.
3. Add a new record:

   | Field | Value |
   |---|---|
   | Type | **CNAME** |
   | Host / Name | `cashflow` |
   | Points to / Target | `sgcash.onrender.com` |
   | TTL | leave the default |

4. Save.

Three things to get right:

* **CNAME, not A and not AAAA.** An AAAA record was set by mistake during the
  Collecta go-live and cost an afternoon. Render's address can change; a CNAME
  follows it, an A record does not.
* **Host is just `cashflow`**, not `cashflow.awspro.uk`. The panel adds the
  domain for you. Putting the whole thing in creates
  `cashflow.awspro.uk.awspro.uk`, which resolves to nothing.
* **Target is the plain hostname** — `sgcash.onrender.com`, not a URL.

Do not touch the existing records. The root `awspro.uk` and `www` records point
at your names.co.uk hosting and the `collecta` record runs Collecta; all three
carry on untouched.

---

## Step 3 — Verify and wait for the certificate

1. Back on Render's **Custom Domains** panel, click **Verify** next to
   `cashflow.awspro.uk`.
2. If it fails, the DNS has not spread yet. Wait five minutes and click again.
   Fifteen minutes is normal; an hour happens.
3. Once it verifies, Render issues the TLS certificate by itself. The status
   moves to **Certificate issued**. Usually a few minutes, occasionally longer.

When the padlock shows on `https://cashflow.awspro.uk`, you are live. Plain
`http://` redirects to `https://` automatically — nobody can reach the site
unencrypted.

---

## Step 4 — Check it properly

| Check | What you should see |
|---|---|
| `https://cashflow.awspro.uk/healthz` | `{"status":"ok","version":"4.0"}` |
| `https://cashflow.awspro.uk` | The sign-in page, padlock showing |
| Sign in, then the footer | `Scientific Gate Cash Flow v4.0` |
| `http://cashflow.awspro.uk` | Redirects to `https://` on its own |
| Sign in, move between pages | You stay signed in — if you are thrown out on every page, `SECRET_KEY` is unset in Render |
| `https://collecta.awspro.uk` | Still works. Nothing you did touched it |

Do this on a phone as well as the office machine. The mobile network resolves
DNS independently, so it is a genuine second opinion rather than a repeat.

---

## Step 5 — Settle the environment

While you are in Render's **Environment** tab, confirm these are set:

* **`DATABASE_URL`** — the Neon connection string, in one piece with no line
  break. If this is wrong you get the setup wizard again on an empty database.
* **`SECRET_KEY`** — any long random string. Without it, sessions are not
  secure and people are signed out constantly.
* **`PYTHON_VERSION`** — `3.12.6`.
* **`WEB_CONCURRENCY`** — `2`.

The plan should be **Starter**, not Free. Free sleeps after fifteen minutes,
which shows up as the site taking half a minute to load the first time each
morning — the thing most likely to make people give up on it in week one.

---

## Step 6 — Turn on the weekly emails

The reminder to the team before each week opens, and the review to the MD and
CFO when the week is confirmed, are both sent by a scheduled call to
`/tasks/notices`. Set it up once:

1. In the admin console, **Settings → Email**, fill in the SMTP details and the
   sender address, and note the **notices token**.
2. In Render: **New → Cron Job**, in the same region, with the command

   ```
   curl -fsS "https://cashflow.awspro.uk/tasks/notices?token=YOUR_TOKEN"
   ```

   and a schedule of `0 7 * * *` — 7am UTC, which is 9am in Cairo.

The endpoint records what it has already sent, so an extra run sends nothing
twice. Running it daily is safe.

---

## Step 7 — Tell people the address

Send the team `https://cashflow.awspro.uk`, not the onrender.com address. Both
work, but only one of them still works if the service is ever renamed or
rebuilt — which is the whole reason for putting it on your own domain.

Worth doing in the same message: ask everyone to sign in once and change their
password, so you find any account problems on a quiet day rather than on a
Sunday morning when the week needs confirming.

---

## If it will not come up

| Symptom | Cause and fix |
|---|---|
| Render will not verify the domain | Wrong record type — it must be CNAME. Or the host field holds the full `cashflow.awspro.uk` instead of just `cashflow`. |
| Verified, but the browser shows a certificate warning | The certificate has not been issued yet. Wait; do not add records to hurry it. |
| The name resolves to your names.co.uk holding page | An existing record for `cashflow` is winning. Delete the old one, keep the CNAME. |
| Works on the office wifi, not on mobile data | DNS has not spread everywhere yet. This resolves on its own within the hour. |
| Site loads but every page signs you out | `SECRET_KEY` missing in Render's Environment tab. |
| Setup wizard appears on a system that already had data | `DATABASE_URL` is pointing at a different or empty database. Re-copy it from Neon. |

A CNAME change is reversible and affects nothing else on the domain. If it goes
wrong, delete the `cashflow` record and everything is exactly as it was.
