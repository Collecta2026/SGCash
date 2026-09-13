"""Scientific Gate — Cash Flow Budgeting System (Flask / Postgres).

Routes only: the arithmetic lives in `services.py`, the delegation rules
in `permissions.py`, the drawing in `charts.py` and the emails in
`notifications.py`.
"""
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort, session, jsonify, Response)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from sqlalchemy import or_

from models import (db, D, User, get_setting, set_setting, Approval, AuditLog,
                    RevenueType, CostCategory, BankAccount, WeekSignoff,
                    NotificationLog, CURRENCIES, STATUSES, ST_DRAFT, ST_SUBMITTED,
                    ST_APPROVED, ST_REJECTED, ST_SETTLED)
import services as svc
import permissions as perms
import charts
import i18n
from streams import (STREAMS, STREAM_ORDER, INFLOW_STREAMS, OUTFLOW_STREAMS,
                     WEEKDAYS, stream as get_stream, field_label)


# ============================================================
# Report registry
# ============================================================
REPORTS = [
    ("cashflow", "Cash Flow Position", "الموقف النقدي", "reports"),
    ("in_out", "Money In and Out", "المتحصلات والمدفوعات", "reports"),
    ("revenue", "Revenue Analysis", "تحليل الإيرادات", "reports"),
    ("costs", "Cost Elements", "عناصر التكلفة", "reports"),
    ("collections", "Collections Schedule", "جدول التحصيلات", "reports"),
    ("payables", "Payables Schedule", "جدول المدفوعات", "reports"),
    ("shortfall", "Shortfall & Funding Gap", "العجز والفجوة التمويلية", "reports"),
    ("completeness", "Data Completeness", "اكتمال البيانات", "audit_report"),
    ("audit", "Audit Trail", "سجل المراجعة", "audit_report"),
    ("imports", "Upload Data Files", "رفع ملفات البيانات", "imports"),
]
REPORT_CAP = {r[0]: r[3] for r in REPORTS}


# ============================================================
# Form parsing
# ============================================================

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_money(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return D(str(s).replace(",", "").replace("٬", "").strip())
    except (InvalidOperation, ValueError):
        return None


def parse_int(s, lo=None, hi=None):
    try:
        v = int(str(s).strip())
    except (TypeError, ValueError):
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return v


def read_field(f, form):
    raw = form.get(f["name"], "")
    ft, req = f["type"], f.get("required")

    if ft in ("text", "textarea"):
        v = (raw or "").strip() or None
        return (v, "required_field" if req and not v else None)
    if ft == "money":
        v = parse_money(raw)
        if v is None:
            return (None, "required_field" if req else None)
        if v < 0:
            return (None, "negative_amount")
        return (svc.q2(v), None)
    if ft == "date":
        v = parse_date(raw)
        return (v, "required_field" if req and v is None else None)
    if ft == "int":
        v = parse_int(raw, f.get("min"), f.get("max"))
        return (v, "required_field" if req and v is None else None)
    if ft == "pct":
        v = parse_int(raw, 0, 100)
        return (100 if v is None else v, None)
    if ft == "weekday":
        v = parse_int(raw, 0, 6)
        return (v, "required_field" if req and v is None else None)
    if ft == "currency":
        v = (raw or "EGP").upper()
        return (v if v in CURRENCIES else "EGP", None)
    if ft == "direction":
        return ("in" if (raw or "out").lower() == "in" else "out", None)
    if ft == "select":
        opts = [o[0] for o in f.get("options", [])]
        v = (raw or "").strip()
        if v not in opts:
            v = opts[0] if opts else None
        return (v, None)
    if ft in ("revenue_type", "cost_category"):
        v = parse_int(raw)
        return (v if v else None, None)
    return ((raw or "").strip() or None, None)


def apply_form(spec, row, form):
    errors = {}
    for f in spec["fields"]:
        v, err = read_field(f, form)
        if err:
            errors[f["name"]] = err
            continue
        setattr(row, f["name"], v)
    if getattr(row, "amount", None) in (None, "", 0) and "amount" not in errors:
        errors["amount"] = "required_field"
    return errors


# ============================================================
# App factory
# ============================================================

def create_app(config=None):
    base = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__,
                template_folder=os.path.join(base, "templates"),
                static_folder=os.path.join(base, "static"))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

    uri = os.environ.get("DATABASE_URL", "sqlite:///sgcash.db")
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql+psycopg://", 1)
    elif uri.startswith("postgresql://") and "+psycopg" not in uri:
        uri = uri.replace("postgresql://", "postgresql+psycopg://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    eng = {"pool_pre_ping": True}
    if uri.startswith("postgresql+psycopg://"):
        eng["pool_recycle"] = 300
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = eng
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    if os.environ.get("RENDER") or os.environ.get("FORCE_HTTPS") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config.update(SESSION_COOKIE_SECURE=True, PREFERRED_URL_SCHEME="https")
    if app.config["SECRET_KEY"] == "change-me-in-production":
        import logging
        logging.getLogger(__name__).warning(
            "SECRET_KEY is unset - sessions are not secure. Set SECRET_KEY before going live.")

    if config:
        app.config.update(config)

    db.init_app(app)
    with app.app_context():
        try:
            db.create_all()
            svc.ensure_schema()
            perms.seed_matrix()
        except Exception:
            db.session.rollback()

    login = LoginManager(app)
    login.login_view = "login"

    @login.user_loader
    def load_user(uid):
        try:
            return db.session.get(User, int(uid))
        except (TypeError, ValueError):
            return None

    # ---------------- Jinja helpers ----------------
    @app.template_filter("money")
    def _money(v, ccy=""):
        try:
            s = f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return "–"
        return f"{s} {ccy}".strip()

    @app.template_filter("money0")
    def _money0(v, ccy=""):
        try:
            s = f"{float(v):,.0f}"
        except (TypeError, ValueError):
            return "–"
        return f"{s} {ccy}".strip()

    @app.template_filter("neg")
    def _neg(v):
        """CSS class for a figure: negatives are always red."""
        try:
            return "neg" if float(v) < 0 else ""
        except (TypeError, ValueError):
            return ""

    @app.template_filter("pct")
    def _pct(v):
        if v is None:
            return "–"
        try:
            return f"{float(v):,.1f}%"
        except (TypeError, ValueError):
            return "–"

    @app.template_filter("d")
    def _d(v):
        if not v:
            return ""
        try:
            return v.strftime("%d %b %Y")
        except AttributeError:
            return str(v)

    @app.template_filter("dt")
    def _dt(v):
        if not v:
            return ""
        try:
            return v.strftime("%d %b %Y %H:%M")
        except AttributeError:
            return str(v)

    app.jinja_env.globals.update(
        t=i18n.t, lang=i18n.lang, direction=i18n.direction, LANGS=i18n.LANGS,
        today=date.today, STREAMS=STREAMS, STREAM_ORDER=STREAM_ORDER,
        INFLOW_STREAMS=INFLOW_STREAMS, OUTFLOW_STREAMS=OUTFLOW_STREAMS,
        WEEKDAYS=WEEKDAYS, CURRENCIES=CURRENCIES, STATUSES=STATUSES,
        ACTIONS=perms.ACTIONS, ROLES=perms.ROLES, REPORTS=REPORTS,
        charts=charts,
        field_label=lambda f: field_label(f, i18n.lang()),
        stream_label=lambda k: (STREAMS[k]["ar"] if i18n.lang() == "ar" else STREAMS[k]["en"]),
        report_label=lambda r: (r[2] if i18n.lang() == "ar" else r[1]),
        opt_label=lambda o: (o[2] if i18n.lang() == "ar" and len(o) > 2 else o[1]),
    )

    @app.context_processor
    def _inject():
        auth = current_user.is_authenticated
        return dict(
            brand=svc.get_brand(), app_version=svc.VERSION, fx=svc.fx_rate(),
            can=(lambda c: perms.has_perm(current_user, c)) if auth else (lambda c: False),
            can_s=(lambda s, a: perms.can(current_user, s, a)) if auth else (lambda s, a: False),
            why=(lambda c: perms.dim_reason(current_user, c, i18n.lang())) if auth else (lambda c: ""),
            why_s=(lambda s, a: perms.dim_reason(current_user, perms.stream_cap(s, a),
                                                 i18n.lang())) if auth else (lambda s, a: ""),
            is_admin=(perms.is_admin(current_user) if auth else False),
            my_streams=(perms.visible_streams(current_user) if auth else []),
            my_caps=(perms.visible_caps(current_user, perms.CAP_KEYS) if auth else []),
            is_super=(perms.is_super(current_user) if auth else False),
            can_approve_any=(perms.can_approve_any(current_user) if auth else False),
            ROLE_LABEL=(perms.ROLE_LABELS_AR if i18n.lang() == "ar" else perms.ROLE_LABELS),
        )

    def _needs_setup():
        try:
            return User.query.first() is None
        except Exception:
            return False

    @app.before_request
    def _guards():
        ep = request.endpoint or ""
        if ep in perms.OPEN_ENDPOINTS or ep.startswith("static"):
            return
        if _needs_setup():
            return redirect(url_for("setup"))
        cap = ENDPOINT_CAP.get(ep)
        if cap and current_user.is_authenticated and not perms.has_perm(current_user, cap):
            abort(403)

    def who():
        return current_user.display if current_user.is_authenticated else "system"

    def role():
        return perms.role_key(current_user) if current_user.is_authenticated else ""

    def audit(action, target="", detail="", stream=None, row_id=None):
        svc.audit(who(), action, target, detail, stream=stream, row_id=row_id, role=role())

    def require(cap):
        def deco(f):
            @wraps(f)
            def w(*a, **k):
                if not perms.has_perm(current_user, cap):
                    abort(403)
                return f(*a, **k)
            return w
        return deco

    def admin_only(f):
        @wraps(f)
        def w(*a, **k):
            if not perms.is_admin(current_user):
                abort(403)
            return f(*a, **k)
        return w

    # =========================================================
    # Setup / auth / language / branding
    # =========================================================
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if User.query.first() is not None:
            return redirect(url_for("login"))
        if request.method == "POST":
            u = (request.form.get("username") or "admin").strip() or "admin"
            p = request.form.get("password") or ""
            if len(p) < 6:
                flash("Choose a password of at least 6 characters.", "danger")
                return render_template("setup.html")
            import seed
            seed.initialise(u, p, org=request.form.get("org_name") or svc.DEFAULT_ORG,
                            lang=request.form.get("lang") or "en",
                            demo=request.form.get("demo") == "1")
            flash("Setup complete — please sign in.", "success")
            return redirect(url_for("login"))
        return render_template("setup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = User.query.filter_by(username=(request.form.get("username") or "").strip()).first()
            if u and u.active and u.check_password(request.form.get("password")):
                login_user(u)
                if u.lang:
                    i18n.set_lang(u.lang)
                svc.audit(u.display, "login", u.username, role=u.role)
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Invalid username or password.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        audit("logout", current_user.username)
        logout_user()
        return redirect(url_for("login"))

    @app.route("/lang/<code>")
    def set_lang(code):
        i18n.set_lang(code)
        if current_user.is_authenticated and code in i18n.LANG_KEYS:
            current_user.lang = code
            db.session.commit()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/brand/logo")
    def brand_logo():
        blob = svc.get_logo()
        if not blob:
            return redirect(url_for("static", filename="logo.png"))
        data, mime = blob
        return Response(data, mimetype=mime or "image/png",
                        headers={"Cache-Control": "no-cache"})

    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok", version=svc.VERSION)

    # =========================================================
    # Dashboard & forecast
    # =========================================================
    @app.route("/")
    @login_required
    def dashboard():
        # A role with no dashboard capability (data entry only) never sees the
        # cash position — it lands on the table it maintains instead.
        if not perms.has_perm(current_user, "dashboard"):
            landing = perms.landing_stream(current_user)
            if landing:
                return redirect(url_for("table_list", key=landing))
            abort(403)
        n = parse_int(request.args.get("weeks")) or svc.setting_int("dashboard_weeks")
        n = max(1, min(26, n))
        dash = svc.dashboard(n)
        strip = svc.cash_position_strip(back=1, ahead=3)
        comp = svc.completeness()
        health = svc.cash_health(n)
        signoff = svc.get_signoff(svc.current_week_id())
        pending = _pending_count()
        return render_template("dashboard.html", dash=dash, n=n, strip=strip,
                               comp=comp, health=health, signoff=signoff,
                               pending=pending)

    @app.route("/forecast")
    @login_required
    @require("forecast")
    def forecast():
        f = svc.build_forecast()
        return render_template("forecast.html", f=f)

    @app.route("/forecast/week/<week_id>")
    @login_required
    @require("forecast")
    def week_detail(week_id):
        f = svc.build_forecast()
        idx = next((i for i, r in enumerate(f["rows"]) if r["week"]["week_id"] == week_id), None)
        if idx is None:
            abort(404)
        return render_template("week_detail.html", f=f, row=f["rows"][idx],
                               moves=svc.movements_for_week(f, idx),
                               signoff=svc.get_signoff(week_id))

    @app.route("/trend")
    @login_required
    @require("trend")
    def trend():
        lb = parse_int(request.args.get("lookback")) or svc.setting_int("trend_lookback")
        ah = parse_int(request.args.get("ahead")) or svc.setting_int("trend_project")
        method = request.args.get("method") or svc.setting("trend_method")
        if method not in ("linear", "average"):
            method = "linear"
        tf = svc.trend_forecast(lookback=max(2, min(52, lb)),
                                ahead=max(1, min(52, ah)), method=method)
        return render_template("trend.html", tf=tf, lb=lb, ah=ah, method=method)

    # =========================================================
    # Week sign-off — releases the weekly review to the MD and CFO
    # =========================================================
    @app.route("/week/<week_id>/confirm", methods=["POST"])
    @login_required
    @require("week_signoff")
    def week_confirm(week_id):
        import notifications
        so = svc.confirm_week(week_id, who(), role(), request.form.get("notes", ""))
        audit("week_confirmed", week_id, so.notes or "")
        result = notifications.send_weekly_review(week_id, triggered_by=who())
        if result["sent"]:
            flash(f"Week confirmed. Cash review sent to {result['sent']} recipient(s).",
                  "success")
        else:
            flash(f"Week confirmed. Review not sent — {result.get('reason') or 'no recipients with an email address'}.",
                  "warning")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/week/<week_id>/reopen", methods=["POST"])
    @login_required
    @require("week_signoff")
    def week_reopen(week_id):
        svc.reopen_week(week_id)
        audit("week_reopened", week_id)
        flash("Week reopened for editing.", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/tasks/notices", methods=["GET", "POST"])
    def run_notices():
        """Token-protected endpoint for a scheduler to hit."""
        import notifications
        token = svc.setting("notices_token", "")
        given = request.args.get("token") or request.headers.get("X-Notices-Token")
        if not token or given != token:
            abort(403)
        return jsonify(notifications.run_scheduled(triggered_by="scheduler"))

    # =========================================================
    # The editable tables
    # =========================================================
    @app.route("/table/<key>")
    @login_required
    def table_list(key):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "view"):
            abort(403)
        model = spec["model"]
        q = model.query
        status = request.args.get("status") or ""
        ccy = (request.args.get("ccy") or "").upper()
        term = (request.args.get("q") or "").strip()
        if status in STATUSES:
            q = q.filter(model.status == status)
        if ccy in CURRENCIES:
            q = q.filter(model.currency == ccy)
        if term:
            like = f"%{term}%"
            cols = [getattr(model, f["name"]) for f in spec["fields"]
                    if f["type"] in ("text", "textarea") and hasattr(model, f["name"])]
            cols += [model.ref, model.description, model.notes]
            q = q.filter(or_(*[c.ilike(like) for c in cols if c is not None]))
        order = model.due_date.asc() if hasattr(model, "due_date") else model.id.desc()
        rows = q.order_by(order, model.id.asc()).all()
        totals = {c: sum((D(r.amount) for r in rows if r.currency == c), Decimal("0"))
                  for c in CURRENCIES}
        return render_template("table_list.html", key=key, spec=spec, rows=rows,
                               totals=totals, status=status, ccy=ccy, term=term,
                               refs=_reference_lists())

    @app.route("/table/<key>/weekly")
    @login_required
    def table_weekly(key):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "view"):
            abort(403)
        if not spec.get("weekly_planner"):
            return redirect(url_for("table_list", key=key))
        n = parse_int(request.args.get("weeks")) or svc.setting_int("horizon_weeks")
        weeks = svc.build_weeks(max(1, min(104, n)))
        plan = svc.weekly_stream(key, weeks)
        return render_template("table_weekly.html", key=key, spec=spec, plan=plan,
                               groups=spec.get("group_options") or [],
                               refs=_reference_lists())

    @app.route("/table/<key>/new", methods=["GET", "POST"])
    @login_required
    def table_new(key):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "enter"):
            abort(403)
        row = spec["model"]()
        errors = {}
        if request.method == "POST":
            errors = apply_form(spec, row, request.form)
            if not errors:
                row.status = ST_DRAFT
                row.created_by = who()
                row.updated_by = who()
                db.session.add(row)
                db.session.commit()
                audit(f"{key}_created", row.id, _summary(key, row), stream=key, row_id=row.id)
                if request.form.get("and_submit") == "1":
                    _submit_row(key, row)
                flash(i18n.t("saved"), "success")
                return redirect(url_for("table_list", key=key))
        return render_template("table_form.html", key=key, spec=spec, row=row,
                               errors=errors, refs=_reference_lists(), mode="new",
                               editable=True)

    @app.route("/table/<key>/<int:rid>/edit", methods=["GET", "POST"])
    @login_required
    def table_edit(key, rid):
        spec = _spec_or_404(key)
        row = db.session.get(spec["model"], rid) or abort(404)
        if not perms.can(current_user, key, "view"):
            abort(403)
        editable = perms.can(current_user, key, "edit")
        errors = {}
        if request.method == "POST":
            if not editable:
                abort(403)
            if row.status == ST_APPROVED and not perms.can(current_user, key, "approve"):
                flash("Approved entries can only be changed by an approver.", "danger")
                return redirect(url_for("table_list", key=key))
            before = _summary(key, row)
            errors = apply_form(spec, row, request.form)
            if not errors:
                row.updated_by = who()
                if row.status in (ST_APPROVED, ST_REJECTED):
                    row.status = ST_DRAFT
                    row.approved_by = None
                    row.approved_at = None
                db.session.commit()
                audit(f"{key}_updated", row.id, f"{before} → {_summary(key, row)}",
                      stream=key, row_id=row.id)
                flash(i18n.t("saved"), "success")
                return redirect(url_for("table_list", key=key))
        return render_template("table_form.html", key=key, spec=spec, row=row,
                               errors=errors, refs=_reference_lists(), mode="edit",
                               editable=editable)

    @app.route("/table/<key>/<int:rid>/delete", methods=["POST"])
    @login_required
    def table_delete(key, rid):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "delete"):
            abort(403)
        row = db.session.get(spec["model"], rid) or abort(404)
        detail = _summary(key, row)
        Approval.query.filter_by(stream=key, row_id=rid, status="pending").delete()
        db.session.delete(row)
        db.session.commit()
        audit(f"{key}_deleted", rid, detail, stream=key, row_id=rid)
        flash(i18n.t("delete") + " ✓", "success")
        return redirect(request.referrer or url_for("table_list", key=key))

    @app.route("/table/<key>/<int:rid>/submit", methods=["POST"])
    @login_required
    def table_submit(key, rid):
        spec = _spec_or_404(key)
        if not (perms.can(current_user, key, "enter") or perms.can(current_user, key, "edit")):
            abort(403)
        row = db.session.get(spec["model"], rid) or abort(404)
        _submit_row(key, row)
        flash(i18n.t("submit_for_approval") + " ✓", "success")
        return redirect(request.referrer or url_for("table_list", key=key))

    def _submit_row(key, row):
        row.status = ST_SUBMITTED
        row.reviewed_by = None
        row.reviewed_at = None
        db.session.commit()
        if not Approval.query.filter_by(stream=key, row_id=row.id, status="pending").first():
            db.session.add(Approval(
                stream=key, row_id=row.id, kind="entry", summary=_summary(key, row),
                currency=row.currency, amount=row.amount, requester=who(),
                requester_id=(current_user.id if current_user.is_authenticated else None)))
            db.session.commit()
        audit(f"{key}_submitted", row.id, _summary(key, row), stream=key, row_id=row.id)

    @app.route("/table/<key>/<int:rid>/decide/<decision>", methods=["POST"])
    @login_required
    def table_decide(key, rid, decision):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "approve") or not perms.can_approve_any(current_user):
            abort(403)
        if decision not in ("approve", "reject"):
            abort(400)
        row = db.session.get(spec["model"], rid) or abort(404)
        ap = Approval.query.filter_by(stream=key, row_id=rid, status="pending").first()
        mine = (ap and ap.requester_id == getattr(current_user, "id", None)) or \
               (row.created_by == who())
        if mine and not perms.is_admin(current_user):
            flash(i18n.t("no_self_approve"), "danger")
            return redirect(request.referrer or url_for("table_list", key=key))
        row.status = ST_APPROVED if decision == "approve" else ST_REJECTED
        row.reviewed_by = who()
        row.reviewed_at = datetime.now()
        if decision == "approve":
            row.approved_by = who()
            row.approved_at = datetime.now()
        if ap:
            ap.status = "approved" if decision == "approve" else "rejected"
            ap.approver = who()
            ap.decided_at = datetime.now()
            ap.reason = request.form.get("reason")
        db.session.commit()
        audit(f"{key}_{row.status}", rid, _summary(key, row), stream=key, row_id=rid)
        flash(i18n.t("approve" if decision == "approve" else "reject") + " ✓", "success")
        return redirect(request.referrer or url_for("table_list", key=key))

    @app.route("/table/<key>/<int:rid>/settle", methods=["POST"])
    @login_required
    def table_settle(key, rid):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "edit"):
            abort(403)
        row = db.session.get(spec["model"], rid) or abort(404)
        row.status = ST_SETTLED if row.status != ST_SETTLED else ST_APPROVED
        row.updated_by = who()
        db.session.commit()
        audit(f"{key}_{row.status}", rid, _summary(key, row), stream=key, row_id=rid)
        return redirect(request.referrer or url_for("table_list", key=key))

    # =========================================================
    # Banks & opening balances
    # =========================================================
    @app.route("/banks", methods=["GET", "POST"])
    @login_required
    @require("banks_view")
    def banks():
        if request.method == "POST":
            if not perms.has_perm(current_user, "banks_edit"):
                abort(403)
            rid = parse_int(request.form.get("id"))
            a = db.session.get(BankAccount, rid) if rid else BankAccount()
            if a is None:
                abort(404)
            a.name = (request.form.get("name") or "").strip()
            if not a.name:
                flash(i18n.t("required_field"), "danger")
                return redirect(url_for("banks"))
            a.bank = (request.form.get("bank") or "").strip() or None
            a.account_no = (request.form.get("account_no") or "").strip() or None
            ccy = (request.form.get("currency") or "EGP").upper()
            a.currency = ccy if ccy in CURRENCIES else "EGP"
            a.balance = svc.q2(parse_money(request.form.get("balance")) or 0)
            a.overdraft_limit = svc.q2(parse_money(request.form.get("overdraft_limit")) or 0)
            a.as_at = parse_date(request.form.get("as_at")) or date.today()
            a.include_in_forecast = request.form.get("include_in_forecast") == "1"
            a.notes = (request.form.get("notes") or "").strip() or None
            a.updated_by = who()
            if not rid:
                db.session.add(a)
            db.session.commit()
            audit("bank_saved", a.id, f"{a.name} {a.currency} {a.balance}")
            flash(i18n.t("saved"), "success")
            return redirect(url_for("banks"))
        rows = BankAccount.query.order_by(BankAccount.currency, BankAccount.name).all()
        tot = svc.bank_opening()
        return render_template("banks.html", rows=rows, tot=tot,
                               eqv=svc.egp_equivalent(tot))

    @app.route("/banks/<int:rid>/delete", methods=["POST"])
    @login_required
    @require("banks_edit")
    def bank_delete(rid):
        a = db.session.get(BankAccount, rid) or abort(404)
        name = a.name
        db.session.delete(a)
        db.session.commit()
        audit("bank_deleted", rid, name)
        return redirect(url_for("banks"))

    @app.route("/opening", methods=["GET", "POST"])
    @login_required
    @require("forecast")
    def opening():
        if request.method == "POST":
            if not perms.has_perm(current_user, "opening_edit"):
                abort(403)
            wid = request.form.get("week_id")
            ccy = (request.form.get("currency") or "").upper()
            if ccy not in CURRENCIES or not wid:
                abort(400)
            if request.form.get("clear") == "1":
                svc.clear_opening_override(wid, ccy)
                audit("opening_cleared", f"{wid}/{ccy}")
            else:
                v = parse_money(request.form.get("opening"))
                if v is None:
                    flash(i18n.t("required_field"), "danger")
                    return redirect(url_for("opening"))
                svc.set_opening_override(wid, ccy, v, who(), request.form.get("reason") or "")
                audit("opening_set", f"{wid}/{ccy}", str(v))
            flash(i18n.t("saved"), "success")
            return redirect(url_for("opening"))
        return render_template("opening.html", f=svc.build_forecast())

    # =========================================================
    # Reports
    # =========================================================
    @app.route("/reports")
    @login_required
    @require("reports")
    def reports():
        name = request.args.get("report") or "cashflow"
        if name not in REPORT_CAP:
            name = "cashflow"
        cap = REPORT_CAP[name]
        if not perms.has_perm(current_user, cap):
            abort(403)
        ctx = _report_context(name)
        return render_template("reports.html", report=name, printable=(request.args.get("print") == "1"),
                               **ctx)

    def _report_context(name):
        weeks_n = parse_int(request.args.get("weeks")) or 13
        weeks_n = max(2, min(52, weeks_n))
        ctx = {"weeks_n": weeks_n}

        if name in ("cashflow", "in_out", "shortfall"):
            back = parse_int(request.args.get("back"))
            back = 4 if back is None else max(0, min(12, back))
            strip = svc.cash_position_strip(back=back, ahead=weeks_n)
            ctx["strip"] = strip
            pts = [(r["week"]["short"], r["closing_eqv"]) for r in strip["rows"]]
            io_pts = [(r["week"]["short"], r["inflow_eqv"], r["outflow_eqv"])
                      for r in strip["rows"]]
            cut = next((i for i, r in enumerate(strip["rows"]) if r["is_current"]), 0)
            buffer_eqv = svc.egp_equivalent(strip["buffers"], strip["fx"])
            ctx["chart_balance"] = charts.cash_balance(
                pts, threshold=float(buffer_eqv), projected_from=cut,
                title="Cash position by week (EGP equivalent)", currency="EGP")
            ctx["chart_in_out"] = charts.money_in_out(
                io_pts, projected_from=cut,
                title="Money in and out by week (EGP equivalent)", currency="EGP")
            ctx["back"] = back
            if name == "shortfall":
                ctx["gaps"] = [r for r in strip["rows"] if r["gap_eqv"] < 0]
                ctx["chart_gap"] = charts.ranked_bars(
                    [(r["week"]["label"], r["gap_eqv"]) for r in ctx["gaps"]],
                    title="Forecast shortfall by week", currency="EGP")

        if name in ("revenue", "costs"):
            a = svc.analysis(from_index=0, to_index=weeks_n - 1)
            ctx["a"] = a
            group = a["revenue"] if name == "revenue" else a["costs"]
            total = a["revenue_total"] if name == "revenue" else a["cost_total"]
            ctx["chart_share"] = charts.donut(
                [(r["name"], r["eqv"]) for r in group],
                title=("Revenue by type" if name == "revenue" else "Cost elements"),
                centre_value=f"{float(total):,.0f}", centre_label="EGP equivalent")
            ctx["chart_rank"] = charts.ranked_bars(
                [(r["name"], r["eqv"]) for r in group],
                title=("Revenue by type" if name == "revenue" else "Cost elements"),
                currency="EGP",
                colour=charts.POS if name == "revenue" else charts.BRAND)
            ctx["group"] = group
            ctx["group_total"] = total

        if name == "collections":
            weeks = svc.build_weeks(weeks_n)
            ctx["plan"] = svc.weekly_stream("collections", weeks)
            ctx["groups"] = STREAMS["collections"].get("group_options") or []
            ctx["chart_share"] = charts.donut(
                [(_opt_name(ctx["groups"], g), svc.egp_equivalent(v))
                 for g, v in ctx["plan"]["group_totals"].items()],
                title="Collections by type", centre_label="EGP equivalent",
                centre_value=f"{float(ctx['plan']['grand_eqv']):,.0f}")
            ctx["chart_weekly"] = charts.ranked_bars(
                [(c["week"]["label"], c["eqv"]) for c in ctx["plan"]["cells"] if c["eqv"]],
                title="Collections by week", currency="EGP", colour=charts.POS,
                max_rows=weeks_n)

        if name == "payables":
            weeks = svc.build_weeks(weeks_n)
            f = svc.build_forecast(weeks=weeks)
            out = [m for m in f["movements"] if m["direction"] == "out"]
            by_stream = {}
            for m in out:
                s = by_stream.setdefault(m["stream"], Decimal("0"))
                by_stream[m["stream"]] = s + m["amount"] * (f["fx"] if m["currency"] == "USD" else 1)
            ctx["payables"] = out
            ctx["by_stream"] = by_stream
            ctx["chart_share"] = charts.donut(
                [(STREAMS[k]["en"], v) for k, v in by_stream.items()],
                title="Payments by table", centre_label="EGP equivalent",
                centre_value=f"{float(sum(by_stream.values(), Decimal('0'))):,.0f}")
            ctx["chart_rank"] = charts.ranked_bars(
                [(STREAMS[k]["en"], v) for k, v in by_stream.items()],
                title="Payments by table", currency="EGP")

        if name == "completeness":
            back = parse_int(request.args.get("back")) or 0
            weeks = svc.build_weeks(n=weeks_n,
                                    start=svc.forecast_start() - __import__("datetime").timedelta(days=7 * back))
            ctx["comp"] = svc.completeness(weeks=weeks)
            ctx["back"] = back

        if name == "audit":
            ctx["af"] = {
                "user": request.args.get("user") or "",
                "stream": request.args.get("stream") or "",
                "action": request.args.get("action") or "",
                "from": request.args.get("from") or "",
                "to": request.args.get("to") or "",
            }
            ctx["audit"] = svc.audit_report(
                user=ctx["af"]["user"] or None, stream=ctx["af"]["stream"] or None,
                action=ctx["af"]["action"] or None,
                date_from=parse_date(ctx["af"]["from"]), date_to=parse_date(ctx["af"]["to"]))
            ctx["actors"] = svc.audit_actors()
            ctx["chart_people"] = charts.ranked_bars(
                [(p["actor"], p["total"]) for p in ctx["audit"]["people"]],
                title="Entries by user", max_rows=12)

        if name == "imports":
            ctx["import_streams"] = [k for k in STREAM_ORDER
                                     if perms.can(current_user, k, "enter")]
            ctx["all_streams"] = STREAM_ORDER
        return ctx

    # ---------------- Uploads ----------------
    @app.route("/reports/template/<key>")
    @login_required
    @require("imports")
    def import_template(key):
        _spec_or_404(key)
        import importer
        data = importer.build_template(key)
        fname = f"template_{key}.xlsx"
        return Response(data, mimetype=importer.XLSX_MIME,
                        headers={"Content-Disposition": f"attachment; filename={fname}"})

    @app.route("/reports/import/<key>", methods=["POST"])
    @login_required
    @require("imports")
    def import_preview(key):
        spec = _spec_or_404(key)
        if not perms.can(current_user, key, "enter"):
            abort(403)
        import importer
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a file to upload.", "warning")
            return redirect(url_for("reports", report="imports"))
        try:
            records = importer.read_table(f, key)
        except Exception as exc:
            flash(f"Could not read that file: {exc}. Use the template, or a .csv.", "danger")
            return redirect(url_for("reports", report="imports"))
        if not records:
            flash("That file has no data rows.", "warning")
            return redirect(url_for("reports", report="imports"))
        accepted, rejected = importer.validate(key, records)
        session[f"import_{key}"] = _jsonable(accepted)
        return render_template("import_preview.html", key=key, spec=spec,
                               accepted=accepted, rejected=rejected,
                               summary=importer.summarise(key, accepted),
                               filename=f.filename, refs=_reference_lists())

    @app.route("/reports/import/<key>/commit", methods=["POST"])
    @login_required
    @require("imports")
    def import_commit(key):
        _spec_or_404(key)
        if not perms.can(current_user, key, "enter"):
            abort(403)
        import importer
        stored = session.pop(f"import_{key}", None)
        if not stored:
            flash("That upload has expired — please upload the file again.", "warning")
            return redirect(url_for("reports", report="imports"))
        rows = _unjson(key, stored)
        written = importer.commit(key, rows, who())
        audit(f"{key}_imported", key, f"{written} row(s) uploaded", stream=key)
        flash(f"{written} row(s) added to {STREAMS[key]['en']} as drafts.", "success")
        return redirect(url_for("table_list", key=key))

    # =========================================================
    # Admin console — System Administrator only
    # =========================================================
    @app.route("/admin", methods=["GET", "POST"])
    @login_required
    @admin_only
    def admin():
        import notifications
        section = request.args.get("s") or "system"
        if section in SUPER_ONLY_SECTIONS and not perms.is_super(current_user):
            abort(403)
        if request.method == "POST":
            if request.form.get("section") in SUPER_ONLY_SECTIONS and \
                    not perms.is_super(current_user):
                abort(403)
            _handle_admin_post(request.form, request.files)
            return redirect(url_for("admin", s=request.form.get("section") or section))
        keys = ["work_week", "week_start", "weekend_rule", "horizon_weeks",
                "dashboard_weeks", "fx_rate", "min_buffer_EGP", "min_buffer_USD",
                "org_name", "product_name", "default_lang", "forecast_start",
                "trend_lookback", "trend_project", "trend_method", "amber_headroom_pct",
                "smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_from_name",
                "notify_days_before", "notices_token"]
        vals = {k: svc.setting(k) for k in keys}
        vals["include_pending"] = svc.setting_bool("include_pending")
        vals["apply_certainty"] = svc.setting_bool("apply_certainty")
        vals["smtp_tls"] = svc.setting_bool("smtp_tls")
        import emailer
        return render_template(
            "admin.html", section=section, v=vals, work_weeks=svc.WORK_WEEKS,
            users=User.query.order_by(User.username).all(),
            matrix=perms.get_matrix(), caps=perms.CORE_CAPS,
            cap_labels=(perms.CAP_LABELS_AR if i18n.lang() == "ar" else perms.CAP_LABELS),
            approver_roles=perms.get_approver_roles(),
            review_roles=perms.get_review_roles(),
            request_roles=perms.get_request_roles(),
            stream_cap=perms.stream_cap,
            audit_rows=AuditLog.query.order_by(AuditLog.id.desc()).limit(200).all(),
            notices=notifications.recent_log(100),
            email_ready=emailer.configured(),
            has_logo=bool(svc.get_logo()),
            **_reference_lists(all_rows=True))

    def _handle_admin_post(form, files):
        import notifications
        section = form.get("section")

        if section == "system":
            for k in ["work_week", "week_start", "weekend_rule", "horizon_weeks",
                      "dashboard_weeks", "fx_rate", "min_buffer_EGP", "min_buffer_USD",
                      "org_name", "product_name", "default_lang", "forecast_start",
                      "trend_lookback", "trend_project", "trend_method",
                      "amber_headroom_pct"]:
                if k in form:
                    set_setting(k, (form.get(k) or "").strip())
            set_setting("include_pending", "1" if form.get("include_pending") == "on" else "0")
            set_setting("apply_certainty", "1" if form.get("apply_certainty") == "on" else "0")
            audit("settings_updated", "system", f"fx={form.get('fx_rate')}")
            flash(i18n.t("saved"), "success")

        elif section == "branding":
            set_setting("org_name", (form.get("org_name") or "").strip())
            set_setting("product_name", (form.get("product_name") or "").strip())
            logo = files.get("logo")
            if form.get("remove_logo") == "1":
                svc.clear_logo()
                audit("logo_removed", "branding")
            elif logo and logo.filename:
                data = logo.read()
                if len(data) > 1_500_000:
                    flash("Logo is too large (1.5 MB maximum).", "warning")
                elif not (logo.mimetype or "").startswith("image/"):
                    flash("That file is not an image.", "warning")
                else:
                    svc.set_logo(data, logo.mimetype)
                    audit("logo_updated", "branding")
            flash(i18n.t("saved"), "success")

        elif section == "email":
            for k in ["smtp_host", "smtp_port", "smtp_user", "smtp_from",
                      "smtp_from_name", "notify_days_before", "notices_token"]:
                if k in form:
                    set_setting(k, (form.get(k) or "").strip())
            if form.get("smtp_pass"):
                set_setting("smtp_pass", form.get("smtp_pass"))
            set_setting("smtp_tls", "1" if form.get("smtp_tls") == "on" else "0")
            perms.set_review_roles(form.getlist("review_roles"))
            perms.set_request_roles(form.getlist("request_roles"))
            audit("email_settings_updated", "email")
            flash(i18n.t("saved"), "success")

        elif section == "send_test":
            import emailer
            to = (form.get("to") or current_user.email or "").strip()
            ok, message = emailer.send(
                to, "Cash flow system — test message",
                "<p>This is a test from the Scientific Gate cash flow system. "
                "If you can read it, the mail settings are correct.</p>")
            flash(message, "success" if ok else "danger")

        elif section == "send_requests":
            result = notifications.send_input_requests(triggered_by=who(), force=True)
            flash(f"Input requests: {result['sent']} sent, {result['skipped']} skipped.",
                  "success" if result["sent"] else "warning")

        elif section == "delegation":
            from models import RolePermission
            changes = 0
            for r in perms.ROLE_KEYS:
                if r == "admin":
                    continue
                for cap in perms.CAP_KEYS:
                    allowed = form.get(f"p_{r}_{cap}") == "on"
                    row = db.session.get(RolePermission, (r, cap))
                    if bool(row and row.allowed) != allowed:
                        perms.set_permission(r, cap, allowed)
                        changes += 1
            perms.set_approver_roles(form.getlist("approver_roles"))
            audit("delegation_updated", "matrix", f"{changes} change(s)")
            flash(i18n.t("saved"), "success")

        elif section == "delegation_reset":
            perms.seed_matrix(force=True)
            audit("delegation_reset", "matrix")
            flash(i18n.t("saved"), "success")

        elif section == "users":
            _handle_users(form)

        elif section == "reference":
            _handle_reference(form)

    def _handle_users(form):
        act = form.get("action")
        if act == "new":
            un = (form.get("username") or "").strip()
            pw = form.get("password") or ""
            if not un or User.query.filter_by(username=un).first():
                flash("Username missing or already in use.", "danger")
            elif len(pw) < 6:
                flash("Password must be at least 6 characters.", "danger")
            else:
                u = User(username=un, full_name=form.get("full_name"),
                         email=(form.get("email") or None),
                         role=form.get("role", "viewer"), lang=form.get("lang", "en"))
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
                audit("user_created", un, f"role={u.role}")
                flash(i18n.t("saved"), "success")
        elif act == "update":
            u = db.session.get(User, parse_int(form.get("id"))) or abort(404)
            new_role = form.get("role") or u.role
            if u.role == "super_admin" and new_role != "super_admin" and \
                    User.query.filter_by(role="super_admin").count() <= 1:
                flash("You cannot change the role of the only Super Administrator.", "danger")
                return
            old = f"role={u.role}, email={u.email or '-'}"
            u.full_name = form.get("full_name") or u.full_name
            u.email = form.get("email") or None
            u.role = new_role
            u.lang = form.get("lang") or u.lang
            u.active = form.get("active") == "1"
            db.session.commit()
            audit("user_updated", u.username, f"{old} → role={u.role}, email={u.email or '-'}")
            flash(i18n.t("saved"), "success")
        elif act == "reset":
            u = db.session.get(User, parse_int(form.get("id"))) or abort(404)
            pw = form.get("password") or ""
            if len(pw) < 6:
                flash("Password must be at least 6 characters.", "danger")
            else:
                u.set_password(pw)
                db.session.commit()
                audit("user_password_reset", u.username)
                flash(i18n.t("saved"), "success")

    def _handle_reference(form):
        kind = form.get("kind")
        Model = RevenueType if kind == "revenue" else CostCategory
        rid = parse_int(form.get("id"))
        row = db.session.get(Model, rid) if rid else Model()
        if row is None:
            abort(404)
        if form.get("remove") == "1" and rid:
            row.active = False
        else:
            row.code = (form.get("code") or "").strip().upper()[:30]
            row.name_en = (form.get("name_en") or "").strip()
            row.name_ar = (form.get("name_ar") or "").strip() or None
            row.active = True
            if kind == "cost":
                row.group_key = form.get("group_key") or "operating"
            if not row.code or not row.name_en:
                flash(i18n.t("required_field"), "danger")
                return
            if not rid:
                db.session.add(row)
        db.session.commit()
        audit("reference_saved", f"{kind}:{row.code}", row.name_en)
        flash(i18n.t("saved"), "success")

    # ---------------- errors ----------------
    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403,
                               msg="You do not have permission for that action."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, msg="Page not found."), 404

    @app.errorhandler(413)
    def _413(e):
        return render_template("error.html", code=413,
                               msg="That file is too large (12 MB maximum)."), 413

    return app


# ============================================================
# Helpers
# ============================================================

ENDPOINT_CAP = {
    # "dashboard" is deliberately absent: the view itself sends a data-entry
    # user to their own table rather than refusing them the front page.
    "forecast": "forecast",
    "week_detail": "forecast",
    "trend": "trend",
    "reports": "reports",
    "import_template": "imports",
    "import_preview": "imports",
    "import_commit": "imports",
    "banks": "banks_view",
    "bank_delete": "banks_edit",
    "opening": "forecast",
    "week_confirm": "week_signoff",
    "week_reopen": "week_signoff",
}


SUPER_ONLY_SECTIONS = {"system", "delegation", "delegation_reset", "branding"}


def _pending_count():
    """Entries submitted and waiting for review, for the dashboard tile."""
    try:
        return Approval.query.filter_by(status="pending").count()
    except Exception:
        return 0


def _spec_or_404(key):
    try:
        return get_stream(key)
    except KeyError:
        abort(404)


def _reference_lists(all_rows=False):
    rq, cq = RevenueType.query, CostCategory.query
    if not all_rows:
        rq = rq.filter_by(active=True)
        cq = cq.filter_by(active=True)
    return {"revenue_types": rq.order_by(RevenueType.sort, RevenueType.name_en).all(),
            "cost_categories": cq.order_by(CostCategory.sort, CostCategory.name_en).all()}


def _summary(key, row):
    from services import _describe
    return f"{STREAMS[key]['en']}: {_describe(key, row)} — {row.currency} {row.amount}"


def _opt_name(options, code):
    for o in options:
        if o[0] == code:
            return o[1]
    return code or "Other"


def _jsonable(rows):
    """Session storage: dates and Decimals become strings."""
    out = []
    for r in rows:
        rec = {}
        for k, v in r.items():
            if isinstance(v, (date, datetime)):
                rec[k] = v.isoformat()
            elif isinstance(v, Decimal):
                rec[k] = str(v)
            else:
                rec[k] = v
        out.append(rec)
    return out


def _unjson(key, rows):
    spec = STREAMS[key]
    types = {f["name"]: f["type"] for f in spec["fields"]}
    out = []
    for r in rows:
        rec = {}
        for k, v in r.items():
            if k.startswith("_") or v is None:
                continue
            t = types.get(k)
            if t == "date":
                rec[k] = date.fromisoformat(v) if isinstance(v, str) else v
            elif t == "money":
                rec[k] = D(v)
            else:
                rec[k] = v
        out.append(rec)
    return out


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
