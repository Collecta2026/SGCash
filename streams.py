"""Stream registry — the single description of every editable table.

Each entry declares the model, the direction of cash, the fields shown
on its editable page, and how the forecast engine should place its rows
into weeks.  Routes, forms, validation, permissions, exports and the
forecast engine all read from here, so a table is defined once and
behaves consistently everywhere.

`occurrence` tells the engine how a row becomes cash movements:
    "dated"   — one movement on `due_date`
    "weekly"  — recurs on `weekday` between effective_from/effective_to
    "monthly" — recurs on `day_of_month` between effective_from/effective_to
"""
from models import (BankLoanInstalment, SupplierInstalment, CustomerRefund,
                    ChequePayable, PettyCashReplenishment, FixedWeeklyCost,
                    FixedMonthlyCost, CustomerCollection, AdhocInflow, OneOffItem)

WEEKDAYS = [
    (0, "Monday", "الإثنين"), (1, "Tuesday", "الثلاثاء"), (2, "Wednesday", "الأربعاء"),
    (3, "Thursday", "الخميس"), (4, "Friday", "الجمعة"), (5, "Saturday", "السبت"),
    (6, "Sunday", "الأحد"),
]

PAYMENT_METHODS = [("transfer", "Bank transfer", "تحويل بنكي"),
                   ("cheque", "Cheque", "شيك"),
                   ("cash", "Cash", "نقدي"),
                   ("card", "Card", "بطاقة"),
                   ("lc", "Letter of credit", "اعتماد مستندي")]

COLLECTION_TYPES = [("instalment", "Contract instalment", "قسط تعاقدي"),
                    ("down_payment", "Contract down payment", "دفعة مقدمة تعاقدية"),
                    ("installation", "Installation instalment", "قسط التركيب"),
                    ("advance", "Advance / on account", "دفعة تحت الحساب"),
                    ("invoice", "Invoice settlement", "سداد فاتورة"),
                    ("retention", "Retention release", "الإفراج عن محتجزات")]

SUPPLIER_PAYMENT_TYPES = [("instalment", "Contract instalment", "قسط تعاقدي"),
                          ("deposit", "Deposit / advance to supplier", "دفعة مقدمة للمورد"),
                          ("final", "Final payment", "دفعة نهائية"),
                          ("lc", "Letter of credit settlement", "تسوية اعتماد مستندي"),
                          ("retention", "Retention payment", "سداد محتجزات"),
                          ("invoice", "Invoice settlement", "سداد فاتورة")]

CHEQUE_STATES = [("issued", "Issued", "صادر"),
                 ("presented", "Presented", "مقدم للصرف"),
                 ("cleared", "Cleared", "تم صرفه"),
                 ("returned", "Returned", "مرتد"),
                 ("cancelled", "Cancelled", "ملغي")]


def F(name, ftype, en, ar, **kw):
    d = dict(name=name, type=ftype, en=en, ar=ar, required=kw.pop("required", False))
    d.update(kw)
    return d


# Fields shared by every stream, appended after the stream-specific ones.
COMMON_TAIL = [
    F("currency", "currency", "Currency", "العملة", required=True),
    F("amount", "money", "Amount", "المبلغ", required=True),
    F("notes", "textarea", "Notes", "ملاحظات"),
]


STREAMS = {
    # ---------------- Inflows ----------------
    "collections": dict(
        model=CustomerCollection, direction="in", occurrence="dated",
        en="Customer Collections", ar="تحصيلات العملاء",
        en_one="collection", icon="↘",
        date_label_en="Expected date", date_label_ar="التاريخ المتوقع",
        fields=[
            F("customer_no", "text", "Customer no.", "رقم العميل"),
            F("customer", "text", "Customer", "العميل", required=True),
            F("collection_type", "select", "Collection type", "نوع التحصيل",
              options=COLLECTION_TYPES, required=True),
            F("contract_ref", "text", "Contract ref.", "رقم العقد"),
            F("instalment_no", "int", "Instalment no.", "رقم القسط"),
            F("invoice_ref", "text", "Invoice ref.", "رقم الفاتورة"),
            F("due_date", "date", "Expected date", "التاريخ المتوقع", required=True),
            F("method", "select", "Method", "طريقة السداد", options=PAYMENT_METHODS),
            F("revenue_type_id", "revenue_type", "Revenue type", "نوع الإيراد"),
            F("certainty", "pct", "Certainty %", "نسبة التأكد"),
        ] + COMMON_TAIL,
        weekly_planner=True,
        group_by="collection_type",
        group_options=COLLECTION_TYPES,
    ),
    "adhoc_in": dict(
        model=AdhocInflow, direction="in", occurrence="dated",
        en="Ad hoc Cash Inflows", ar="متحصلات نقدية متنوعة",
        en_one="inflow", icon="↘",
        date_label_en="Expected date", date_label_ar="التاريخ المتوقع",
        fields=[
            F("source", "text", "Source", "المصدر", required=True),
            F("ref", "text", "Reference", "المرجع"),
            F("due_date", "date", "Expected date", "التاريخ المتوقع", required=True),
            F("revenue_type_id", "revenue_type", "Revenue type", "نوع الإيراد"),
            F("certainty", "pct", "Certainty %", "نسبة التأكد"),
            F("description", "text", "Description", "البيان"),
        ] + COMMON_TAIL,
    ),

    # ---------------- Outflows ----------------
    "bank_loans": dict(
        model=BankLoanInstalment, direction="out", occurrence="dated",
        en="Bank Loan Instalments", ar="أقساط القروض البنكية",
        en_one="instalment", icon="↗",
        date_label_en="Due date", date_label_ar="تاريخ الاستحقاق",
        fields=[
            F("lender", "text", "Lender / bank", "البنك المقرض", required=True),
            F("facility_ref", "text", "Facility ref.", "رقم التسهيل"),
            F("instalment_no", "int", "Instalment no.", "رقم القسط"),
            F("due_date", "date", "Due date", "تاريخ الاستحقاق", required=True),
            F("principal", "money", "Principal", "أصل القرض"),
            F("interest", "money", "Interest", "الفوائد"),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
        ] + COMMON_TAIL,
        split_hint="principal+interest",
    ),
    "suppliers": dict(
        model=SupplierInstalment, direction="out", occurrence="dated",
        en="Supplier Payment Instalments", ar="أقساط مدفوعات الموردين",
        en_one="instalment", icon="↗",
        date_label_en="Due date", date_label_ar="تاريخ الاستحقاق",
        fields=[
            F("supplier_no", "text", "Supplier no.", "رقم المورد"),
            F("supplier", "text", "Supplier", "المورد", required=True),
            F("payment_type", "select", "Payment type", "نوع الدفعة",
              options=SUPPLIER_PAYMENT_TYPES, required=True),
            F("contract_ref", "text", "Contract ref.", "رقم العقد"),
            F("agreement_ref", "text", "Agreement ref.", "رقم الاتفاقية"),
            F("instalment_no", "int", "Instalment no.", "رقم القسط"),
            F("invoice_ref", "text", "Invoice ref.", "رقم الفاتورة"),
            F("due_date", "date", "Due date", "تاريخ الاستحقاق", required=True),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
        ] + COMMON_TAIL,
        weekly_planner=True,
        group_by="payment_type",
        group_options=SUPPLIER_PAYMENT_TYPES,
    ),
    "cheques": dict(
        model=ChequePayable, direction="out", occurrence="dated",
        en="Cheques Payable", ar="الشيكات المستحقة الدفع",
        en_one="cheque", icon="↗",
        date_label_en="Payable date", date_label_ar="تاريخ الاستحقاق",
        fields=[
            F("cheque_no", "text", "Cheque no.", "رقم الشيك", required=True),
            F("payee", "text", "Payee", "المستفيد", required=True),
            F("bank", "text", "Drawn on bank", "البنك المسحوب عليه"),
            F("issue_date", "date", "Issue date", "تاريخ الإصدار"),
            F("due_date", "date", "Payable date", "تاريخ الاستحقاق", required=True),
            F("cheque_status", "select", "Cheque state", "حالة الشيك", options=CHEQUE_STATES),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
        ] + COMMON_TAIL,
    ),
    "refunds": dict(
        model=CustomerRefund, direction="out", occurrence="dated",
        en="Customer Refunds", ar="مرتجعات ومستردات العملاء",
        en_one="refund", icon="↗",
        date_label_en="Due date", date_label_ar="تاريخ الاستحقاق",
        fields=[
            F("customer_no", "text", "Customer no.", "رقم العميل"),
            F("customer", "text", "Customer", "العميل", required=True),
            F("invoice_ref", "text", "Invoice ref.", "رقم الفاتورة"),
            F("reason", "text", "Reason", "السبب"),
            F("due_date", "date", "Due date", "تاريخ الاستحقاق", required=True),
            F("revenue_type_id", "revenue_type", "Against revenue type", "مقابل نوع الإيراد"),
        ] + COMMON_TAIL,
    ),
    "petty_cash": dict(
        model=PettyCashReplenishment, direction="out", occurrence="dated",
        en="Petty Cash Replenishments", ar="تغذية العهد النقدية",
        en_one="replenishment", icon="↗",
        date_label_en="Replenishment date", date_label_ar="تاريخ التغذية",
        fields=[
            F("employee_name", "text", "Employee", "اسم الموظف", required=True),
            F("department", "text", "Department", "الإدارة"),
            F("purpose", "text", "Purpose", "الغرض"),
            F("due_date", "date", "Replenishment date", "تاريخ التغذية", required=True),
            F("float_limit", "money", "Float limit", "حد العهدة"),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
        ] + COMMON_TAIL,
    ),
    "fixed_weekly": dict(
        model=FixedWeeklyCost, direction="out", occurrence="weekly",
        en="Fixed Weekly Costs", ar="التكاليف الأسبوعية الثابتة",
        en_one="weekly cost", icon="↻",
        date_label_en="Weekday", date_label_ar="يوم الأسبوع",
        fields=[
            F("description", "text", "Description", "البيان", required=True),
            F("weekday", "weekday", "Weekday", "يوم الأسبوع", required=True),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
            F("effective_from", "date", "Effective from", "ساري من"),
            F("effective_to", "date", "Effective to", "ساري حتى"),
        ] + COMMON_TAIL,
    ),
    "fixed_monthly": dict(
        model=FixedMonthlyCost, direction="out", occurrence="monthly",
        en="Fixed Monthly Costs", ar="التكاليف الشهرية الثابتة",
        en_one="monthly cost", icon="↻",
        date_label_en="Day of month", date_label_ar="يوم من الشهر",
        fields=[
            F("description", "text", "Description", "البيان", required=True),
            F("day_of_month", "int", "Day of month", "يوم من الشهر", required=True, min=1, max=31),
            F("cost_category_id", "cost_category", "Cost category", "بند التكلفة"),
            F("effective_from", "date", "Effective from", "ساري من"),
            F("effective_to", "date", "Effective to", "ساري حتى"),
        ] + COMMON_TAIL,
    ),

    # ---------------- Either direction ----------------
    "one_off": dict(
        model=OneOffItem, direction="both", occurrence="dated",
        en="One-off Items", ar="بنود غير متكررة",
        en_one="item", icon="◆",
        date_label_en="Date", date_label_ar="التاريخ",
        fields=[
            F("direction", "direction", "In / Out", "وارد / صادر", required=True),
            F("description", "text", "Description", "البيان", required=True),
            F("counterparty", "text", "Counterparty", "الطرف الآخر"),
            F("due_date", "date", "Date", "التاريخ", required=True),
            F("revenue_type_id", "revenue_type", "Revenue type (if in)", "نوع الإيراد (إن كان وارداً)"),
            F("cost_category_id", "cost_category", "Cost category (if out)", "بند التكلفة (إن كان صادراً)"),
        ] + COMMON_TAIL,
    ),
}

#: Display order on menus and reports.
STREAM_ORDER = ["collections", "adhoc_in", "one_off", "bank_loans", "suppliers",
                "cheques", "refunds", "petty_cash", "fixed_weekly", "fixed_monthly"]

INFLOW_STREAMS = [k for k in STREAM_ORDER if STREAMS[k]["direction"] in ("in", "both")]
OUTFLOW_STREAMS = [k for k in STREAM_ORDER if STREAMS[k]["direction"] in ("out", "both")]


def stream(key):
    s = STREAMS.get(key)
    if not s:
        raise KeyError(f"Unknown stream: {key}")
    return s


def label(key, lang="en"):
    s = stream(key)
    return s["ar"] if lang == "ar" else s["en"]


def field_label(f, lang="en"):
    return f["ar"] if lang == "ar" and f.get("ar") else f["en"]
