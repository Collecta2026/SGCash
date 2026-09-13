"""Accuracy tests for the cash flow engine.

Every figure asserted here is computed independently of the engine —
by hand in the test, or by a deliberately naive re-implementation — so
a bug in `services.py` cannot make its own test pass.
"""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (db, CustomerCollection, BankLoanInstalment, ChequePayable,
                    FixedMonthlyCost, FixedWeeklyCost, AdhocInflow, OneOffItem,
                    BankAccount, set_setting, ST_APPROVED, ST_DRAFT, ST_SUBMITTED)
import services as svc


@pytest.fixture()
def app():
    from app import create_app
    a = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://", "TESTING": True})
    with a.app_context():
        db.drop_all()
        db.create_all()
        for k, v in svc.DEFAULTS.items():
            set_setting(k, v)
        set_setting("forecast_start", "2026-01-04")   # a Sunday
        set_setting("horizon_weeks", "52")
        set_setting("week_start", "6")                # Sunday
        set_setting("work_week", "sun_thu")
        set_setting("weekend_rule", "next")
        set_setting("fx_rate", "50")
        yield a
        db.session.remove()


def mk_bank(ccy, amount):
    db.session.add(BankAccount(name=f"{ccy} account", currency=ccy,
                               balance=Decimal(str(amount)), as_at=date(2026, 1, 4)))
    db.session.commit()


def approved(model, **kw):
    kw.setdefault("status", ST_APPROVED)
    kw.setdefault("currency", "EGP")
    row = model(**kw)
    db.session.add(row)
    db.session.commit()
    return row


# ============================================================
# Calendar
# ============================================================

def test_weeks_tile_the_calendar_exactly(app):
    weeks = svc.build_weeks()
    assert len(weeks) == 52
    assert weeks[0]["start"] == date(2026, 1, 4)
    for i, w in enumerate(weeks):
        assert (w["end"] - w["start"]).days == 6
        assert w["start"].weekday() == 6            # Sunday
        if i:
            # contiguous: this week starts the day after the last one ended
            assert w["start"] == weeks[i - 1]["end"] + timedelta(days=1)


def test_every_date_in_horizon_lands_in_exactly_one_week(app):
    weeks = svc.build_weeks()
    d = weeks[0]["start"]
    last = weeks[-1]["end"]
    seen = 0
    while d <= last:
        hits = [i for i, w in enumerate(weeks) if w["start"] <= d <= w["end"]]
        assert len(hits) == 1, d
        assert svc.week_for_date(d, weeks) == hits[0], d
        seen += 1
        d += timedelta(days=1)
    assert seen == 52 * 7


def test_dates_outside_the_horizon_are_dropped_not_clamped(app):
    weeks = svc.build_weeks()
    assert svc.week_for_date(weeks[0]["start"] - timedelta(days=1), weeks) is None
    assert svc.week_for_date(weeks[-1]["end"] + timedelta(days=1), weeks) is None


@pytest.mark.parametrize("day,rule,expected", [
    # Egypt: weekend is Friday (4) and Saturday (5)
    (date(2026, 3, 6), "next", date(2026, 3, 8)),       # Fri -> Sun
    (date(2026, 3, 7), "next", date(2026, 3, 8)),       # Sat -> Sun
    (date(2026, 3, 6), "previous", date(2026, 3, 5)),   # Fri -> Thu
    (date(2026, 3, 7), "previous", date(2026, 3, 5)),   # Sat -> Thu
    (date(2026, 3, 5), "next", date(2026, 3, 5)),       # Thu unchanged
    (date(2026, 3, 6), "none", date(2026, 3, 6)),       # rule off
])
def test_working_day_adjustment_egypt(app, day, rule, expected):
    set_setting("weekend_rule", rule)
    assert svc.adjust_to_working_day(day) == expected


def test_working_day_adjustment_uk_week(app):
    set_setting("work_week", "mon_fri")
    set_setting("weekend_rule", "next")
    assert svc.adjust_to_working_day(date(2026, 3, 7)) == date(2026, 3, 9)   # Sat -> Mon
    assert svc.adjust_to_working_day(date(2026, 3, 8)) == date(2026, 3, 9)   # Sun -> Mon
    assert svc.adjust_to_working_day(date(2026, 3, 6)) == date(2026, 3, 6)   # Fri is a working day


# ============================================================
# Recurrence
# ============================================================

def test_monthly_cost_recurs_exactly_twelve_times_a_year(app):
    mk_bank("EGP", 0)
    approved(FixedMonthlyCost, description="Rent", day_of_month=10,
             amount=Decimal("1000"))
    weeks = svc.build_weeks()
    moves = svc.generate_movements(weeks)
    # the horizon is 364 days, so it spans 12 occurrences of the 10th
    assert len(moves) == 12
    months = sorted({(m["nominal"].year, m["nominal"].month) for m in moves})
    assert len(months) == 12


def test_monthly_day_31_clamps_to_month_end(app):
    mk_bank("EGP", 0)
    approved(FixedMonthlyCost, description="Month end", day_of_month=31,
             amount=Decimal("100"))
    moves = svc.generate_movements(svc.build_weeks())
    by_month = {(m["nominal"].year, m["nominal"].month): m["nominal"].day for m in moves}
    assert by_month[(2026, 2)] == 28      # 2026 is not a leap year
    assert by_month[(2026, 4)] == 30
    assert by_month[(2026, 1)] == 31


def test_weekly_cost_recurs_once_per_week(app):
    mk_bank("EGP", 0)
    approved(FixedWeeklyCost, description="Wages", weekday=2, amount=Decimal("500"))
    weeks = svc.build_weeks()
    moves = svc.generate_movements(weeks)
    assert len(moves) == 52
    assert sorted(m["week"] for m in moves) == list(range(52))


def test_effective_dating_bounds_a_recurring_cost(app):
    mk_bank("EGP", 0)
    approved(FixedWeeklyCost, description="Temp cover", weekday=2, amount=Decimal("500"),
             effective_from=date(2026, 2, 1), effective_to=date(2026, 2, 28))
    moves = svc.generate_movements(svc.build_weeks())
    assert len(moves) == 4
    assert all(date(2026, 2, 1) <= m["nominal"] <= date(2026, 2, 28) for m in moves)


def test_weekend_recurrence_is_shifted_not_lost(app):
    mk_bank("EGP", 0)
    # Friday (weekday 4) is a non-working day in Egypt
    approved(FixedWeeklyCost, description="Friday charge", weekday=4, amount=Decimal("100"))
    weeks = svc.build_weeks()
    moves = svc.generate_movements(weeks)
    assert all(m["nominal"].weekday() == 4 for m in moves)
    assert all(m["date"].weekday() == 6 for m in moves)   # every one shifted to Sunday
    # One occurrence lands in every week of the horizon, including the first —
    # whose charge is the Friday *before* the horizon opens, shifted forward.
    assert sorted(m["week"] for m in moves) == list(range(52))
    assert moves[0]["nominal"] == date(2026, 1, 2)
    assert moves[0]["date"] == date(2026, 1, 4)


# ============================================================
# Rollover arithmetic
# ============================================================

def test_closing_equals_opening_plus_in_minus_out_every_week(app):
    mk_bank("EGP", 1000000)
    mk_bank("USD", 50000)
    approved(CustomerCollection, customer="A", amount=Decimal("250000"),
             due_date=date(2026, 1, 7))
    approved(FixedMonthlyCost, description="Rent", day_of_month=5, amount=Decimal("40000"))
    approved(BankLoanInstalment, lender="CIB", amount=Decimal("9000"), currency="USD",
             due_date=date(2026, 2, 3))
    f = svc.build_forecast()
    for r in f["rows"]:
        for c in ("EGP", "USD"):
            assert r["closing"][c] == r["opening"][c] + r["inflow"][c] - r["outflow"][c]


def test_opening_rolls_over_from_the_previous_closing(app):
    mk_bank("EGP", 500000)
    approved(FixedWeeklyCost, description="Wages", weekday=1, amount=Decimal("7000"))
    f = svc.build_forecast()
    for i, r in enumerate(f["rows"][1:], start=1):
        assert r["opening"]["EGP"] == f["rows"][i - 1]["closing"]["EGP"]
    # and the first week opens on the bank balances
    assert f["rows"][0]["opening"]["EGP"] == Decimal("500000")


def test_first_week_opening_is_the_sum_of_included_accounts_only(app):
    mk_bank("EGP", 300000)
    db.session.add(BankAccount(name="Excluded", currency="EGP", balance=Decimal("999999"),
                               include_in_forecast=False))
    db.session.commit()
    f = svc.build_forecast()
    assert f["rows"][0]["opening"]["EGP"] == Decimal("300000")


def test_opening_override_pins_a_week_and_propagates_forward(app):
    mk_bank("EGP", 100000)
    approved(FixedWeeklyCost, description="Wages", weekday=1, amount=Decimal("1000"))
    weeks = svc.build_weeks()
    target = weeks[5]["week_id"]
    svc.set_opening_override(target, "EGP", Decimal("777000"), "tester", "actual bank statement")
    f = svc.build_forecast()
    assert f["rows"][5]["opening"]["EGP"] == Decimal("777000")
    assert f["rows"][5]["overridden"]["EGP"] is True
    assert f["rows"][5]["closing"]["EGP"] == Decimal("776000")
    assert f["rows"][6]["opening"]["EGP"] == Decimal("776000")   # rolls on from the override
    # weeks before the override are untouched
    assert f["rows"][0]["opening"]["EGP"] == Decimal("100000")


def test_clearing_an_override_restores_the_rollover(app):
    mk_bank("EGP", 100000)
    weeks = svc.build_weeks()
    svc.set_opening_override(weeks[2]["week_id"], "EGP", Decimal("5"))
    assert svc.build_forecast()["rows"][2]["opening"]["EGP"] == Decimal("5")
    svc.clear_opening_override(weeks[2]["week_id"], "EGP")
    assert svc.build_forecast()["rows"][2]["opening"]["EGP"] == Decimal("100000")


# ============================================================
# Currency
# ============================================================

def test_currencies_never_mix(app):
    mk_bank("EGP", 1000)
    mk_bank("USD", 2000)
    approved(CustomerCollection, customer="USD payer", amount=Decimal("500"),
             currency="USD", due_date=date(2026, 1, 7))
    f = svc.build_forecast()
    r = f["rows"][0]
    assert r["inflow"]["USD"] == Decimal("500")
    assert r["inflow"]["EGP"] == Decimal("0")
    assert r["closing"]["EGP"] == Decimal("1000")
    assert r["closing"]["USD"] == Decimal("2500")


def test_egp_equivalent_uses_the_fx_rate(app):
    set_setting("fx_rate", "48.25")
    mk_bank("EGP", 1000)
    mk_bank("USD", 100)
    f = svc.build_forecast()
    assert f["rows"][0]["opening_eqv"] == Decimal("1000") + Decimal("100") * Decimal("48.25")


def test_a_zero_or_broken_fx_rate_cannot_wipe_out_the_equivalent(app):
    set_setting("fx_rate", "0")
    assert svc.fx_rate() == Decimal(svc.DEFAULTS["fx_rate"])
    set_setting("fx_rate", "not a number")
    assert svc.fx_rate() == Decimal(svc.DEFAULTS["fx_rate"])


# ============================================================
# Workflow gating
# ============================================================

def test_only_approved_entries_reach_the_forecast_by_default(app):
    mk_bank("EGP", 0)
    approved(CustomerCollection, customer="Approved", amount=Decimal("100"),
             due_date=date(2026, 1, 7))
    approved(CustomerCollection, customer="Draft", amount=Decimal("999"),
             due_date=date(2026, 1, 7), status=ST_DRAFT)
    approved(CustomerCollection, customer="Submitted", amount=Decimal("555"),
             due_date=date(2026, 1, 7), status=ST_SUBMITTED)
    f = svc.build_forecast()
    assert f["rows"][0]["inflow"]["EGP"] == Decimal("100")
    assert f["rows"][0]["pending_in"]["EGP"] == Decimal("1554")


def test_include_pending_brings_unapproved_entries_into_the_balance(app):
    mk_bank("EGP", 0)
    approved(CustomerCollection, customer="Draft", amount=Decimal("999"),
             due_date=date(2026, 1, 7), status=ST_DRAFT)
    set_setting("include_pending", "1")
    f = svc.build_forecast()
    assert f["rows"][0]["inflow"]["EGP"] == Decimal("999")
    assert f["rows"][0]["closing"]["EGP"] == Decimal("999")


def test_cleared_and_cancelled_cheques_leave_the_forecast(app):
    mk_bank("EGP", 0)
    for state in ("issued", "presented", "cleared", "returned", "cancelled"):
        approved(ChequePayable, cheque_no=state, payee="P", amount=Decimal("100"),
                 due_date=date(2026, 1, 7), cheque_status=state)
    f = svc.build_forecast()
    assert f["rows"][0]["outflow"]["EGP"] == Decimal("200")   # issued + presented only


def test_certainty_weighting_applies_to_receipts_only(app):
    mk_bank("EGP", 0)
    approved(CustomerCollection, customer="Maybe", amount=Decimal("1000"),
             due_date=date(2026, 1, 7), certainty=60)
    approved(BankLoanInstalment, lender="CIB", amount=Decimal("1000"),
             due_date=date(2026, 1, 7))
    set_setting("apply_certainty", "1")
    f = svc.build_forecast()
    assert f["rows"][0]["inflow"]["EGP"] == Decimal("600")
    assert f["rows"][0]["outflow"]["EGP"] == Decimal("1000")   # costs are never discounted


def test_one_off_items_respect_their_own_direction(app):
    mk_bank("EGP", 0)
    approved(OneOffItem, direction="in", description="Grant", amount=Decimal("300"),
             due_date=date(2026, 1, 7))
    approved(OneOffItem, direction="out", description="Fine", amount=Decimal("120"),
             due_date=date(2026, 1, 7))
    r = svc.build_forecast()["rows"][0]
    assert r["inflow"]["EGP"] == Decimal("300")
    assert r["outflow"]["EGP"] == Decimal("120")
    assert r["closing"]["EGP"] == Decimal("180")


# ============================================================
# Conservation and precision
# ============================================================

def test_no_cash_is_lost_between_movements_and_the_weekly_totals(app):
    mk_bank("EGP", 0)
    mk_bank("USD", 0)
    approved(CustomerCollection, customer="A", amount=Decimal("1234.56"),
             due_date=date(2026, 4, 15))
    approved(AdhocInflow, source="B", amount=Decimal("9.99"), currency="USD",
             due_date=date(2026, 6, 2))
    approved(FixedMonthlyCost, description="Rent", day_of_month=7, amount=Decimal("333.33"))
    approved(FixedWeeklyCost, description="Wages", weekday=1, amount=Decimal("77.77"))
    approved(ChequePayable, cheque_no="1", payee="P", amount=Decimal("4000"),
             currency="USD", due_date=date(2026, 8, 3))
    f = svc.build_forecast()
    for c in ("EGP", "USD"):
        from_moves = sum((m["amount"] if m["direction"] == "in" else -m["amount"])
                         for m in f["movements"] if m["currency"] == c)
        from_weeks = sum((r["inflow"][c] - r["outflow"][c] for r in f["rows"]), Decimal("0"))
        assert from_moves == from_weeks
        assert f["rows"][-1]["closing"][c] == from_weeks   # opened at zero


def test_decimal_arithmetic_does_not_drift(app):
    """A hundred entries of 0.01 must total exactly 1.00, not 0.9999999."""
    mk_bank("EGP", 0)
    for i in range(100):
        approved(CustomerCollection, customer=f"C{i}", amount=Decimal("0.01"),
                 due_date=date(2026, 1, 7))
    f = svc.build_forecast()
    assert f["rows"][0]["inflow"]["EGP"] == Decimal("1.00")
    assert f["rows"][0]["closing"]["EGP"] == Decimal("1.00")


def test_rounding_is_half_up_at_two_places(app):
    assert svc.q2("2.345") == Decimal("2.35")
    assert svc.q2("2.344") == Decimal("2.34")
    assert svc.q2("-2.345") == Decimal("-2.35")


# ============================================================
# Golden scenario — every figure computed by hand
# ============================================================

def test_golden_six_week_scenario(app):
    """A worked example checked line by line against hand arithmetic.

    Horizon starts Sunday 4 Jan 2026 (week 0 = 4–10 Jan).
    Opening: EGP 100,000 and USD 10,000.

    EGP movements
      wk0  in   30,000  collection due Wed 7 Jan
      wk0  out   5,000  weekly wage every Monday (5 Jan)
      wk1  out   5,000  weekly wage (12 Jan)
      wk1  out  20,000  monthly rent, nominal Fri 16 Jan -> shifted to Sun 18 Jan (wk2)
      ...rent therefore lands in wk2, not wk1.
    """
    mk_bank("EGP", 100000)
    mk_bank("USD", 10000)
    approved(CustomerCollection, customer="Hospital", amount=Decimal("30000"),
             due_date=date(2026, 1, 7))                      # Wed, week 0
    approved(FixedWeeklyCost, description="Wages", weekday=0, amount=Decimal("5000"))
    approved(FixedMonthlyCost, description="Rent", day_of_month=16, amount=Decimal("20000"))
    approved(BankLoanInstalment, lender="Bank", amount=Decimal("2500"), currency="USD",
             due_date=date(2026, 1, 20))                     # Tue, week 2

    f = svc.build_forecast()
    rows = f["rows"]

    # 16 Jan 2026 is a Friday -> shifted forward to Sunday 18 Jan, which is week 2
    rent = [m for m in f["movements"] if m["description"].startswith("Rent")][0]
    assert rent["nominal"] == date(2026, 1, 16)
    assert rent["date"] == date(2026, 1, 18)
    assert rent["week"] == 2

    # week 0: 4-10 Jan.  Wage Monday 5 Jan.
    assert rows[0]["opening"]["EGP"] == Decimal("100000")
    assert rows[0]["inflow"]["EGP"] == Decimal("30000")
    assert rows[0]["outflow"]["EGP"] == Decimal("5000")
    assert rows[0]["closing"]["EGP"] == Decimal("125000")

    # week 1: 11-17 Jan.  Wage Monday 12 Jan only — rent has moved to week 2.
    assert rows[1]["opening"]["EGP"] == Decimal("125000")
    assert rows[1]["outflow"]["EGP"] == Decimal("5000")
    assert rows[1]["closing"]["EGP"] == Decimal("120000")

    # week 2: 18-24 Jan.  Wage 19 Jan + rent 18 Jan = 25,000 out.
    assert rows[2]["outflow"]["EGP"] == Decimal("25000")
    assert rows[2]["closing"]["EGP"] == Decimal("95000")

    # USD runs entirely separately: only the loan instalment in week 2.
    assert rows[0]["closing"]["USD"] == Decimal("10000")
    assert rows[1]["closing"]["USD"] == Decimal("10000")
    assert rows[2]["outflow"]["USD"] == Decimal("2500")
    assert rows[2]["closing"]["USD"] == Decimal("7500")

    # EGP equivalent of week 2 closing at the test rate of 50
    assert rows[2]["closing_eqv"] == Decimal("95000") + Decimal("7500") * Decimal("50")


def test_shortage_alert_fires_when_a_balance_goes_negative(app):
    mk_bank("EGP", 10000)
    approved(BankLoanInstalment, lender="Bank", amount=Decimal("25000"),
             due_date=date(2026, 1, 7))
    f = svc.build_forecast()
    assert f["rows"][0]["closing"]["EGP"] == Decimal("-15000")
    alerts = f["rows"][0]["alerts"]
    assert any(a["key"] == "alert_overdrawn" and a["currency"] == "EGP" for a in alerts)


def test_buffer_alert_fires_before_the_balance_goes_negative(app):
    set_setting("min_buffer_EGP", "50000")
    mk_bank("EGP", 100000)
    approved(BankLoanInstalment, lender="Bank", amount=Decimal("70000"),
             due_date=date(2026, 1, 7))
    alerts = svc.build_forecast()["rows"][0]["alerts"]
    assert any(a["key"] == "alert_below_buffer" for a in alerts)


def test_dashboard_reports_the_lowest_point_and_first_shortage(app):
    mk_bank("EGP", 50000)
    approved(BankLoanInstalment, lender="Bank", amount=Decimal("60000"),
             due_date=date(2026, 2, 10))            # week 5
    d = svc.dashboard(6)
    assert d["lowest"]["EGP"]["amount"] == Decimal("-10000")
    assert d["first_short"]["EGP"]["index"] == 5


# ============================================================
# Analysis
# ============================================================

def test_analysis_percentages_are_consistent(app):
    from models import RevenueType, CostCategory
    mk_bank("EGP", 0)
    rt = RevenueType(code="EQ", name_en="Equipment")
    ct = CostCategory(code="PR", name_en="Payroll")
    db.session.add_all([rt, ct])
    db.session.commit()
    approved(CustomerCollection, customer="A", amount=Decimal("80000"),
             due_date=date(2026, 1, 7), revenue_type_id=rt.id)
    approved(AdhocInflow, source="B", amount=Decimal("20000"), due_date=date(2026, 1, 8))
    approved(BankLoanInstalment, lender="Bank", amount=Decimal("25000"),
             due_date=date(2026, 1, 7), cost_category_id=ct.id)
    a = svc.analysis(from_index=0, to_index=0)
    assert a["revenue_total"] == Decimal("100000")
    assert a["cost_total"] == Decimal("25000")
    assert a["cost_ratio"] == Decimal("25")
    assert sum(r["pct_of_revenue"] for r in a["revenue"]) == Decimal("100")
    eq = next(r for r in a["revenue"] if r["name"] == "Equipment")
    assert eq["pct_of_revenue"] == Decimal("80")
    assert sum(c["pct_of_cost"] for c in a["costs"]) == Decimal("100")


def test_analysis_converts_usd_into_the_egp_equivalent(app):
    mk_bank("EGP", 0)
    approved(CustomerCollection, customer="USD", amount=Decimal("100"),
             currency="USD", due_date=date(2026, 1, 7))
    a = svc.analysis(from_index=0, to_index=0)
    assert a["revenue_total"] == Decimal("5000")     # 100 USD @ 50


# ============================================================
# Rolling trend forecast
# ============================================================

def test_history_window_is_contiguous_with_the_forward_horizon(app):
    hist = svc.history_weeks(6)
    fwd = svc.build_weeks()
    assert len(hist) == 6
    assert hist[-1]["end"] + timedelta(days=1) == fwd[0]["start"]
    assert hist[0]["start"] == fwd[0]["start"] - timedelta(days=42)


def test_least_squares_matches_hand_arithmetic(app):
    # y = 10, 20, 30, 40 -> slope exactly 10, intercept exactly 10
    slope, intercept = svc.least_squares([10, 20, 30, 40])
    assert slope == Decimal("10")
    assert intercept == Decimal("10")
    # a flat series has no trend
    assert svc.least_squares([7, 7, 7, 7])[0] == Decimal("0")
    # a falling series trends down
    assert svc.least_squares([100, 90, 80]) [0] == Decimal("-10")


def test_least_squares_on_an_irregular_series(app):
    """Worked by hand: x=0..4, y=2,4,5,4,5. mean x=2, mean y=4.
    Sxy = (-2)(-2)+(-1)(0)+(0)(1)+(1)(0)+(2)(1) = 4+0+0+0+2 = 6
    Sxx = 4+1+0+1+4 = 10  ->  slope 0.6, intercept 4 - 0.6*2 = 2.8
    """
    slope, intercept = svc.least_squares([2, 4, 5, 4, 5])
    assert slope == Decimal("0.6")
    assert intercept == Decimal("2.8")


def test_linear_projection_continues_the_line(app):
    # history 10,20,30,40 (indices 0-3) projects 50,60 at indices 4,5
    assert svc.project_series([10, 20, 30, 40], 2, "linear") == [Decimal("50"), Decimal("60")]


def test_projection_is_floored_at_zero(app):
    # a steeply falling line would go negative; receipts cannot
    out = svc.project_series([100, 60, 20], 3, "linear")
    assert out[0] == Decimal("0")        # index 3 -> 100 - 40*3 = -20 -> 0
    assert all(v >= 0 for v in out)


def test_average_method_returns_the_flat_mean(app):
    assert svc.project_series([10, 20, 30], 2, "average") == [Decimal("20"), Decimal("20")]


def test_short_window_falls_back_to_the_average(app):
    """Two points make a line but not a trend, so the average is used."""
    assert svc.project_series([10, 30], 2, "linear") == [Decimal("20"), Decimal("20")]
    assert svc.project_series([50], 2, "linear") == [Decimal("50"), Decimal("50")]
    assert svc.project_series([], 2, "linear") == [Decimal("0"), Decimal("0")]


def test_trend_forecast_projects_a_rising_revenue_line(app):
    """Six weekly collections rising by 1,000 a week project the line on.

    History weeks are the six before the horizon: the entries are dated
    into each of them, giving the series 10k, 11k, 12k, 13k, 14k, 15k.
    Least squares gives slope 1,000 and intercept 10,000, so weeks 6-11
    project 16k, 17k, 18k, 19k, 20k, 21k — total 111,000.
    """
    from models import RevenueType
    rt = RevenueType(code="EQ", name_en="Equipment")
    db.session.add(rt)
    db.session.commit()
    mk_bank("EGP", 0)
    hist = svc.history_weeks(6)
    for i, w in enumerate(hist):
        approved(CustomerCollection, customer="A", amount=Decimal(10000 + i * 1000),
                 due_date=w["start"] + timedelta(days=1), revenue_type_id=rt.id)
    tf = svc.trend_forecast(lookback=6, ahead=6, method="linear")
    row = next(r for r in tf["revenue"] if r["name"] == "Equipment")
    assert row["by"]["EGP"]["series"] == [Decimal(10000 + i * 1000) for i in range(6)]
    assert row["by"]["EGP"]["slope"] == Decimal("1000")
    assert row["by"]["EGP"]["projected"] == [Decimal(16000 + i * 1000) for i in range(6)]
    assert row["projected_eqv"] == Decimal("111000")
    assert row["avg_eqv"] == Decimal("12500")
    assert row["total_eqv"] == Decimal("75000")


def test_trend_variance_compares_booked_against_projected(app):
    from models import RevenueType
    rt = RevenueType(code="EQ", name_en="Equipment")
    db.session.add(rt)
    db.session.commit()
    mk_bank("EGP", 0)
    for w in svc.history_weeks(6):
        approved(CustomerCollection, customer="A", amount=Decimal("1000"),
                 due_date=w["start"] + timedelta(days=1), revenue_type_id=rt.id)
    # nothing booked forward at all
    tf = svc.trend_forecast(lookback=6, ahead=6, method="average")
    row = next(r for r in tf["revenue"] if r["name"] == "Equipment")
    assert row["projected_eqv"] == Decimal("6000")     # 1,000 a week, flat
    assert row["booked_eqv"] == Decimal("0")
    assert row["variance_eqv"] == Decimal("-6000")     # booked short of the trend

    # now book 4,000 into the first forward week
    approved(CustomerCollection, customer="B", amount=Decimal("4000"),
             due_date=svc.build_weeks()[0]["start"] + timedelta(days=1),
             revenue_type_id=rt.id)
    tf = svc.trend_forecast(lookback=6, ahead=6, method="average")
    row = next(r for r in tf["revenue"] if r["name"] == "Equipment")
    assert row["booked_eqv"] == Decimal("4000")
    assert row["variance_eqv"] == Decimal("-2000")


def test_trend_keeps_currencies_apart(app):
    from models import RevenueType
    rt = RevenueType(code="EQ", name_en="Equipment")
    db.session.add(rt)
    db.session.commit()
    mk_bank("EGP", 0)
    mk_bank("USD", 0)
    for w in svc.history_weeks(6):
        approved(CustomerCollection, customer="EGP payer", amount=Decimal("1000"),
                 due_date=w["start"] + timedelta(days=1), revenue_type_id=rt.id)
        approved(CustomerCollection, customer="USD payer", amount=Decimal("100"),
                 currency="USD", due_date=w["start"] + timedelta(days=1),
                 revenue_type_id=rt.id)
    tf = svc.trend_forecast(lookback=6, ahead=6, method="average")
    row = next(r for r in tf["revenue"] if r["name"] == "Equipment")
    assert row["by"]["EGP"]["avg"] == Decimal("1000")
    assert row["by"]["USD"]["avg"] == Decimal("100")
    # EGP equivalent at the test rate of 50: 1,000 + 100 * 50 = 6,000 a week
    assert row["avg_eqv"] == Decimal("6000")
    assert tf["position"][0]["inflow"]["EGP"] == Decimal("1000")
    assert tf["position"][0]["inflow"]["USD"] == Decimal("100")


def test_projected_position_rolls_forward_from_the_real_opening(app):
    mk_bank("EGP", 200000)
    for w in svc.history_weeks(6):
        approved(CustomerCollection, customer="A", amount=Decimal("5000"),
                 due_date=w["start"] + timedelta(days=1))
        approved(BankLoanInstalment, lender="Bank", amount=Decimal("8000"),
                 due_date=w["start"] + timedelta(days=1))
    tf = svc.trend_forecast(lookback=6, ahead=4, method="average")
    pos = tf["position"]
    assert pos[0]["opening"]["EGP"] == Decimal("200000")
    # flat 5,000 in and 8,000 out -> 3,000 a week decline
    for i, p in enumerate(pos):
        assert p["closing"]["EGP"] == Decimal(200000 - 3000 * (i + 1))
        assert p["closing"]["EGP"] == p["opening"]["EGP"] + p["inflow"]["EGP"] - p["outflow"]["EGP"]


def test_recurring_costs_appear_in_the_trend_history(app):
    """A fixed weekly cost has no past entries of its own — the engine
    must generate its historical occurrences to measure the trend."""
    from models import CostCategory
    cc = CostCategory(code="PR", name_en="Payroll")
    db.session.add(cc)
    db.session.commit()
    mk_bank("EGP", 0)
    approved(FixedWeeklyCost, description="Wages", weekday=1, amount=Decimal("9000"),
             cost_category_id=cc.id)
    tf = svc.trend_forecast(lookback=6, ahead=6, method="average")
    row = next(r for r in tf["costs"] if r["name"] == "Payroll")
    assert row["by"]["EGP"]["series"] == [Decimal("9000")] * 6
    assert row["projected_eqv"] == Decimal("54000")


# ============================================================
# Collections: type, customer number, weekly planner
# ============================================================

def test_collection_type_and_customer_number_are_stored(app):
    row = approved(CustomerCollection, customer_no="C-1044", customer="Hospital",
                   collection_type="down_payment", contract_ref="CT-9",
                   instalment_no=2, amount=Decimal("500"), due_date=date(2026, 1, 7))
    assert row.customer_no == "C-1044"
    assert row.collection_type == "down_payment"
    assert row.instalment_no == 2


def test_weekly_planner_groups_collections_by_week_and_type(app):
    weeks = svc.build_weeks()
    approved(CustomerCollection, customer="A", collection_type="instalment",
             amount=Decimal("100"), due_date=weeks[0]["start"] + timedelta(days=1))
    approved(CustomerCollection, customer="B", collection_type="instalment",
             amount=Decimal("250"), due_date=weeks[0]["start"] + timedelta(days=2))
    approved(CustomerCollection, customer="C", collection_type="down_payment",
             amount=Decimal("900"), due_date=weeks[0]["start"] + timedelta(days=3))
    approved(CustomerCollection, customer="D", collection_type="down_payment",
             amount=Decimal("40"), currency="USD",
             due_date=weeks[2]["start"] + timedelta(days=1))
    plan = svc.weekly_stream("collections", weeks)
    wk0 = plan["cells"][0]
    assert len(wk0["rows"]) == 3
    assert wk0["totals"]["EGP"] == Decimal("1250")
    assert wk0["groups"]["instalment"]["EGP"] == Decimal("350")
    assert wk0["groups"]["down_payment"]["EGP"] == Decimal("900")
    assert plan["cells"][2]["totals"]["USD"] == Decimal("40")
    assert plan["group_totals"]["down_payment"]["EGP"] == Decimal("900")
    assert plan["grand"]["EGP"] == Decimal("1250")


def test_weekly_planner_shows_drafts_that_the_forecast_excludes(app):
    """The planner is where entries are chased, so unapproved rows must
    be visible there even though they are kept out of the balance."""
    mk_bank("EGP", 0)
    weeks = svc.build_weeks()
    approved(CustomerCollection, customer="Draft", amount=Decimal("700"),
             due_date=weeks[0]["start"] + timedelta(days=1), status=ST_DRAFT)
    plan = svc.weekly_stream("collections", weeks)
    assert plan["cells"][0]["totals"]["EGP"] == Decimal("700")
    assert svc.build_forecast()["rows"][0]["inflow"]["EGP"] == Decimal("0")


# ============================================================
# Working week
# ============================================================

def test_saturday_thursday_week_treats_saturday_as_working(app):
    set_setting("work_week", "sat_thu")
    set_setting("weekend_rule", "next")
    assert svc.adjust_to_working_day(date(2026, 3, 7)) == date(2026, 3, 7)   # Sat works
    assert svc.adjust_to_working_day(date(2026, 3, 6)) == date(2026, 3, 7)   # Fri -> Sat


def test_analysis_window_excludes_weeks_outside_the_range(app):
    mk_bank("EGP", 0)
    approved(CustomerCollection, customer="Early", amount=Decimal("100"),
             due_date=date(2026, 1, 7))              # week 0
    approved(CustomerCollection, customer="Later", amount=Decimal("900"),
             due_date=date(2026, 3, 3))              # week 8
    assert svc.analysis(from_index=0, to_index=0)["revenue_total"] == Decimal("100")
    assert svc.analysis(from_index=8, to_index=8)["revenue_total"] == Decimal("900")
    assert svc.analysis(from_index=0, to_index=51)["revenue_total"] == Decimal("1000")
