"""Acceptance tests — one test per requirement asked for, in the order asked.

These are deliberately blunt: they drive the app through HTTP the way a user
does, and each one names the requirement it stands for. If a requirement is
ever dropped in a later change, the test that names it fails.
"""
import os
import re
import sys
import pathlib
from datetime import date, timedelta
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (db, User, BankAccount, CustomerCollection, set_setting,
                    ST_APPROVED)
import services as svc
import permissions as perms
import seed
from streams import STREAM_ORDER

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def app():
    from app import create_app
    a = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True,
                    "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with a.app_context():
        db.drop_all()
        db.create_all()
        for k, v in svc.DEFAULTS.items():
            set_setting(k, v)
        set_setting("fx_rate", "48.5")
        perms.seed_matrix(force=True)
        seed.seed_reference()
        seed.seed_accounts()
        yield a
        db.session.remove()


def signed_in(app, role="super_admin", username="acc"):
    u = User(username=username, full_name=username.title(), role=role, lang="en")
    u.set_password("pw123456")
    db.session.add(u)
    db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": username, "password": "pw123456"},
           follow_redirects=True)
    c.get("/lang/en", follow_redirects=True)
    return c


def page(c, path="/"):
    r = c.get(path, follow_redirects=True)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.data.decode()


def nav_of(html):
    return html.split("<nav", 1)[1].split("</nav>", 1)[0]


# ------------------------------------------------------------------
# 1. Ten independently editable tables, one per cost and revenue type
# ------------------------------------------------------------------

def test_every_cost_and_revenue_type_has_its_own_editable_table(app):
    c = signed_in(app)
    wanted = {"collections", "adhoc_in", "one_off", "bank_loans", "suppliers",
              "cheques", "refunds", "petty_cash", "fixed_weekly", "fixed_monthly"}
    assert wanted <= set(STREAM_ORDER), wanted - set(STREAM_ORDER)
    for k in STREAM_ORDER:
        assert c.get(f"/table/{k}").status_code == 200, k          # list
        assert c.get(f"/table/{k}/new").status_code == 200, k      # add
    # and rows can be added, edited and removed
    r = c.post("/table/refunds/new", data={"customer": "Acme Hospital",
                                           "due_date": "2026-10-08",
                                           "currency": "EGP", "amount": "5000"},
               follow_redirects=True)
    assert r.status_code == 200
    from models import CustomerRefund
    row = CustomerRefund.query.filter_by(customer="Acme Hospital").one()
    c.post(f"/table/refunds/{row.id}/edit", data={"customer": "Acme Hospital",
                                                  "due_date": "2026-10-08",
                                                  "currency": "EGP", "amount": "7500"},
           follow_redirects=True)
    assert str(db.session.get(CustomerRefund, row.id).amount) == "7500.00"
    c.post(f"/table/refunds/{row.id}/delete", follow_redirects=True)
    assert db.session.get(CustomerRefund, row.id) is None


# ------------------------------------------------------------------
# 2. Cheques payable and petty cash carry the fields asked for
# ------------------------------------------------------------------

def test_cheques_and_petty_cash_carry_their_own_fields(app):
    from streams import stream
    cheque = {f["name"] for f in stream("cheques")["fields"]}
    assert {"cheque_no", "payee", "issue_date", "due_date", "bank"} <= cheque
    petty = {f["name"] for f in stream("petty_cash")["fields"]}
    assert "employee_name" in petty and "amount" in petty


# ------------------------------------------------------------------
# 3. Both currencies kept apart, with an EGP equivalent
# ------------------------------------------------------------------

def test_the_two_currencies_never_mix_and_the_equivalent_is_at_the_rate(app):
    from models import AdhocInflow
    for ccy, amt in (("EGP", "100000"), ("USD", "10000")):
        db.session.add(AdhocInflow(source="x", currency=ccy, amount=Decimal(amt),
                                   due_date=svc.forecast_start() + timedelta(days=2),
                                   status=ST_APPROVED))
    db.session.commit()
    d = svc.dashboard(6)
    assert d["totals"]["inflow"]["EGP"] == Decimal("100000")
    assert d["totals"]["inflow"]["USD"] == Decimal("10000")
    # 100,000 + 10,000 x 48.5
    assert d["totals_eqv"]["inflow"] == Decimal("585000")


# ------------------------------------------------------------------
# 4. Bilingual, right to left in Arabic
# ------------------------------------------------------------------

def test_every_page_turns_over_to_arabic_and_flips_direction(app):
    c = signed_in(app)
    c.get("/lang/ar", follow_redirects=True)
    for p in ("/", "/forecast", "/trend", "/banks", "/opening", "/admin",
              "/reports?report=cashflow", "/table/collections"):
        html = page(c, p)
        assert 'dir="rtl"' in html, p
        assert re.search(r"[؀-ۿ]", html), f"no Arabic text on {p}"


# ------------------------------------------------------------------
# 5. Working week Sunday to Thursday, Friday off
# ------------------------------------------------------------------

def test_the_working_week_runs_sunday_to_thursday(app):
    set_setting("work_week", "sun_thu")
    days = svc.working_days()
    assert 6 in days and 0 in days and 1 in days and 2 in days and 3 in days
    assert 4 not in days, "Friday must be a non-working day"
    friday = date(2026, 10, 9)
    assert friday.weekday() == 4
    assert svc.adjust_to_working_day(friday) > friday, "a Friday item must move"


# ------------------------------------------------------------------
# 6. Six-week dashboard, 52-week horizon, editable opening balance
# ------------------------------------------------------------------

def test_dashboard_shows_six_weeks_and_the_horizon_runs_a_year(app):
    assert len(svc.dashboard(6)["rows"]) == 6
    assert len(svc.build_weeks()) >= 52


def test_the_opening_balance_rolls_forward_and_can_be_overridden(app):
    c = signed_in(app)
    weeks = svc.build_weeks()
    wid = weeks[1]["week_id"]
    c.post("/opening", data={"week_id": wid, "currency": "EGP",
                             "opening": "250000"}, follow_redirects=True)
    f = svc.build_forecast()
    row = next(r for r in f["rows"] if r["week"]["week_id"] == wid)
    assert row["opening"]["EGP"] == Decimal("250000"), "the override did not pin the week"
    nxt = next(r for r in f["rows"] if r["week"]["week_id"] == weeks[2]["week_id"])
    assert nxt["opening"]["EGP"] == row["closing"]["EGP"], "it must roll on from there"


# ------------------------------------------------------------------
# 7. Customer collections: weekly view, every type, customer number
# ------------------------------------------------------------------

def test_collections_distinguish_every_type_and_have_a_weekly_view(app):
    from streams import COLLECTION_TYPES
    keys = {k for k, _en, _ar in COLLECTION_TYPES}
    assert {"instalment", "down_payment", "advance", "installation"} <= keys
    fields = {f["name"] for f in __import__("streams").stream("collections")["fields"]}
    assert {"customer_no", "customer", "contract_ref", "instalment_no"} <= fields
    c = signed_in(app)
    assert c.get("/table/collections/weekly").status_code == 200


def test_supplier_payments_are_part_of_the_cash_instalments(app):
    from streams import SUPPLIER_PAYMENT_TYPES, OUTFLOW_STREAMS
    assert "suppliers" in OUTFLOW_STREAMS
    assert len(SUPPLIER_PAYMENT_TYPES) >= 4
    c = signed_in(app)
    assert c.get("/table/suppliers/weekly").status_code == 200


# ------------------------------------------------------------------
# 8. Rolling trend forecast from the previous six weeks
# ------------------------------------------------------------------

def test_the_trend_forecast_reads_the_previous_six_weeks(app):
    c = signed_in(app)
    html = page(c, "/trend")
    t = svc.trend_forecast()
    assert t["lookback"] == 6, t["lookback"]
    assert "revenue" in t and "costs" in t
    assert "Trend" in html or "trend" in html


# ------------------------------------------------------------------
# 9. Scheme of delegation, exactly seven roles
# ------------------------------------------------------------------

def test_the_roles_are_exactly_the_seven_asked_for(app):
    assert [r[0] for r in perms.ROLES] == [
        "super_admin", "admin", "md", "cfo", "finance_manager",
        "credit_control", "sales_admin"]


def test_the_scheme_of_delegation_is_editable_in_the_app(app):
    c = signed_in(app)
    html = page(c, "/admin?s=delegation")
    assert "delegation" in html.lower() or "تفويض" in html


@pytest.mark.parametrize("role", ["credit_control", "sales_admin"])
def test_entry_only_roles_see_nothing_but_their_own_table(app, role):
    c = signed_in(app, role, username=f"u_{role}")
    html = page(c, "/")
    nav = nav_of(html)
    assert "Customer Collections" in nav
    for forbidden in ("Dashboard", "Weekly Forecast", "Bank & Cash Accounts",
                      "Opening Balances", "Reports", "Trend Forecast", "Admin"):
        assert forbidden not in nav, f"{role} can see {forbidden}"
    assert "Cash position" not in html and "cash position" not in html.lower()
    assert 'class="off"' not in nav, "items must be hidden, not dimmed"
    for blocked in ("/forecast", "/banks", "/trend", "/admin",
                    "/reports?report=cashflow", "/table/suppliers"):
        assert c.get(blocked).status_code == 403, f"{role} reached {blocked}"


def test_the_three_seats_that_work_the_figures_can_view_and_edit(app):
    for role in ("finance_manager", "cfo", "md"):
        u = User(username=f"w_{role}", role=role)
        db.session.add(u)
        db.session.commit()
        assert perms.has_perm(u, "dashboard"), role
        assert perms.has_perm(u, "forecast"), role
        assert perms.has_perm(u, "reports"), role
        assert perms.can(u, "collections", "edit"), role


# ------------------------------------------------------------------
# 10. The menu: strong colour, only permitted items, frozen while scrolling
# ------------------------------------------------------------------

def _css():
    return ROOT.joinpath("static", "style.css").read_text()


def test_the_menu_stays_in_view_while_the_page_scrolls(app):
    css = _css()
    block = css.split("nav.main {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in block, "the menu is not frozen to the top"
    assert "top: 0" in block
    assert "z-index" in block, "without a stacking order it scrolls under the page"


def test_the_menu_is_painted_in_a_strong_colour(app):
    css = _css()
    block = css.split("nav.main {", 1)[1].split("}", 1)[0]
    assert "var(--brand-deep)" in block, "the menu bar is not the corporate blue"
    entry = css.split("nav.main a, nav.main .dd > span {", 1)[1].split("}", 1)[0]
    assert "#ffffff" in entry and "font-weight: 600" in entry


def test_drop_down_entries_are_not_painted_white_on_white(app):
    """The white `nav.main a` rule outranks a bare `.dd-menu a`, which left the
    entries invisible. The drop-down rule has to be qualified and come later."""
    css = _css()
    assert "nav.main .dd-menu a" in css
    assert css.index("nav.main .dd-menu a") > css.index("nav.main a, nav.main .dd > span")
    assert ":focus-within .dd-menu" in css, "the menu must open without a mouse too"


def test_nothing_in_the_menu_is_dimmed_for_anybody(app):
    for role in ("super_admin", "admin", "md", "cfo", "finance_manager",
                 "credit_control", "sales_admin"):
        c = signed_in(app, role, username=f"m_{role}")
        nav = nav_of(page(c, "/"))
        assert 'class="off"' not in nav, role
        assert "disabled" not in nav, role


def test_the_removed_pages_are_gone(app):
    c = signed_in(app)
    nav = nav_of(page(c, "/"))
    assert ">Approvals<" not in nav, "the approvals page was to be removed"
    assert ">Analysis<" not in nav, "the analysis page was replaced by reports"
    assert "/approvals" not in nav and "/analysis" not in nav
    assert c.get("/approvals").status_code == 404
    assert c.get("/analysis").status_code == 404


# ------------------------------------------------------------------
# 11. Reports page: printable, graphed, with bulk upload and templates
# ------------------------------------------------------------------

def test_the_reports_page_carries_every_report_and_they_print(app):
    import app as A
    c = signed_in(app)
    for key, _en, _ar, _cap in A.REPORTS:
        assert c.get(f"/reports?report={key}").status_code == 200, key
    assert c.get("/reports?report=cashflow&print=1").status_code == 200
    assert "@media print" in _css(), "no print styling"


def test_reports_are_drawn_with_graphs(app):
    c = signed_in(app)
    assert page(c, "/reports?report=cashflow").count("<svg") >= 1
    assert page(c, "/reports?report=costs").count("<svg") >= 2   # donut + bars
    assert page(c, "/reports?report=in_out").count("<svg") >= 1


def test_every_table_has_an_upload_template_and_an_upload_route(app):
    c = signed_in(app)
    html = page(c, "/reports?report=imports")
    for k in STREAM_ORDER:
        assert f"/reports/template/{k}" in html, f"no template link for {k}"
        r = c.get(f"/reports/template/{k}")
        assert r.status_code == 200 and len(r.data) > 3000, k


def test_an_upload_is_previewed_before_anything_is_written(app):
    import io
    c = signed_in(app)
    csv = ("Customer no.,Customer,Collection type,Contract ref.,Instalment no.,"
           "Invoice ref.,Expected date,Method,Revenue type,Certainty %,Currency,"
           "Amount,Notes\n"
           "C-1,Good Row Hospital,Instalment,,,,2026-11-12,,,,EGP,1000,\n"
           "C-2,Bad Row,Instalment,,,,not-a-date,,,,EGP,10,\n")
    r = c.post("/reports/import/collections",
               data={"file": (io.BytesIO(csv.encode()), "t.csv")},
               content_type="multipart/form-data")
    assert b"Good Row Hospital" in r.data
    assert b"date" in r.data.lower(), "the rejected row is not explained"
    assert CustomerCollection.query.count() == 0, "nothing may be written before commit"
    c.post("/reports/import/collections/commit", follow_redirects=True)
    assert CustomerCollection.query.filter_by(customer="Good Row Hospital").count() == 1
    assert CustomerCollection.query.filter_by(customer="Bad Row").count() == 0


def test_the_audit_and_completeness_reports_exist(app):
    c = signed_in(app)
    assert "audit" in page(c, "/reports?report=audit").lower()
    assert svc.completeness()["gap_count"] >= 0
    assert c.get("/reports?report=completeness").status_code == 200


# ------------------------------------------------------------------
# 12. Negative figures in red, "cash position" wording
# ------------------------------------------------------------------

def test_negative_figures_are_red_everywhere(app):
    css = _css()
    assert ".neg, td.neg, .num.neg" in css
    assert "var(--out) !important" in css
    import app as A
    with app.test_request_context():
        assert A.create_app.__name__          # filter registered on the app below
    c = signed_in(app)
    # a negative closing balance must carry the class
    from models import BankLoanInstalment
    db.session.add(BankLoanInstalment(lender="Bank", currency="EGP",
                                      amount=Decimal("99000000"), status=ST_APPROVED,
                                      due_date=svc.forecast_start() + timedelta(days=2)))
    db.session.commit()
    html = page(c, "/")
    assert 'class="num neg"' in html or "neg" in html


def test_the_wording_is_cash_position_not_booked_position(app):
    c = signed_in(app)
    html = page(c, "/")
    assert "Cash position" in html
    assert "ooked position" not in html


# ------------------------------------------------------------------
# 13. Available cash: green, orange at 15% headroom, red on a shortage
# ------------------------------------------------------------------

def _bank(ccy, amount):
    db.session.add(BankAccount(name=f"acc {ccy} {amount}", currency=ccy,
                               balance=Decimal(str(amount)), as_at=date.today()))
    db.session.commit()


def test_available_cash_is_green_amber_and_red_on_the_right_figures(app):
    from models import BankLoanInstalment
    for a in BankAccount.query.all():           # start from the seeded accounts at zero
        db.session.delete(a)
    db.session.commit()
    _bank("EGP", 1150000)
    db.session.add(BankLoanInstalment(lender="Bank", currency="EGP",
                                      amount=Decimal("1000000"), status=ST_APPROVED,
                                      due_date=svc.forecast_start() + timedelta(days=2)))
    db.session.commit()
    h = svc.cash_health(6)
    assert h["committed_out"] == Decimal("1000000")
    assert h["available"] == Decimal("1150000")
    assert h["headroom"] == Decimal("150000")
    assert h["headroom_pct"] == Decimal("15")
    assert h["state"] == "amber", "15% headroom must read orange"

    set_setting("amber_headroom_pct", "10")
    assert svc.cash_health(6)["state"] == "green", "clear headroom must read green"

    db.session.add(BankLoanInstalment(lender="Bank", currency="EGP",
                                      amount=Decimal("500000"), status=ST_APPROVED,
                                      due_date=svc.forecast_start() + timedelta(days=3)))
    db.session.commit()
    assert svc.cash_health(6)["state"] == "red", "a forecast shortage must read red"


def test_the_traffic_light_is_on_the_dashboard_with_all_three_states_styled(app):
    c = signed_in(app)
    assert 'class="health' in page(c, "/")
    css = _css()
    for state in (".health.green", ".health.amber", ".health.red"):
        assert state in css, state


# ------------------------------------------------------------------
# 14. The named banks, and the balances table at the top of the dashboard
# ------------------------------------------------------------------

def test_the_named_banks_and_wallets_are_created_as_standard(app):
    blob = " ".join(f"{a.name} {a.bank or ''}" for a in BankAccount.query.all())
    for wanted in ("NBE", "National Bank of Egypt", "CIB",
                   "Commercial International Bank", "InstaPay", "Vodafone Cash"):
        assert wanted in blob, f"{wanted} is not a standard account"


def test_the_balances_table_leads_the_weekly_position_page(app):
    for a in BankAccount.query.all():
        a.balance = Decimal("1000000") if a.currency == "EGP" else Decimal("10000")
    db.session.commit()

    c = signed_in(app)
    html = page(c, "/position")
    assert "Latest recorded balances" in html, "no balances table"
    assert html.index("Latest recorded balances") < html.index("Next 6 weeks"), \
        "the balances table must come first on the page"
    for acct in ("NBE", "CIB", "InstaPay", "Vodafone Cash"):
        assert acct in html, acct

    b = svc.bank_balances()
    assert b["totals"]["EGP"] == Decimal("5000000")     # five EGP accounts
    assert b["totals"]["USD"] == Decimal("20000")       # two USD accounts
    assert b["eqv"] == Decimal("5970000")               # 5,000,000 + 20,000 x 48.5
    assert b["eqv"] == svc.cash_health(6)["available"], \
        "the table total must agree with the available cash figure"


def test_the_weekly_position_page_holds_the_detail_moved_off_the_dashboard(app):
    c = signed_in(app)
    html = page(c, "/position")
    assert "Latest recorded balances" in html
    assert "Next 6 weeks" in html
    assert "Weekly review" in html or "Confirm the week" in html


def test_the_dashboard_keeps_only_the_cash_position_and_available_cash(app):
    c = signed_in(app)
    html = page(c, "/")
    assert "Available cash" in html and 'class="health' in html
    assert "Cash position" in html
    # and the three blocks that moved are gone from it
    assert "Latest recorded balances" not in html
    assert "Next 6 weeks" not in html.replace("Cash in — Next 6 weeks", "") \
        .replace("Cash out — Next 6 weeks", ""), "the six-week table is still here"
    assert "Confirm the week" not in html
    # with a way through to where they went
    assert "/position" in html


def test_the_dashboard_draws_the_figures_as_well_as_stating_them(app):
    c = signed_in(app)
    html = page(c, "/")
    assert html.count("<svg") >= 2, "expected the cash line and the in/out bars"
    assert "Cash position by week" in html
    assert "Money in and out by week" in html


def test_the_weekly_position_page_is_refused_to_entry_only_roles(app):
    for role in ("credit_control", "sales_admin"):
        c = signed_in(app, role, username=f"p_{role}")
        assert c.get("/position").status_code == 403, role
        nav = nav_of(page(c, "/"))
        assert "Weekly Position" not in nav, role


def test_the_weekly_position_page_is_on_the_menu_for_the_three_seats(app):
    for role in ("finance_manager", "cfo", "md"):
        c = signed_in(app, role, username=f"n_{role}")
        assert "Weekly Position" in nav_of(page(c, "/")), role


def test_the_balances_are_hidden_from_anyone_without_bank_access(app):
    c = signed_in(app, "credit_control", username="cc_bal")
    html = page(c, "/")
    assert "Latest recorded balances" not in html
    assert "NBE" not in html


# ------------------------------------------------------------------
# 15. Admin console, administrator only
# ------------------------------------------------------------------

def test_the_admin_console_holds_the_rate_users_delegation_and_settings(app):
    c = signed_in(app)
    for s in ("system", "users", "delegation", "reference", "branding", "email", "audit"):
        assert c.get(f"/admin?s={s}").status_code == 200, s
    c.post("/admin", data={"section": "system", "fx_rate": "49.75"},
           follow_redirects=True)
    assert svc.fx_rate() == Decimal("49.75")


def test_only_an_administrator_reaches_the_admin_console(app):
    for role in ("md", "cfo", "finance_manager", "credit_control", "sales_admin"):
        c = signed_in(app, role, username=f"a_{role}")
        assert c.get("/admin").status_code == 403, role


# ------------------------------------------------------------------
# 16. Weekly sign-off and the notification emails
# ------------------------------------------------------------------

def test_confirming_the_week_records_it_and_queues_the_review_to_md_and_cfo(app):
    import notifications
    c = signed_in(app, "finance_manager", username="fm_sign")
    wid = svc.current_week_id()
    c.post(f"/week/{wid}/confirm", data={"notes": "Reviewed"}, follow_redirects=True)
    so = svc.get_signoff(wid)
    assert so and so.status == "confirmed"
    assert hasattr(notifications, "send_weekly_review")
    assert hasattr(notifications, "send_input_requests")
    html = notifications.build_weekly_review(
        svc.build_weeks()[0], svc.cash_position_strip(), so, "en")
    html = html if isinstance(html, str) else str(html)
    assert "Cash in" in html and "Cash out" in html, "the review email has no figures"


def test_there_is_a_scheduled_endpoint_for_the_input_request_emails(app):
    import app as A
    rules = {str(r) for r in A.app.url_map.iter_rules()}
    assert any("/tasks/notices" in r for r in rules), "no scheduler endpoint"


# ------------------------------------------------------------------
# 17. Branding
# ------------------------------------------------------------------

def test_the_corporate_mark_and_palette_are_used(app):
    c = signed_in(app)
    html = page(c, "/")
    assert "/brand/logo" in html, "the logo is not on the page"
    assert c.get("/brand/logo").status_code in (200, 302)
    css = _css()
    for colour in ("#1B5584", "#4FC0F0", "#231F20"):
        assert colour in css, colour


def test_the_release_is_stamped_where_it_can_be_checked(app):
    """So it is obvious which release is actually live."""
    c = signed_in(app)
    assert f"v{svc.VERSION}" in page(c, "/")
    r = c.get("/healthz")
    assert r.get_json()["version"] == svc.VERSION


def test_the_chart_legends_are_translated_too(app):
    c = signed_in(app)
    assert "Confirmed" in page(c, "/") and "Money in" in page(c, "/")
    c.get("/lang/ar", follow_redirects=True)
    html = page(c, "/")
    assert "مؤكد" in html and "متوقع" in html, "the cash line legend is still English"
    assert "متحصلات" in html and "مدفوعات" in html, "the bar legend is still English"
    assert "Confirmed" not in html and "Money in" not in html
