"""Forecast engine and business logic.

Design notes that matter for accuracy
-------------------------------------
*   Weeks tile the calendar in exact 7-day blocks from a fixed anchor
    (the configured week-start weekday).  Every date therefore falls in
    exactly one week — no gaps, no overlaps, nothing lost or counted
    twice.  A week is identified by the ISO date of its first day.
*   Recurring costs generate a *nominal* date, which is then shifted off
    a non-working day, and only then placed in a week.  Nothing is
    scanned or de-duplicated, so a recurrence can never be dropped or
    doubled.
*   All money is `Decimal`, quantised to two places once, at the point
    a figure is presented.  Intermediate sums are never rounded.
*   Currencies never mix.  EGP and USD are forecast independently; the
    EGP-equivalent column is a presentation layer over the two.
"""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import inspect

from models import (db, D, BankAccount, OpeningOverride, RevenueType, CostCategory,
                    CURRENCIES, COMMITTED_STATUSES, PENDING_STATUSES, AuditLog)
from streams import STREAMS, STREAM_ORDER

VERSION = "1.0"
DEFAULT_PRODUCT = "Scientific Gate Cash Flow"
DEFAULT_ORG = "Scientific Gate Co."

TWO = Decimal("0.01")

# Working-week presets: the weekday numbers that are working days.
# Python weekday(): Monday=0 ... Sunday=6
WORK_WEEKS = {
    "sun_thu": {6, 0, 1, 2, 3},        # Egypt — Friday and Saturday off
    "sat_thu": {5, 6, 0, 1, 2, 3},     # Egypt — Friday off only
    "mon_fri": {0, 1, 2, 3, 4},        # UK / Europe
    "mon_sat": {0, 1, 2, 3, 4, 5},
    "all": {0, 1, 2, 3, 4, 5, 6},
}

DEFAULTS = {
    "work_week": "sun_thu",
    "week_start": "6",            # Sunday
    "weekend_rule": "next",       # next | previous | none
    "horizon_weeks": "52",
    "dashboard_weeks": "6",
    "fx_rate": "48.50",           # USD -> EGP
    "min_buffer_EGP": "0",
    "min_buffer_USD": "0",
    "include_pending": "0",
    "apply_certainty": "0",
    "trend_lookback": "6",
    "trend_project": "6",
    "trend_method": "linear",     # linear | average
    "notify_days_before": "3",
    "smtp_port": "587",
    "smtp_tls": "1",
    "notices_token": "",
    "product_name": DEFAULT_PRODUCT,
    "org_name": DEFAULT_ORG,
    "default_lang": "en",
}


# ============================================================
# Settings helpers
# ============================================================

def setting(key, default=None):
    from models import get_setting
    if default is None:
        default = DEFAULTS.get(key, "")
    return get_setting(key, default)


def setting_int(key, default=None):
    try:
        return int(str(setting(key, default)).strip())
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, 0) or 0)


def setting_dec(key, default=None):
    try:
        return D(setting(key, default))
    except Exception:
        return Decimal("0")


def setting_bool(key):
    return str(setting(key)).strip() in ("1", "true", "True", "yes", "on")


def get_brand():
    return {"product": setting("product_name") or DEFAULT_PRODUCT,
            "org": setting("org_name") or DEFAULT_ORG}


def fx_rate():
    """USD -> EGP. Guarded so a bad value can never zero out the equivalent."""
    r = setting_dec("fx_rate")
    return r if r > 0 else D(DEFAULTS["fx_rate"])


def q2(v):
    """Quantise to 2 dp, half-up — the single rounding point."""
    return D(v).quantize(TWO, rounding=ROUND_HALF_UP)


def money(v):
    return float(q2(v))


# ============================================================
# Branding
# ============================================================

def get_logo():
    """(bytes, mime) of the uploaded logo, or None to fall back to the shipped file."""
    import base64
    from models import get_setting as gs
    raw = gs("org_logo", "")
    if not raw:
        return None
    try:
        return base64.b64decode(raw), gs("org_logo_mime", "image/png")
    except Exception:
        return None


def set_logo(data, mime="image/png"):
    import base64
    from models import set_setting as ss
    ss("org_logo", base64.b64encode(data).decode("ascii"))
    ss("org_logo_mime", mime or "image/png")


def clear_logo():
    from models import set_setting as ss
    ss("org_logo", "")
    ss("org_logo_mime", "")


# ============================================================
# Calendar
# ============================================================

def working_days():
    return WORK_WEEKS.get(setting("work_week"), WORK_WEEKS["sun_thu"])


def is_working_day(d):
    return d.weekday() in working_days()


def adjust_to_working_day(d, rule=None, work=None):
    """Move a date off a non-working day.

    'next'     -> forward to the following working day (bank settles late)
    'previous' -> back to the preceding working day (prudent for outflows)
    'none'     -> leave the date alone
    """
    if d is None:
        return None
    rule = rule or setting("weekend_rule")
    work = work if work is not None else working_days()
    if rule == "none" or not work or d.weekday() in work:
        return d
    step = 1 if rule != "previous" else -1
    out = d
    for _ in range(7):
        out = out + timedelta(days=step)
        if out.weekday() in work:
            return out
    return d


def week_start_weekday():
    try:
        v = int(setting("week_start"))
    except (TypeError, ValueError):
        v = 6
    return v if 0 <= v <= 6 else 6


def week_start_for(d, start_wd=None):
    """First day of the 7-day block containing `d`."""
    start_wd = week_start_weekday() if start_wd is None else start_wd
    delta = (d.weekday() - start_wd) % 7
    return d - timedelta(days=delta)


def forecast_start():
    s = setting("forecast_start", "")
    if s:
        try:
            return week_start_for(date.fromisoformat(s))
        except ValueError:
            pass
    return week_start_for(date.today())


def build_weeks(n=None, start=None):
    """The forecast horizon: contiguous 7-day blocks, oldest first."""
    n = n or setting_int("horizon_weeks")
    n = max(1, min(260, n))
    start = start or forecast_start()
    start_wd = week_start_weekday()
    work = working_days()
    out = []
    for i in range(n):
        s = start + timedelta(days=7 * i)
        e = s + timedelta(days=6)
        wdays = [s + timedelta(days=k) for k in range(7) if (s + timedelta(days=k)).weekday() in work]
        out.append({
            "week_id": s.isoformat(),
            "index": i,
            "start": s,
            "end": e,
            "work_start": wdays[0] if wdays else s,
            "work_end": wdays[-1] if wdays else e,
            "iso_year": s.isocalendar()[0],
            "iso_week": s.isocalendar()[1],
            "label": f"W{i + 1} · {s.strftime('%d %b')} – {e.strftime('%d %b %Y')}",
            "short": s.strftime("%d %b"),
        })
    # unused variable guard for linting clarity
    del start_wd
    return out


def week_index_map(weeks):
    return {w["week_id"]: i for i, w in enumerate(weeks)}


def week_for_date(d, weeks):
    """Index of the week containing `d`, or None if outside the horizon."""
    if not weeks or d is None:
        return None
    first = weeks[0]["start"]
    idx = (d - first).days // 7
    if 0 <= idx < len(weeks):
        return idx
    return None


# ============================================================
# Opening balances
# ============================================================

def bank_opening():
    """Opening cash per currency from the bank/cash accounts register."""
    out = {c: Decimal("0") for c in CURRENCIES}
    for a in BankAccount.query.filter_by(include_in_forecast=True).all():
        if a.currency in out:
            out[a.currency] += D(a.balance)
    return out


def opening_overrides():
    out = {}
    for o in OpeningOverride.query.all():
        out.setdefault(o.week_id, {})[o.currency] = D(o.opening)
    return out


def set_opening_override(week_id, currency, value, who="", reason=""):
    row = OpeningOverride.query.filter_by(week_id=week_id, currency=currency).first()
    if row is None:
        row = OpeningOverride(week_id=week_id, currency=currency)
        db.session.add(row)
    row.opening = q2(value)
    row.set_by = who
    row.reason = reason
    db.session.commit()
    return row


def clear_opening_override(week_id, currency):
    row = OpeningOverride.query.filter_by(week_id=week_id, currency=currency).first()
    if row:
        db.session.delete(row)
        db.session.commit()


# ============================================================
# Movement generation
# ============================================================

def _statuses(include_pending):
    return set(COMMITTED_STATUSES) | (set(PENDING_STATUSES) if include_pending else set())


def _row_excluded(key, row):
    """Stream-specific exclusions beyond workflow status."""
    if key == "cheques":
        # A cleared cheque has already moved; returned/cancelled never will.
        if (row.cheque_status or "issued") in ("cleared", "returned", "cancelled"):
            return True
    return False


def _direction_of(key, row):
    d = STREAMS[key]["direction"]
    if d == "both":
        return "in" if (getattr(row, "direction", "out") or "out") == "in" else "out"
    return d


def _effective_on(d, eff_from, eff_to):
    if eff_from and d < eff_from:
        return False
    if eff_to and d > eff_to:
        return False
    return True


def _weighted(amount, key, row, apply_certainty, direction):
    amt = D(amount)
    if apply_certainty and direction == "in":
        c = getattr(row, "certainty", None)
        if c is not None:
            pct = D(c)
            if pct < 0:
                pct = Decimal("0")
            if pct > 100:
                pct = Decimal("100")
            amt = amt * pct / Decimal("100")
    return amt


def generate_movements(weeks, include_pending=False, apply_certainty=None,
                       streams=None, statuses=None):
    """Every cash movement that lands inside the horizon.

    Returns a list of dicts:
        week (int index), date, stream, row_id, direction, currency,
        amount (Decimal, positive), description, category, status, pending
    """
    if apply_certainty is None:
        apply_certainty = setting_bool("apply_certainty")
    allowed = set(statuses) if statuses else _statuses(include_pending)
    pending_set = set(PENDING_STATUSES)
    keys = streams or STREAM_ORDER
    rule = setting("weekend_rule")
    work = working_days()
    horizon_start, horizon_end = weeks[0]["start"], weeks[-1]["end"]
    # Recurrences are generated over a padded window so an occurrence whose
    # nominal date sits just outside the horizon, but which shifts into it off
    # a non-working day, is still caught.  Anything that lands outside after
    # shifting is dropped when it is placed in a week.
    gen_start, gen_end = horizon_start - timedelta(days=8), horizon_end + timedelta(days=8)
    rev_names = {r.id: r.name_en for r in RevenueType.query.all()}
    cost_names = {c.id: c.name_en for c in CostCategory.query.all()}
    out = []

    for key in keys:
        spec = STREAMS[key]
        model = spec["model"]
        occ = spec["occurrence"]
        for row in model.query.all():
            if row.status not in allowed or _row_excluded(key, row):
                continue
            direction = _direction_of(key, row)
            amount = _weighted(row.amount, key, row, apply_certainty, direction)
            if amount == 0:
                continue
            cur = row.currency if row.currency in CURRENCIES else "EGP"
            cat = (cost_names.get(getattr(row, "cost_category_id", None))
                   if direction == "out" else
                   rev_names.get(getattr(row, "revenue_type_id", None)))
            base = dict(stream=key, row_id=row.id, direction=direction, currency=cur,
                        status=row.status, pending=(row.status in pending_set),
                        category=cat or ("Uncategorised" if direction == "out" else "Unclassified"),
                        description=_describe(key, row))

            if occ == "dated":
                nominal = row.due_date
                if not nominal:
                    continue
                dates = [nominal]
            elif occ == "weekly":
                dates = _weekly_dates(row, gen_start, gen_end)
            elif occ == "monthly":
                dates = _monthly_dates(row, gen_start, gen_end)
            else:
                dates = []

            for nominal in dates:
                d = adjust_to_working_day(nominal, rule, work)
                wi = week_for_date(d, weeks)
                if wi is None:
                    continue
                out.append(dict(base, week=wi, date=d, nominal=nominal, amount=amount))

    out.sort(key=lambda m: (m["week"], m["date"], m["stream"], m["row_id"]))
    return out


def _weekly_dates(row, h_start, h_end):
    """Every occurrence of a weekly cost inside the horizon (nominal dates)."""
    wd = int(row.weekday or 0)
    # first occurrence on/after horizon start
    delta = (wd - h_start.weekday()) % 7
    d = h_start + timedelta(days=delta)
    out = []
    while d <= h_end:
        if _effective_on(d, row.effective_from, row.effective_to):
            out.append(d)
        d += timedelta(days=7)
    return out


def _monthly_dates(row, h_start, h_end):
    """Every occurrence of a monthly cost inside the horizon (nominal dates).

    A nominal day beyond the length of a month clamps to that month's last
    day — the standard treatment for a 31st-of-the-month charge.
    """
    dom = max(1, min(31, int(row.day_of_month or 1)))
    y, m = h_start.year, h_start.month
    out = []
    while (y, m) <= (h_end.year, h_end.month):
        last = calendar.monthrange(y, m)[1]
        d = date(y, m, min(dom, last))
        if h_start <= d <= h_end and _effective_on(d, row.effective_from, row.effective_to):
            out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _describe(key, row):
    spec = STREAMS[key]
    for f in ("customer", "supplier", "lender", "payee", "employee_name", "source", "counterparty"):
        v = getattr(row, f, None)
        if v:
            extra = row.description or getattr(row, "purpose", None) or getattr(row, "reason", None)
            no = getattr(row, "instalment_no", None)
            bits = [str(v)]
            if key == "cheques" and getattr(row, "cheque_no", None):
                bits.append(f"chq {row.cheque_no}")
            elif no:
                bits.append(f"inst {no}")
            if extra:
                bits.append(str(extra))
            return " — ".join(bits)
    return row.description or spec["en"]


# ============================================================
# The forecast
# ============================================================

def build_forecast(weeks=None, include_pending=None, apply_certainty=None):
    """The complete weekly forecast, per currency, with EGP equivalent.

    closing = opening + inflow - outflow
    next opening = this closing, unless an override pins it.
    """
    if include_pending is None:
        include_pending = setting_bool("include_pending")
    weeks = weeks or build_weeks()
    moves = generate_movements(weeks, include_pending=True, apply_certainty=apply_certainty)
    overrides = opening_overrides()
    opening0 = bank_opening()
    rate = fx_rate()

    n = len(weeks)
    zero = lambda: {c: Decimal("0") for c in CURRENCIES}          # noqa: E731
    inflow = [zero() for _ in range(n)]
    outflow = [zero() for _ in range(n)]
    pend_in = [zero() for _ in range(n)]
    pend_out = [zero() for _ in range(n)]

    for m in moves:
        i, c = m["week"], m["currency"]
        if m["pending"]:
            (pend_in if m["direction"] == "in" else pend_out)[i][c] += m["amount"]
            if not include_pending:
                continue
        (inflow if m["direction"] == "in" else outflow)[i][c] += m["amount"]

    rows = []
    prev_closing = dict(opening0)
    for i, w in enumerate(weeks):
        ov = overrides.get(w["week_id"], {})
        opening = {}
        for c in CURRENCIES:
            opening[c] = ov[c] if c in ov else prev_closing[c]
        closing = {c: opening[c] + inflow[i][c] - outflow[i][c] for c in CURRENCIES}
        row = {
            "week": w,
            "opening": opening,
            "inflow": inflow[i],
            "outflow": outflow[i],
            "net": {c: inflow[i][c] - outflow[i][c] for c in CURRENCIES},
            "closing": closing,
            "pending_in": pend_in[i],
            "pending_out": pend_out[i],
            "overridden": {c: (c in ov) for c in CURRENCIES},
        }
        for k in ("opening", "inflow", "outflow", "net", "closing"):
            row[k + "_eqv"] = egp_equivalent(row[k], rate)
        row["alerts"] = week_alerts(row)
        rows.append(row)
        prev_closing = closing

    return {"weeks": weeks, "rows": rows, "movements": moves, "fx": rate,
            "include_pending": include_pending,
            "opening": opening0, "overrides": overrides}


def egp_equivalent(by_ccy, rate=None):
    rate = rate if rate is not None else fx_rate()
    return D(by_ccy.get("EGP", 0)) + D(by_ccy.get("USD", 0)) * rate


def buffers():
    return {c: setting_dec(f"min_buffer_{c}") for c in CURRENCIES}


def week_alerts(row):
    """Shortage warnings for one week."""
    out = []
    buf = buffers()
    for c in CURRENCIES:
        cl = row["closing"][c]
        if cl < 0:
            out.append({"level": "danger", "currency": c, "amount": cl,
                        "key": "alert_overdrawn"})
        elif buf[c] > 0 and cl < buf[c]:
            out.append({"level": "warning", "currency": c, "amount": cl,
                        "key": "alert_below_buffer"})
    return out


def weekly_stream(key, weeks=None):
    """One stream's entries laid out week by week, grouped for planning.

    Every status is returned — the planner is where entries are created
    and chased, so drafts must be visible — with the group taken from
    the stream's own `group_by` field (collection type, for collections).
    """
    from streams import STREAMS
    spec = STREAMS[key]
    model = spec["model"]
    weeks = weeks or build_weeks()
    rule, work = setting("weekend_rule"), working_days()
    group_field = spec.get("group_by")
    cells = [{"week": w, "rows": [],
              "totals": {c: Decimal("0") for c in CURRENCIES},
              "groups": {}} for w in weeks]
    grand = {c: Decimal("0") for c in CURRENCIES}
    group_totals = {}

    for row in model.query.all():
        if row.due_date is None:
            continue
        d = adjust_to_working_day(row.due_date, rule, work)
        wi = week_for_date(d, weeks)
        if wi is None:
            continue
        g = (getattr(row, group_field, None) if group_field else None) or "other"
        cur = row.currency if row.currency in CURRENCIES else "EGP"
        amt = D(row.amount)
        cell = cells[wi]
        cell["rows"].append({"row": row, "date": d, "group": g})
        cell["totals"][cur] += amt
        cell["groups"].setdefault(g, {c: Decimal("0") for c in CURRENCIES})[cur] += amt
        grand[cur] += amt
        group_totals.setdefault(g, {c: Decimal("0") for c in CURRENCIES})[cur] += amt

    for cell in cells:
        cell["rows"].sort(key=lambda r: (r["date"], r["group"]))
        cell["eqv"] = egp_equivalent(cell["totals"])
    return {"cells": cells, "grand": grand, "group_totals": group_totals,
            "grand_eqv": egp_equivalent(grand), "group_field": group_field}


def movements_for_week(forecast, index):
    return [m for m in forecast["movements"] if m["week"] == index]


def dashboard(weeks_n=None):
    """The next N weeks, plus the headline numbers and shortage alerts."""
    n = weeks_n or setting_int("dashboard_weeks")
    full = build_forecast()
    head = full["rows"][:n]
    rate = full["fx"]
    totals = {
        "inflow": {c: sum((r["inflow"][c] for r in head), Decimal("0")) for c in CURRENCIES},
        "outflow": {c: sum((r["outflow"][c] for r in head), Decimal("0")) for c in CURRENCIES},
    }
    totals["net"] = {c: totals["inflow"][c] - totals["outflow"][c] for c in CURRENCIES}
    opening = head[0]["opening"] if head else bank_opening()
    closing = head[-1]["closing"] if head else opening
    lowest = {}
    for c in CURRENCIES:
        if head:
            i = min(range(len(head)), key=lambda k: head[k]["closing"][c])
            lowest[c] = {"week": head[i]["week"], "amount": head[i]["closing"][c]}
        else:
            lowest[c] = {"week": None, "amount": Decimal("0")}
    alerts = []
    for r in head:
        for a in r["alerts"]:
            alerts.append(dict(a, week=r["week"]))
    # first week a currency goes short, anywhere in the full horizon
    first_short = {}
    for c in CURRENCIES:
        first_short[c] = next((r["week"] for r in full["rows"] if r["closing"][c] < 0), None)
    return {
        "forecast": full, "rows": head, "totals": totals, "opening": opening,
        "closing": closing, "lowest": lowest, "alerts": alerts, "fx": rate,
        "first_short": first_short,
        "opening_eqv": egp_equivalent(opening, rate),
        "closing_eqv": egp_equivalent(closing, rate),
        "totals_eqv": {k: egp_equivalent(v, rate) for k, v in totals.items()},
        "banks": BankAccount.query.order_by(BankAccount.currency, BankAccount.name).all(),
    }


# ============================================================
# Analysis
# ============================================================

def analysis(weeks=None, from_index=0, to_index=None):
    """Revenue by type, cost by category, and cost as a % of revenue.

    Percentages are computed on the EGP-equivalent totals so a single
    figure covers both currencies; per-currency splits are returned too.
    """
    f = build_forecast(weeks=weeks)
    rows = f["rows"]
    to_index = len(rows) - 1 if to_index is None else to_index
    lo, hi = max(0, from_index), min(len(rows) - 1, to_index)
    rate = f["fx"]
    sel = [m for m in f["movements"] if lo <= m["week"] <= hi
           and (f["include_pending"] or not m["pending"])]

    def bucket(direction):
        agg = {}
        for m in sel:
            if m["direction"] != direction:
                continue
            b = agg.setdefault(m["category"], {c: Decimal("0") for c in CURRENCIES})
            b[m["currency"]] += m["amount"]
        out = []
        for name, by in sorted(agg.items()):
            eq = egp_equivalent(by, rate)
            out.append({"name": name, "by": by, "eqv": eq})
        out.sort(key=lambda r: r["eqv"], reverse=True)
        return out

    revenue = bucket("in")
    costs = bucket("out")
    rev_total = sum((r["eqv"] for r in revenue), Decimal("0"))
    cost_total = sum((r["eqv"] for r in costs), Decimal("0"))

    def pct(v, base):
        if base == 0:
            return None
        return (D(v) / base * Decimal("100"))

    for r in revenue:
        r["pct_of_revenue"] = pct(r["eqv"], rev_total)
    for c in costs:
        c["pct_of_revenue"] = pct(c["eqv"], rev_total)
        c["pct_of_cost"] = pct(c["eqv"], cost_total)

    by_stream = {}
    for m in sel:
        s = by_stream.setdefault(m["stream"], {"in": Decimal("0"), "out": Decimal("0")})
        s[m["direction"]] += m["amount"] * (rate if m["currency"] == "USD" else Decimal("1"))

    return {
        "from": rows[lo]["week"] if rows else None,
        "to": rows[hi]["week"] if rows else None,
        "revenue": revenue, "costs": costs,
        "revenue_total": rev_total, "cost_total": cost_total,
        "net": rev_total - cost_total,
        "cost_ratio": pct(cost_total, rev_total),
        "by_stream": by_stream, "fx": rate,
    }


# ============================================================
# Rolling trend forecast
# ============================================================

def history_weeks(lookback=None, anchor=None):
    """The complete weeks immediately BEFORE the forecast starts.

    These are the weeks the trend is measured over.  Because the weekly
    blocks tile the calendar from a fixed anchor, the history window is
    contiguous with the forward horizon — week -1 ends the day before
    week 0 begins.
    """
    lookback = lookback or setting_int("trend_lookback")
    lookback = max(2, min(52, lookback))
    start = anchor or forecast_start()
    return build_weeks(n=lookback, start=start - timedelta(days=7 * lookback))


def least_squares(series):
    """(slope, intercept) of the best-fit line through `series` by index.

    Plain Decimal arithmetic — no floats — so the projection is exactly
    reproducible from the figures on screen.
    """
    n = len(series)
    if n == 0:
        return Decimal("0"), Decimal("0")
    if n == 1:
        return Decimal("0"), D(series[0])
    xs = [Decimal(i) for i in range(n)]
    ys = [D(v) for v in series]
    nx = Decimal(n)
    mx = sum(xs, Decimal("0")) / nx
    my = sum(ys, Decimal("0")) / nx
    num = sum(((x - mx) * (y - my) for x, y in zip(xs, ys)), Decimal("0"))
    den = sum(((x - mx) ** 2 for x in xs), Decimal("0"))
    if den == 0:
        return Decimal("0"), my
    slope = num / den
    return slope, my - slope * mx


def project_series(series, ahead, method=None):
    """Project `ahead` further weeks from a historical weekly series.

    'linear'  — least-squares trend, floored at zero (a cash line cannot
                trend into negative receipts or negative costs).
    'average' — the flat mean of the window.
    A window shorter than three points always falls back to the average,
    because a slope through two points is noise, not a trend.
    """
    method = method or setting("trend_method")
    ys = [D(v) for v in series]
    n = len(ys)
    if n == 0:
        return [Decimal("0")] * ahead
    avg = sum(ys, Decimal("0")) / Decimal(n)
    if method != "linear" or n < 3:
        return [avg] * ahead
    slope, intercept = least_squares(ys)
    out = []
    for j in range(n, n + ahead):
        v = intercept + slope * Decimal(j)
        out.append(v if v > 0 else Decimal("0"))
    return out


def trend_forecast(lookback=None, ahead=None, method=None, anchor=None):
    """Roll the last N weeks forward: per revenue type and cost category.

    Returns, for every element and currency, the weekly history, its
    average and trend, the projection for the coming weeks, the amount
    already booked in the forward forecast, and the variance between
    them — plus a projected cash position alongside the booked one.
    """
    lookback = lookback or setting_int("trend_lookback")
    ahead = ahead or setting_int("trend_project")
    ahead = max(1, min(52, ahead))
    method = method or setting("trend_method")
    rate = fx_rate()

    hist = history_weeks(lookback, anchor)
    n = len(hist)
    hmoves = generate_movements(hist, include_pending=setting_bool("include_pending"))

    fwd = build_forecast()
    future = fwd["rows"][:ahead]
    fweeks = [r["week"] for r in future]

    # element key -> currency -> weekly series
    series = {}
    for m in hmoves:
        key = (m["direction"], m["category"])
        by = series.setdefault(key, {c: [Decimal("0")] * n for c in CURRENCIES})
        by[m["currency"]][m["week"]] += m["amount"]

    # what the forward forecast already carries for the same elements
    booked = {}
    for m in fwd["movements"]:
        if m["week"] >= ahead:
            continue
        key = (m["direction"], m["category"])
        by = booked.setdefault(key, {c: [Decimal("0")] * ahead for c in CURRENCIES})
        by[m["currency"]][m["week"]] += m["amount"]

    rows = []
    proj_in = [{c: Decimal("0") for c in CURRENCIES} for _ in range(ahead)]
    proj_out = [{c: Decimal("0") for c in CURRENCIES} for _ in range(ahead)]

    for key in sorted(set(series) | set(booked)):
        direction, name = key
        by = series.get(key, {c: [Decimal("0")] * n for c in CURRENCIES})
        bk = booked.get(key, {c: [Decimal("0")] * ahead for c in CURRENCIES})
        cell = {}
        for c in CURRENCIES:
            hist_series = by[c]
            projected = project_series(hist_series, ahead, method)
            slope, _ = least_squares(hist_series)
            total = sum(hist_series, Decimal("0"))
            cell[c] = {
                "series": hist_series,
                "total": total,
                "avg": total / Decimal(n) if n else Decimal("0"),
                "slope": slope,
                "projected": projected,
                "projected_total": sum(projected, Decimal("0")),
                "booked": bk[c],
                "booked_total": sum(bk[c], Decimal("0")),
            }
            for j in range(ahead):
                target = proj_in if direction == "in" else proj_out
                target[j][c] += projected[j]

        eqv = lambda f: D(cell["EGP"][f]) + D(cell["USD"][f]) * rate   # noqa: E731
        avg_eqv = eqv("avg")
        slope_eqv = eqv("slope")
        rows.append({
            "direction": direction, "name": name, "by": cell,
            "total_eqv": eqv("total"), "avg_eqv": avg_eqv, "slope_eqv": slope_eqv,
            "projected_eqv": eqv("projected_total"),
            "booked_eqv": eqv("booked_total"),
            "variance_eqv": eqv("booked_total") - eqv("projected_total"),
            "trend_pct": (slope_eqv / avg_eqv * Decimal("100")) if avg_eqv else None,
        })

    rows.sort(key=lambda r: (r["direction"] != "in", -r["total_eqv"]))
    revenue = [r for r in rows if r["direction"] == "in"]
    costs = [r for r in rows if r["direction"] == "out"]

    # projected cash position, rolled forward from the same opening balance
    position = []
    opening = dict(fwd["rows"][0]["opening"]) if fwd["rows"] else bank_opening()
    prev = opening
    for j in range(ahead):
        closing = {c: prev[c] + proj_in[j][c] - proj_out[j][c] for c in CURRENCIES}
        position.append({
            "week": fweeks[j] if j < len(fweeks) else None,
            "opening": prev, "inflow": proj_in[j], "outflow": proj_out[j],
            "closing": closing,
            "closing_eqv": egp_equivalent(closing, rate),
            "booked_closing": future[j]["closing"] if j < len(future) else None,
            "booked_closing_eqv": future[j]["closing_eqv"] if j < len(future) else None,
        })
        prev = closing

    def tot(group, field):
        return sum((r[field] for r in group), Decimal("0"))

    return {
        "lookback": n, "ahead": ahead, "method": method, "fx": rate,
        "history_weeks": hist, "future_weeks": fweeks,
        "rows": rows, "revenue": revenue, "costs": costs,
        "totals": {
            "revenue_hist": tot(revenue, "total_eqv"),
            "cost_hist": tot(costs, "total_eqv"),
            "revenue_proj": tot(revenue, "projected_eqv"),
            "cost_proj": tot(costs, "projected_eqv"),
            "revenue_booked": tot(revenue, "booked_eqv"),
            "cost_booked": tot(costs, "booked_eqv"),
        },
        "position": position,
        "booked_rows": future,
    }


# ============================================================
# Cash position strip (last week, this week, coming weeks)
# ============================================================

def cash_position_strip(back=1, ahead=3, include_pending=None):
    """Cash position either side of today.

    Weeks before the current one are reconstructed backwards: this week's
    opening is the bank position on file, so last week's closing is the
    same figure, and its opening follows from its own movements.  That
    keeps the strip arithmetically consistent with the forward forecast
    rather than being a second, disagreeing calculation.
    """
    if include_pending is None:
        include_pending = setting_bool("include_pending")
    back = max(0, min(12, back))
    ahead = max(0, min(26, ahead))
    start = forecast_start() - timedelta(days=7 * back)
    weeks = build_weeks(n=back + 1 + ahead, start=start)
    moves = generate_movements(weeks, include_pending=True)
    overrides = opening_overrides()
    rate = fx_rate()
    buf = buffers()
    n = len(weeks)

    zero = lambda: {c: Decimal("0") for c in CURRENCIES}      # noqa: E731
    inflow = [zero() for _ in range(n)]
    outflow = [zero() for _ in range(n)]
    for m in moves:
        if m["pending"] and not include_pending:
            continue
        (inflow if m["direction"] == "in" else outflow)[m["week"]][m["currency"]] += m["amount"]

    cur = back                                   # index of the current week
    opening = [zero() for _ in range(n)]
    closing = [zero() for _ in range(n)]

    base = bank_opening()
    ov0 = overrides.get(weeks[cur]["week_id"], {})
    opening[cur] = {c: ov0.get(c, base[c]) for c in CURRENCIES}
    closing[cur] = {c: opening[cur][c] + inflow[cur][c] - outflow[cur][c] for c in CURRENCIES}

    for i in range(cur + 1, n):
        ov = overrides.get(weeks[i]["week_id"], {})
        opening[i] = {c: ov.get(c, closing[i - 1][c]) for c in CURRENCIES}
        closing[i] = {c: opening[i][c] + inflow[i][c] - outflow[i][c] for c in CURRENCIES}

    for i in range(cur - 1, -1, -1):             # backwards through history
        closing[i] = dict(opening[i + 1])
        opening[i] = {c: closing[i][c] - inflow[i][c] + outflow[i][c] for c in CURRENCIES}

    rows = []
    for i, w in enumerate(weeks):
        gap = {}
        for c in CURRENCIES:
            floor = buf[c]
            gap[c] = closing[i][c] - floor if closing[i][c] < floor else Decimal("0")
        rows.append({
            "week": w, "index": i, "offset": i - cur,
            "is_current": i == cur, "is_past": i < cur,
            "opening": opening[i], "inflow": inflow[i], "outflow": outflow[i],
            "closing": closing[i], "gap": gap,
            "closing_eqv": egp_equivalent(closing[i], rate),
            "inflow_eqv": egp_equivalent(inflow[i], rate),
            "outflow_eqv": egp_equivalent(outflow[i], rate),
            "gap_eqv": egp_equivalent(gap, rate),
            "short": any(closing[i][c] < 0 for c in CURRENCIES),
        })
    worst = min(rows, key=lambda r: r["closing_eqv"]) if rows else None
    return {"rows": rows, "current": rows[cur] if rows else None, "worst": worst,
            "fx": rate, "buffers": buf,
            "total_gap_eqv": sum((r["gap_eqv"] for r in rows), Decimal("0"))}


# ============================================================
# Data completeness — which tables are missing for which weeks
# ============================================================

def completeness(weeks=None, back=0):
    """Where the cash flow has holes.

    A table with entries in some weeks but none in a given week is the
    signal worth acting on: it usually means somebody has not yet entered
    their figures, not that the week is genuinely empty.  Recurring cost
    tables are judged on their generated occurrences, not their row count.
    """
    from streams import STREAMS, STREAM_ORDER
    weeks = weeks or build_weeks(n=setting_int("dashboard_weeks") or 6,
                                 start=forecast_start() - timedelta(days=7 * back))
    moves = generate_movements(weeks, include_pending=True)
    n = len(weeks)

    grid = {k: [0] * n for k in STREAM_ORDER}
    value = {k: [Decimal("0")] * n for k in STREAM_ORDER}
    for m in moves:
        grid[m["stream"]][m["week"]] += 1
        value[m["stream"]][m["week"]] += m["amount"]

    rows, alerts = [], []
    for key in STREAM_ORDER:
        spec = STREAMS[key]
        counts = grid[key]
        total = sum(counts)
        populated = sum(1 for c in counts if c)
        row = {
            "key": key, "spec": spec, "counts": counts, "values": value[key],
            "total": total, "populated": populated,
            "coverage": (Decimal(populated) / Decimal(n) * 100) if n else Decimal("0"),
            "never_used": total == 0,
            "gaps": [i for i, c in enumerate(counts) if c == 0] if total else [],
        }
        rows.append(row)
        if row["never_used"]:
            alerts.append({"level": "info", "stream": key, "weeks": [],
                           "key": "gap_never_used"})
        elif row["gaps"]:
            alerts.append({"level": "warning", "stream": key, "weeks": row["gaps"],
                           "key": "gap_missing_weeks"})

    empty_weeks = [i for i in range(n)
                   if not any(grid[k][i] for k in STREAM_ORDER)]
    for i in empty_weeks:
        alerts.append({"level": "danger", "stream": None, "weeks": [i],
                       "key": "gap_empty_week"})

    return {"weeks": weeks, "rows": rows, "alerts": alerts,
            "empty_weeks": empty_weeks,
            "gap_count": sum(len(r["gaps"]) for r in rows),
            "streams_never_used": [r["key"] for r in rows if r["never_used"]]}


def missing_for_role(role, week_index=0, weeks=None):
    """Tables this role is responsible for that have nothing in a week."""
    import permissions as perms
    comp = completeness(weeks=weeks)
    owned = set(perms.streams_for_role(role))
    out = []
    for r in comp["rows"]:
        if r["key"] not in owned:
            continue
        if week_index in r["gaps"] or r["never_used"]:
            out.append(r)
    return comp, out


# ============================================================
# Week sign-off
# ============================================================

def current_week_id():
    return forecast_start().isoformat()


def week_by_id(week_id, weeks=None):
    for w in (weeks or build_weeks()):
        if w["week_id"] == week_id:
            return w
    try:
        start = date.fromisoformat(week_id)
    except ValueError:
        return None
    return build_weeks(n=1, start=start)[0]


def get_signoff(week_id):
    from models import WeekSignoff
    return WeekSignoff.query.filter_by(week_id=week_id).first()


def confirm_week(week_id, who, role, notes=""):
    from models import db as _db, WeekSignoff
    row = get_signoff(week_id)
    if row is None:
        row = WeekSignoff(week_id=week_id)
        _db.session.add(row)
    row.status = "confirmed"
    row.confirmed_by = who
    row.confirmed_role = role
    row.confirmed_at = datetime.now()
    row.notes = (notes or "")[:1000]
    _db.session.commit()
    return row


def reopen_week(week_id):
    from models import db as _db
    row = get_signoff(week_id)
    if row:
        _db.session.delete(row)
        _db.session.commit()


# ============================================================
# Audit trail report
# ============================================================

def audit_report(user=None, stream=None, action=None, date_from=None,
                 date_to=None, limit=1000):
    from models import AuditLog
    q = AuditLog.query
    if user:
        q = q.filter(AuditLog.actor == user)
    if stream:
        q = q.filter(AuditLog.stream == stream)
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if date_from:
        q = q.filter(AuditLog.at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(AuditLog.at <= datetime.combine(date_to, datetime.max.time()))
    rows = q.order_by(AuditLog.id.desc()).limit(limit).all()

    by_user, by_action, by_stream = {}, {}, {}
    for r in rows:
        u = by_user.setdefault(r.actor or "system",
                               {"actor": r.actor or "system", "role": r.actor_role or "",
                                "created": 0, "updated": 0, "deleted": 0,
                                "approved": 0, "other": 0, "total": 0, "last": None})
        act = (r.action or "")
        if act.endswith("_created"):
            u["created"] += 1
        elif act.endswith("_updated"):
            u["updated"] += 1
        elif act.endswith("_deleted"):
            u["deleted"] += 1
        elif "approved" in act or "confirm" in act:
            u["approved"] += 1
        else:
            u["other"] += 1
        u["total"] += 1
        if u["last"] is None or (r.at and r.at > u["last"]):
            u["last"] = r.at
        by_action[act] = by_action.get(act, 0) + 1
        if r.stream:
            by_stream[r.stream] = by_stream.get(r.stream, 0) + 1

    people = sorted(by_user.values(), key=lambda x: -x["total"])
    return {"rows": rows, "people": people, "by_action": by_action,
            "by_stream": by_stream, "count": len(rows)}


def audit_actors():
    from models import AuditLog
    return sorted({r[0] for r in db.session.query(AuditLog.actor).distinct() if r[0]})


# ============================================================
# Audit
# ============================================================

def audit(actor, action, target="", detail="", stream=None, row_id=None, role=None):
    try:
        db.session.add(AuditLog(actor=actor or "system", actor_role=role,
                                action=action, stream=stream, row_id=row_id,
                                target=str(target)[:160], detail=str(detail)[:4000]))
        db.session.commit()
    except Exception:
        db.session.rollback()


def ensure_schema():
    """Additive-only column top-ups, mirroring Collecta's safe migration."""
    from sqlalchemy import text
    try:
        insp = inspect(db.engine)
        tables = set(insp.get_table_names())
        wanted = {
            "users": [("lang", "VARCHAR(2)")],
            "customer_collections": [("customer_no", "VARCHAR(40)"),
                                     ("collection_type", "VARCHAR(20)"),
                                     ("contract_ref", "VARCHAR(80)"),
                                     ("instalment_no", "INTEGER")],
            "customer_refunds": [("customer_no", "VARCHAR(40)")],
            "supplier_instalments": [("supplier_no", "VARCHAR(40)"),
                                     ("payment_type", "VARCHAR(20)"),
                                     ("contract_ref", "VARCHAR(80)")],
            "audit_log": [("actor_role", "VARCHAR(40)"), ("stream", "VARCHAR(40)"),
                          ("row_id", "INTEGER")],
        }
        for tbl, cols in wanted.items():
            if tbl not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(tbl)}
            for col, typ in cols:
                if col not in have:
                    db.session.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}"))
                    db.session.commit()
    except Exception:
        db.session.rollback()
