"""The two automated emails.

1. **Input request** — sent a few days before a new week opens, to the
   people who own the tables that have nothing in them yet.  Each person
   is told only about their own tables, so the message is a task, not a
   report.

2. **Weekly cash review** — sent to the Managing Director and CFO the
   moment the Account Manager confirms the week.  Confirmation is the
   trigger, so the leadership sees reviewed figures, never a work in
   progress.

Emails are built with inline styles: mail clients discard stylesheets.
"""
from datetime import date, timedelta

from models import db, NotificationLog
import emailer
import permissions as perms
import services as svc

BRAND_DEEP = "#1B5584"
INK = "#17232e"
MUTED = "#5d6b78"
LINE = "#dbe2e8"
NEG = "#b4342b"


# ============================================================
# Shell
# ============================================================

def _shell(title, intro, body, footer=""):
    brand = svc.get_brand()
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;color:{INK};
 background:#f4f6f8;padding:24px">
  <div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid {LINE};
   border-radius:8px;overflow:hidden">
    <div style="background:{BRAND_DEEP};color:#fff;padding:14px 20px">
      <div style="font-size:15px;font-weight:600">{brand['org']}</div>
      <div style="font-size:12px;opacity:.8">{brand['product']}</div>
    </div>
    <div style="padding:20px">
      <h2 style="margin:0 0 6px;font-size:17px;color:{BRAND_DEEP}">{title}</h2>
      <p style="margin:0 0 14px;color:{MUTED};font-size:13px">{intro}</p>
      {body}
    </div>
    <div style="padding:12px 20px;border-top:1px solid {LINE};color:{MUTED};
     font-size:11px">{footer or 'Sent automatically by the cash flow system.'}</div>
  </div>
</div>"""


def _money(v, dp=0):
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "–"


def _num(v, currency=""):
    """A figure, red when negative — the rule holds in email too."""
    try:
        neg = float(v) < 0
    except (TypeError, ValueError):
        neg = False
    colour = NEG if neg else INK
    weight = "700" if neg else "600"
    return (f'<span style="color:{colour};font-weight:{weight}">'
            f'{_money(v)} {currency}</span>')


def _table(headers, rows):
    head = "".join(
        f'<th style="text-align:left;padding:6px 8px;border-bottom:2px solid {BRAND_DEEP};'
        f'font-size:11px;text-transform:uppercase;color:{MUTED}">{h}</th>' for h in headers)
    body = ""
    for r in rows:
        cells = "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid {LINE};font-size:13px">'
            f'{c}</td>' for c in r)
        body += f"<tr>{cells}</tr>"
    return (f'<table style="width:100%;border-collapse:collapse;margin:6px 0 14px">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


# ============================================================
# 1. Input request
# ============================================================

def build_input_request(user, week, missing_rows, lang="en"):
    """The request email for one person, naming only their own tables."""
    brand = svc.get_brand()
    if missing_rows:
        rows = [[r["spec"]["en"],
                 '<span style="color:#b4342b;font-weight:600">Nothing entered</span>']
                for r in missing_rows]
        body = _table(["Table", "Status"], rows)
        ask = ("Please enter the figures you hold for these tables before the week "
               "opens, so the forecast is complete when it is reviewed.")
    else:
        body = ('<p style="font-size:13px;margin:0 0 14px">Your tables all carry '
                'entries for this week. Please check the amounts and dates are still '
                'right, and update anything that has changed.</p>')
        ask = "This is the routine check before the week opens."

    return _shell(
        title=f"Cash flow input needed — week commencing {week['start'].strftime('%d %B %Y')}",
        intro=(f"{user.display}, the week of "
               f"{week['start'].strftime('%d %b')} – {week['end'].strftime('%d %b %Y')} "
               f"opens shortly. {ask}"),
        body=body,
        footer=f"{brand['org']} · you receive this because you maintain these tables.")


def send_input_requests(week=None, triggered_by="scheduler", force=False):
    """Email everyone whose tables have gaps in the coming week.

    Returns a summary dict. Nobody is emailed twice for the same week
    unless `force` is set, so the scheduler can run as often as it likes.
    """
    weeks = svc.build_weeks()
    week = week or (weeks[0] if weeks else None)
    if week is None:
        return {"sent": 0, "skipped": 0, "results": [], "reason": "no weeks"}
    week_id = week["week_id"]
    idx = next((i for i, w in enumerate(weeks) if w["week_id"] == week_id), 0)

    results, sent, skipped = [], 0, 0
    for role in perms.get_request_roles():
        comp, missing = svc.missing_for_role(role, week_index=idx, weeks=weeks)
        for user in perms.users_for_roles([role]):
            if not user.email:
                results.append({"user": user.display, "ok": False,
                                "message": "No email address on file"})
                skipped += 1
                continue
            if not force and _already_sent("input_request", week_id, user.email):
                skipped += 1
                continue
            html = build_input_request(user, week, missing)
            subject = (f"Action needed — cash flow entries for week commencing "
                       f"{week['start'].strftime('%d %b %Y')}")
            ok, message = emailer.send_and_log(user.email, subject, html,
                                               kind="input_request", week_id=week_id,
                                               triggered_by=triggered_by)
            results.append({"user": user.display, "email": user.email, "ok": ok,
                            "message": message, "missing": len(missing)})
            sent += 1 if ok else 0
    return {"sent": sent, "skipped": skipped, "results": results, "week": week}


# ============================================================
# 2. Weekly cash review
# ============================================================

def build_weekly_review(week, strip, signoff, lang="en"):
    brand = svc.get_brand()
    fx = strip["fx"]
    cur = strip["current"]
    rows = []
    for r in strip["rows"]:
        when = ("Last week" if r["is_past"] else
                "This week" if r["is_current"] else
                f"+{r['offset']} week" + ("s" if r["offset"] > 1 else ""))
        rows.append([
            f"<b>{r['week']['start'].strftime('%d %b')}</b> "
            f"<span style='color:{MUTED}'>{when}</span>",
            _num(r["inflow_eqv"]), _num(r["outflow_eqv"]), _num(r["closing_eqv"]),
            _num(r["gap_eqv"]) if r["gap_eqv"] else "—",
        ])
    table = _table(["Week", "Cash in", "Cash out", "Closing (≡EGP)", "Shortfall"], rows)

    headline = (f'<div style="background:#f4f6f8;border:1px solid {LINE};border-radius:6px;'
                f'padding:14px 16px;margin:0 0 14px">'
                f'<div style="font-size:11px;text-transform:uppercase;color:{MUTED};'
                f'letter-spacing:.6px">Cash position this week</div>'
                f'<div style="font-size:26px;font-weight:700;margin-top:4px;'
                f'color:{NEG if cur and cur["closing_eqv"] < 0 else INK}">'
                f'{_money(cur["closing_eqv"]) if cur else "–"} EGP</div>'
                f'<div style="font-size:12px;color:{MUTED};margin-top:3px">'
                f'EGP {_money(cur["closing"]["EGP"]) if cur else "–"} · '
                f'USD {_money(cur["closing"]["USD"]) if cur else "–"} '
                f'· rate {_money(fx, 2)}</div></div>')

    shortfalls = [r for r in strip["rows"] if r["gap_eqv"] < 0]
    warn = ""
    if shortfalls:
        first = shortfalls[0]
        warn = (f'<div style="background:#fdecea;border:1px solid #f0b6b1;'
                f'border-radius:6px;padding:12px 14px;margin:0 0 14px;font-size:13px">'
                f'<b style="color:{NEG}">Shortfall forecast</b><br>'
                f'Week commencing {first["week"]["start"].strftime("%d %b %Y")} is '
                f'{_money(abs(first["gap_eqv"]))} EGP short of the minimum cash buffer.'
                f'</div>')

    return _shell(
        title=f"Weekly cash review — week commencing {week['start'].strftime('%d %B %Y')}",
        intro=(f"Reviewed and confirmed by {signoff.confirmed_by} on "
               f"{signoff.confirmed_at.strftime('%d %b %Y at %H:%M')}."),
        body=headline + warn + table +
             (f'<p style="font-size:12px;color:{MUTED};margin:0">'
              f'{signoff.notes}</p>' if signoff.notes else ""),
        footer=f"{brand['org']} · sent on confirmation of the weekly cash flow.")


def send_weekly_review(week_id, triggered_by="", force=False):
    """Send the confirmed week's review to the MD, CFO and anyone else listed."""
    signoff = svc.get_signoff(week_id)
    if signoff is None:
        return {"sent": 0, "results": [], "reason": "week not confirmed"}
    if signoff.review_sent_at and not force:
        return {"sent": 0, "results": [], "reason": "already sent"}

    week = svc.week_by_id(week_id)
    strip = svc.cash_position_strip(back=1, ahead=3)
    html = build_weekly_review(week, strip, signoff)
    subject = (f"Weekly cash review — week commencing "
               f"{week['start'].strftime('%d %b %Y')}")

    results, sent, addresses = [], 0, []
    for user in perms.users_for_roles(perms.get_review_roles()):
        if not user.email:
            results.append({"user": user.display, "ok": False,
                            "message": "No email address on file"})
            continue
        ok, message = emailer.send_and_log(user.email, subject, html,
                                           kind="weekly_review", week_id=week_id,
                                           triggered_by=triggered_by)
        results.append({"user": user.display, "email": user.email, "ok": ok,
                        "message": message})
        if ok:
            sent += 1
            addresses.append(user.email)

    if sent:
        signoff.review_sent_at = _now()
        signoff.review_sent_to = ", ".join(addresses)[:600]
        db.session.commit()
    return {"sent": sent, "results": results, "week": week}


# ============================================================
# Scheduling
# ============================================================

def due_week_for_requests():
    """The week an input request is currently due for, if any.

    Requests go out `notify_days_before` days ahead of a week opening.
    """
    days = svc.setting_int("notify_days_before") or 3
    weeks = svc.build_weeks(n=3)
    today = date.today()
    for w in weeks:
        if 0 <= (w["start"] - today).days <= days:
            return w
    return None


def run_scheduled(triggered_by="scheduler", force_week=None):
    """One scheduled pass: send input requests when a week is approaching.

    Safe to call repeatedly — recipients already emailed for a week are
    skipped, so a cron running hourly sends exactly one round.
    """
    if not emailer.configured():
        return {"ran": False, "reason": "email not configured"}
    week = force_week or due_week_for_requests()
    if week is None:
        return {"ran": False, "reason": "no week due for a request today"}
    result = send_input_requests(week=week, triggered_by=triggered_by)
    result["ran"] = True
    return result


def _already_sent(kind, week_id, recipient):
    return db.session.query(NotificationLog.id).filter_by(
        kind=kind, week_id=week_id, recipient=recipient, ok=True).first() is not None


def _now():
    from datetime import datetime
    return datetime.now()


def recent_log(limit=200):
    return NotificationLog.query.order_by(NotificationLog.id.desc()).limit(limit).all()
