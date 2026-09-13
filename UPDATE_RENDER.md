# Replacing the code that is live on Render

The site at **sgcash.onrender.com is still running Release 1.** I checked it:

```
https://sgcash.onrender.com/healthz  →  {"status":"ok","version":"1.0"}
```

Release 3 reports `"version":"3.0"`. Until that line changes, nothing in the
last three releases is live — which is why the menu still shows *Analysis* and
*Approvals*, why the drop-down entries still look faded, and why the bank
balances table is not on the dashboard. The code is correct; the deployment is
old.

---

## The one thing to get right

GitHub keeps every file you have ever uploaded. Dragging the new files in
**adds and overwrites, it never deletes.** Release 1 had files that Release 3
does not (`templates/approvals.html`, `templates/analysis.html`), and if those
stay in the repository Render keeps serving pages built from them. So the old
files have to be deleted first.

---

## Step 1 — Empty the repository

1. Go to your repository on github.com.
2. Click each file or folder, then the **⋯** menu (top right of the file view)
   → **Delete file**, and commit. For a folder, open it and use
   **Delete directory**.
3. Do this for everything: `app.py`, `services.py`, `models.py`, all of them,
   and the `templates`, `static` and `tests` folders.
4. Leave only `README.md` if GitHub will not let you delete the last file.

The repository should now be effectively empty. Nothing is lost — the live site
keeps running the last successful build until you deploy again.

## Step 2 — Upload Release 3

1. Unzip `SGCash_Release3.zip` on your computer. You get a folder called
   `SGCash`.
2. **Open that folder.** You upload what is *inside* it, not the folder itself —
   if `app.py` ends up at `SGCash/app.py` in the repository instead of `app.py`,
   Render will not find it.
3. On GitHub: **Add file → Upload files**, then drag in every file and folder
   from inside `SGCash`.
4. Scroll down, type a message such as `Release 3`, and click
   **Commit changes**.

Files beginning with a dot (`.env.example`, `.gitignore`, `.python-version`)
may be hidden by your file manager. On Windows, tick **Hidden items** in the
File Explorer *View* ribbon before dragging, so they come across too.

## Step 3 — Let Render rebuild

Render redeploys on its own within a minute or two of the commit. Watch it in
the Render dashboard under **Events**; the build takes two to three minutes.

If it does not start by itself: **Manual Deploy → Deploy latest commit**.

## Step 4 — Check what is actually live

Open:

```
https://sgcash.onrender.com/healthz
```

* `{"status":"ok","version":"3.0"}` — Release 3 is live. Go to the site,
  press **Ctrl+F5** to clear the cached stylesheet, and sign in.
* `{"status":"ok","version":"1.0"}` — the old build is still being served.
  The upload did not land or the deploy did not run. Check the repository
  really does contain `app.py` at the top level.

The footer of every page also shows the version, so you can confirm at a glance
after signing in.

## Step 5 — What you should see

Signed in as an administrator, on the dashboard:

* **Latest recorded balances** is the first block on the page — NBE (EGP and
  USD), CIB (EGP and USD), InstaPay, Vodafone Cash and head office cash, each
  with its balance, EGP equivalent and as-at date, then the EGP total, the USD
  total and the combined equivalent.
* Below it, the **Available cash** box — green, orange or red.
* The menu reads **Dashboard · Weekly Forecast · Data Tables ▾ · Bank & Cash
  Accounts · Opening Balances · Reports ▾ · Trend Forecast · ⚙ Admin.**
  There is no *Analysis* and no *Approvals*.
* Hovering **Data Tables** or **Reports** shows dark, readable entries — not
  the faint grey ones.
* Scrolling down keeps the blue menu bar pinned to the top of the window.
* **Reports ▾** lists ten reports, the last of which is **Upload Data Files**,
  with an Excel template for every table.

The seven bank and cash accounts are created automatically the first time the
new code runs, at zero. Enter the real balances on **Bank & Cash Accounts** and
they appear on the dashboard immediately.

## If the page still looks the same after a successful deploy

That is the browser holding the old stylesheet. **Ctrl+F5** (Windows) or
**Cmd+Shift+R** (Mac). If it persists, open the site in a private window to
confirm.
