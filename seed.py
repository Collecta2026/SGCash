"""First-run initialisation: administrator, settings, reference data.

`demo=True` additionally loads a small worked example so the client can
see the forecast populated before entering their own figures.
"""
from datetime import date, timedelta
from decimal import Decimal

from models import (db, User, RevenueType, CostCategory, BankAccount,
                    set_setting, ST_APPROVED, ST_SUBMITTED)
import permissions as perms
import services as svc

REVENUE_TYPES = [
    ("EQUIP", "Equipment sales", "مبيعات أجهزة", 10),
    ("PARTS", "Spare parts & accessories", "قطع غيار وملحقات", 20),
    ("SERVICE", "Service & maintenance contracts", "عقود الصيانة", 30),
    ("INSTALL", "Installation & commissioning", "التركيب والتشغيل", 40),
    ("CONSUM", "Consumables", "مستلزمات", 50),
    ("TENDER", "Tender & project income", "إيرادات المناقصات والمشروعات", 60),
    ("OTHER", "Other income", "إيرادات أخرى", 90),
]

COST_CATEGORIES = [
    ("PAYROLL", "Payroll & wages", "الرواتب والأجور", "operating", 10),
    ("RENT", "Rent & utilities", "الإيجار والمرافق", "operating", 20),
    ("SUPPLIER", "Supplier payments", "مدفوعات الموردين", "trading", 30),
    ("CUSTOMS", "Customs & clearance", "الجمارك والتخليص", "trading", 40),
    ("FREIGHT", "Freight & logistics", "الشحن والنقل", "trading", 50),
    ("BANKCHG", "Bank charges & interest", "مصاريف وفوائد بنكية", "finance", 60),
    ("LOAN", "Loan repayments", "سداد القروض", "finance", 70),
    ("TAX", "Taxes & social insurance", "الضرائب والتأمينات", "statutory", 80),
    ("MARKET", "Marketing & tenders", "التسويق والمناقصات", "operating", 90),
    ("VEHICLE", "Vehicles & travel", "السيارات والانتقالات", "operating", 100),
    ("PETTY", "Petty cash", "المصروفات النثرية", "operating", 110),
    ("PROF", "Professional fees", "أتعاب مهنية", "operating", 120),
    ("OTHER", "Other operating costs", "مصروفات تشغيلية أخرى", "operating", 130),
]


def initialise(username="admin", password="", org=None, lang="en", demo=False):
    if org:
        set_setting("org_name", org)
    set_setting("product_name", svc.DEFAULT_PRODUCT)
    set_setting("default_lang", lang if lang in ("en", "ar") else "en")
    for k, v in svc.DEFAULTS.items():
        if k in ("org_name", "product_name", "default_lang"):
            continue
        from models import get_setting
        if get_setting(k, "") == "":
            set_setting(k, v)

    perms.seed_matrix(force=True)
    seed_reference()
    seed_accounts()

    if User.query.filter_by(username=username).first() is None:
        u = User(username=username, full_name="Super Administrator",
                 role="super_admin", lang=lang)
        u.set_password(password or "changeme123")
        db.session.add(u)
        db.session.commit()

    if demo:
        seed_demo()
    db.session.commit()


#: The accounts Scientific Gate actually holds cash in. Seeded at zero so the
#: client types in the real balances; the demo data fills them in.
STANDARD_ACCOUNTS = [
    ("NBE — current account", "National Bank of Egypt", "EGP", "bank", 10),
    ("NBE — USD account", "National Bank of Egypt", "USD", "bank", 20),
    ("CIB — current account", "Commercial International Bank", "EGP", "bank", 30),
    ("CIB — USD account", "Commercial International Bank", "USD", "bank", 40),
    ("InstaPay", "InstaPay", "EGP", "wallet", 50),
    ("Vodafone Cash", "Vodafone Cash", "EGP", "wallet", 60),
    ("Head office cash", "", "EGP", "cash", 70),
]


def seed_accounts():
    """Create the standard accounts once, at zero. Never touches a balance."""
    from datetime import date as _date
    for name, bank, ccy, kind, sort in STANDARD_ACCOUNTS:
        if BankAccount.query.filter_by(name=name).first() is None:
            db.session.add(BankAccount(name=name, bank=bank or None, currency=ccy,
                                       kind=kind, sort=sort, balance=Decimal("0"),
                                       as_at=_date.today(), include_in_forecast=True))
    db.session.commit()


def seed_reference():
    for code, en, ar, sort in REVENUE_TYPES:
        if RevenueType.query.filter_by(code=code).first() is None:
            db.session.add(RevenueType(code=code, name_en=en, name_ar=ar, sort=sort))
    for code, en, ar, grp, sort in COST_CATEGORIES:
        if CostCategory.query.filter_by(code=code).first() is None:
            db.session.add(CostCategory(code=code, name_en=en, name_ar=ar,
                                        group_key=grp, sort=sort))
    db.session.commit()


def seed_demo():
    """A small, clearly-labelled worked example. Safe to delete in the app."""
    from models import (CustomerCollection, BankLoanInstalment, SupplierInstalment,
                        ChequePayable, PettyCashReplenishment, FixedWeeklyCost,
                        FixedMonthlyCost, AdhocInflow)
    if CustomerCollection.query.first() is not None:
        return
    today = date.today()
    rev = {r.code: r.id for r in RevenueType.query.all()}
    cost = {c.code: c.id for c in CostCategory.query.all()}

    demo_balances = {
        "NBE — current account": ("1450000.00", "2011-0099-4"),
        "NBE — USD account": ("42000.00", "1011-0099-USD"),
        "CIB — current account": ("2450000.00", "100-2233-9"),
        "CIB — USD account": ("43000.00", "100-2233-USD"),
        "InstaPay": ("86000.00", "0100-555-8842"),
        "Vodafone Cash": ("34000.00", "0106-777-2190"),
        "Head office cash": ("120000.00", ""),
    }
    for name, (amount, acc_no) in demo_balances.items():
        acct = BankAccount.query.filter_by(name=name).first()
        if acct is not None:
            acct.balance = Decimal(amount)
            acct.account_no = acc_no or None
            acct.as_at = today
            if name == "CIB — current account":
                acct.overdraft_limit = Decimal("1000000.00")
    db.session.commit()

    def mk(model, **kw):
        kw.setdefault("status", ST_APPROVED)
        kw.setdefault("created_by", "Demo data")
        kw.setdefault("approved_by", "Demo data")
        db.session.add(model(**kw))

    for i, (no, cust, amt, wk, rt, ctype, inst) in enumerate([
            ("C-1001", "Ministry of Health — Cairo", "1850000", 1, "TENDER", "down_payment", None),
            ("C-1090", "Ain Shams University Hospital", "540000", 2, "INSTALL", "installation", 1),
            ("C-1044", "Al Salam International Hospital", "620000", 2, "EQUIP", "instalment", 3),
            ("C-1078", "Dar Al Fouad Hospital", "240000", 3, "SERVICE", "invoice", None),
            ("C-1112", "Cleopatra Hospitals Group", "410000", 4, "PARTS", "instalment", 2),
            ("C-1150", "As-Salam Medical Centre", "175000", 5, "CONSUM", "advance", None)]):
        mk(CustomerCollection, customer_no=no, customer=cust, amount=Decimal(amt),
           currency="EGP", due_date=today + timedelta(days=7 * wk + 2),
           revenue_type_id=rev.get(rt), collection_type=ctype, instalment_no=inst,
           contract_ref=f"CT-{1400 + i}" if ctype == "instalment" else None,
           certainty=90 - i * 5, method="transfer", invoice_ref=f"INV-{2600 + i}")

    mk(CustomerCollection, customer_no="C-2201", customer="Gulf Medical Trading (export)",
       amount=Decimal("45000"), currency="USD", due_date=today + timedelta(days=24),
       revenue_type_id=rev.get("EQUIP"), collection_type="down_payment",
       certainty=80, method="transfer", invoice_ref="INV-EXP-118")

    # Eight weeks of history so the rolling trend report has something to measure.
    history = [
        ("C-1044", "Al Salam International Hospital", "480000", "EQUIP", "instalment", 1),
        ("C-1112", "Cleopatra Hospitals Group", "330000", "PARTS", "instalment", 1),
        ("C-1078", "Dar Al Fouad Hospital", "215000", "SERVICE", "invoice", None),
        ("C-1001", "Ministry of Health — Cairo", "905000", "TENDER", "down_payment", None),
        ("C-1150", "As-Salam Medical Centre", "160000", "CONSUM", "advance", None),
        ("C-1044", "Al Salam International Hospital", "505000", "EQUIP", "instalment", 2),
        ("C-1078", "Dar Al Fouad Hospital", "228000", "SERVICE", "invoice", None),
        ("C-1112", "Cleopatra Hospitals Group", "352000", "PARTS", "instalment", 2),
    ]
    for i, (no, cust, amt, rt, ctype, inst) in enumerate(history):
        mk(CustomerCollection, customer_no=no, customer=cust, amount=Decimal(amt),
           currency="EGP", due_date=today - timedelta(days=7 * (8 - i) - 1),
           revenue_type_id=rev.get(rt), collection_type=ctype, instalment_no=inst,
           certainty=100, method="transfer", invoice_ref=f"INV-{2500 + i}",
           status="settled")

    mk(AdhocInflow, source="Sale of delivery vehicle", amount=Decimal("310000"),
       currency="EGP", due_date=today + timedelta(days=16), revenue_type_id=rev.get("OTHER"),
       description="Disposal of 2019 van", certainty=100)

    for n in range(1, 7):
        mk(BankLoanInstalment, lender="CIB", facility_ref="TL-2024-08", instalment_no=n,
           amount=Decimal("185000"), principal=Decimal("150000"), interest=Decimal("35000"),
           currency="EGP", due_date=_add_months(today.replace(day=25), n - 1),
           cost_category_id=cost.get("LOAN"))

    for n in range(1, 4):
        mk(SupplierInstalment, supplier_no="S-204", supplier="Siemens Healthineers ME",
           payment_type=("deposit" if n == 1 else "instalment"),
           contract_ref="SC-2025-11", agreement_ref="SUP-2025-3",
           instalment_no=n, amount=Decimal("62000"), currency="USD",
           due_date=_add_months(today.replace(day=10), n), cost_category_id=cost.get("SUPPLIER"),
           status=ST_SUBMITTED if n == 3 else ST_APPROVED)
    mk(SupplierInstalment, supplier_no="S-118", supplier="Medical Supplies Egypt",
       payment_type="final", contract_ref="SC-2025-04", amount=Decimal("430000"),
       currency="EGP", due_date=today + timedelta(days=26),
       cost_category_id=cost.get("SUPPLIER"))
    mk(SupplierInstalment, supplier_no="S-330", supplier="Nile Freight Services",
       payment_type="invoice", amount=Decimal("96000"), currency="EGP",
       due_date=today + timedelta(days=12), cost_category_id=cost.get("FREIGHT"))

    for i, (no, payee, days, amt) in enumerate([
            ("000451", "Egyptian Customs Authority", 9, "480000"),
            ("000452", "Nile Freight Services", 18, "96000"),
            ("000453", "Medical Supplies Egypt", 33, "255000")]):
        mk(ChequePayable, cheque_no=no, payee=payee, bank="CIB", currency="EGP",
           amount=Decimal(amt), issue_date=today, due_date=today + timedelta(days=days),
           cheque_status="issued", cost_category_id=cost.get("CUSTOMS" if i == 0 else "SUPPLIER"))

    mk(PettyCashReplenishment, employee_name="Mona Abdel Rahman", department="Service",
       purpose="Engineer travel float", amount=Decimal("25000"), currency="EGP",
       due_date=today + timedelta(days=5), float_limit=Decimal("30000"),
       cost_category_id=cost.get("PETTY"))
    mk(PettyCashReplenishment, employee_name="Hossam Fathy", department="Logistics",
       purpose="Clearance incidentals", amount=Decimal("18000"), currency="EGP",
       due_date=today + timedelta(days=19), float_limit=Decimal("20000"),
       cost_category_id=cost.get("PETTY"))

    mk(FixedWeeklyCost, description="Weekly wages — warehouse & drivers", weekday=3,
       amount=Decimal("64000"), currency="EGP", cost_category_id=cost.get("PAYROLL"))
    mk(FixedWeeklyCost, description="Fuel & vehicle running", weekday=0,
       amount=Decimal("18500"), currency="EGP", cost_category_id=cost.get("VEHICLE"))

    mk(FixedMonthlyCost, description="Office rent — Nasr City", day_of_month=1,
       amount=Decimal("145000"), currency="EGP", cost_category_id=cost.get("RENT"))
    mk(FixedMonthlyCost, description="Salaries — staff payroll", day_of_month=27,
       amount=Decimal("1180000"), currency="EGP", cost_category_id=cost.get("PAYROLL"))
    mk(FixedMonthlyCost, description="Social insurance & payroll tax", day_of_month=15,
       amount=Decimal("218000"), currency="EGP", cost_category_id=cost.get("TAX"))
    mk(FixedMonthlyCost, description="Utilities & communications", day_of_month=20,
       amount=Decimal("46000"), currency="EGP", cost_category_id=cost.get("RENT"))

    db.session.commit()


def _add_months(d, n):
    import calendar
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
