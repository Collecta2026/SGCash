"""Scientific Gate — Cash Flow Budgeting System.

Database models.  Architecture mirrors Collecta: a single SQLAlchemy
`db` object, a `Setting` key/value store, users with roles enforced by
`permissions.py`, an `Approval` queue and an `AuditLog`.

Every forecast line lives in its own table so each can be edited
independently, but they all share `StreamBase`, which carries the
fields the forecast engine needs: currency, amount, workflow status and
the authorisation trail.  That keeps eleven editable tables and exactly
one set of arithmetic.
"""
from datetime import date, datetime
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

MONEY = db.Numeric(18, 2)
RATE = db.Numeric(14, 6)

CURRENCIES = ("EGP", "USD")

# Workflow states shared by every stream table.
ST_DRAFT = "draft"
ST_SUBMITTED = "submitted"
ST_APPROVED = "approved"
ST_REJECTED = "rejected"
ST_CANCELLED = "cancelled"
ST_SETTLED = "settled"

STATUSES = (ST_DRAFT, ST_SUBMITTED, ST_APPROVED, ST_REJECTED, ST_CANCELLED, ST_SETTLED)
#: States whose amounts are committed cash in the forecast.
COMMITTED_STATUSES = (ST_APPROVED, ST_SETTLED)
#: States whose amounts are pending authorisation (shown separately).
PENDING_STATUSES = (ST_DRAFT, ST_SUBMITTED)


# ============================================================
# Core: users, settings, approvals, audit
# ============================================================

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(160))
    email = db.Column(db.String(200))
    role = db.Column(db.String(40), nullable=False, default="data_entry")
    lang = db.Column(db.String(2), default="en")
    active = db.Column(db.Boolean, default=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    # Flask-Login
    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    @property
    def is_admin(self):
        return self.role == "admin"

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw or "")

    @property
    def display(self):
        return self.full_name or self.username


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default="")


def get_setting(key, default=""):
    row = db.session.get(Setting, key)
    if row is None or row.value is None or row.value == "":
        return default
    return row.value


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = "" if value is None else str(value)
    db.session.commit()


class RolePermission(db.Model):
    __tablename__ = "role_permissions"
    role = db.Column(db.String(40), primary_key=True)
    cap = db.Column(db.String(80), primary_key=True)
    allowed = db.Column(db.Boolean, default=False, nullable=False)


class Approval(db.Model):
    """One authorisation request against one stream row."""
    __tablename__ = "approvals"
    id = db.Column(db.Integer, primary_key=True)
    stream = db.Column(db.String(40), nullable=False)
    row_id = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(30), nullable=False, default="entry")
    summary = db.Column(db.String(400))
    currency = db.Column(db.String(3))
    amount = db.Column(MONEY)
    requester = db.Column(db.String(160))
    requester_id = db.Column(db.Integer)
    status = db.Column(db.String(12), nullable=False, default="pending")
    approver = db.Column(db.String(160))
    reason = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, default=datetime.now)
    decided_at = db.Column(db.DateTime)


class AuditLog(db.Model):
    """Every change anyone makes, kept for the audit trail report."""
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(160))
    actor_role = db.Column(db.String(40))
    action = db.Column(db.String(80))
    stream = db.Column(db.String(40))
    row_id = db.Column(db.Integer)
    target = db.Column(db.String(160))
    detail = db.Column(db.Text)
    at = db.Column(db.DateTime, default=datetime.now)


class WeekSignoff(db.Model):
    """The Account Manager's confirmation that a week's cash flow is reviewed.

    Confirming a week is what releases the weekly cash review to the MD
    and CFO, so the record of who confirmed it, and when, is part of the
    audit trail.
    """
    __tablename__ = "week_signoffs"
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.String(12), unique=True, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="confirmed")
    confirmed_by = db.Column(db.String(160))
    confirmed_role = db.Column(db.String(40))
    confirmed_at = db.Column(db.DateTime, default=datetime.now)
    notes = db.Column(db.String(1000))
    review_sent_at = db.Column(db.DateTime)
    review_sent_to = db.Column(db.String(600))


class NotificationLog(db.Model):
    """Every automated email the system sends, and whether it landed."""
    __tablename__ = "notification_log"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False)   # input_request | weekly_review
    week_id = db.Column(db.String(12))
    recipient = db.Column(db.String(200))
    subject = db.Column(db.String(300))
    ok = db.Column(db.Boolean, default=False, nullable=False)
    error = db.Column(db.String(500))
    triggered_by = db.Column(db.String(160))
    at = db.Column(db.DateTime, default=datetime.now)


# ============================================================
# Reference data
# ============================================================

class RevenueType(db.Model):
    """Revenue analysis categories (e.g. Equipment sales, Service contracts)."""
    __tablename__ = "revenue_types"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name_en = db.Column(db.String(160), nullable=False)
    name_ar = db.Column(db.String(160))
    active = db.Column(db.Boolean, default=True, nullable=False)
    sort = db.Column(db.Integer, default=0)


class CostCategory(db.Model):
    """Cost analysis categories (e.g. Payroll, Rent, Customs duty)."""
    __tablename__ = "cost_categories"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    name_en = db.Column(db.String(160), nullable=False)
    name_ar = db.Column(db.String(160))
    group_key = db.Column(db.String(40), default="operating")
    active = db.Column(db.Boolean, default=True, nullable=False)
    sort = db.Column(db.Integer, default=0)


class BankAccount(db.Model):
    """A bank or cash account holding an opening balance in one currency."""
    __tablename__ = "bank_accounts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(10), default="bank")     # bank | wallet | cash
    sort = db.Column(db.Integer, default=100)
    bank = db.Column(db.String(160))
    account_no = db.Column(db.String(80))
    currency = db.Column(db.String(3), nullable=False, default="EGP")
    balance = db.Column(MONEY, nullable=False, default=0)
    as_at = db.Column(db.Date, default=date.today)
    overdraft_limit = db.Column(MONEY, default=0)
    include_in_forecast = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.String(300))
    updated_by = db.Column(db.String(160))
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class OpeningOverride(db.Model):
    """Manually pinned opening balance for one week and currency.

    When present it replaces the balance rolled over from the prior week,
    for that currency, from that week forward.
    """
    __tablename__ = "opening_overrides"
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.String(10), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    opening = db.Column(MONEY, nullable=False, default=0)
    reason = db.Column(db.String(300))
    set_by = db.Column(db.String(160))
    set_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (db.UniqueConstraint("week_id", "currency", name="uq_opening_week_ccy"),)


# ============================================================
# Shared stream columns
# ============================================================

class StreamBase:
    """Columns every forecast line carries.

    `amount` is always positive; direction comes from the stream
    definition (or, for the one-off table, from its own `direction`
    column).  `due_date` is the date the cash is expected to move.
    """
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(80))
    description = db.Column(db.String(300))
    currency = db.Column(db.String(3), nullable=False, default="EGP")
    amount = db.Column(MONEY, nullable=False, default=0)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(12), nullable=False, default="draft")
    notes = db.Column(db.String(500))

    created_by = db.Column(db.String(160))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_by = db.Column(db.String(160))
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    reviewed_by = db.Column(db.String(160))
    reviewed_at = db.Column(db.DateTime)
    approved_by = db.Column(db.String(160))
    approved_at = db.Column(db.DateTime)


# ------------------------------------------------------------
# Outflow streams
# ------------------------------------------------------------

class BankLoanInstalment(StreamBase, db.Model):
    """Scheduled instalments on bank loans and facilities."""
    __tablename__ = "bank_loan_instalments"
    lender = db.Column(db.String(160), nullable=False)
    facility_ref = db.Column(db.String(80))
    instalment_no = db.Column(db.Integer, default=1)
    principal = db.Column(MONEY, default=0)
    interest = db.Column(MONEY, default=0)
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


class SupplierInstalment(StreamBase, db.Model):
    """Scheduled repayment instalments to suppliers."""
    __tablename__ = "supplier_instalments"
    supplier_no = db.Column(db.String(40))
    supplier = db.Column(db.String(160), nullable=False)
    payment_type = db.Column(db.String(20), default="instalment")
    agreement_ref = db.Column(db.String(80))
    contract_ref = db.Column(db.String(80))
    invoice_ref = db.Column(db.String(80))
    instalment_no = db.Column(db.Integer, default=1)
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


class CustomerRefund(StreamBase, db.Model):
    """Refunds payable back to customers."""
    __tablename__ = "customer_refunds"
    customer_no = db.Column(db.String(40))
    customer = db.Column(db.String(160), nullable=False)
    invoice_ref = db.Column(db.String(80))
    reason = db.Column(db.String(300))
    revenue_type_id = db.Column(db.Integer, db.ForeignKey("revenue_types.id"))


class ChequePayable(StreamBase, db.Model):
    """Cheques issued, forecast on their payable (due) date."""
    __tablename__ = "cheques_payable"
    cheque_no = db.Column(db.String(60), nullable=False)
    payee = db.Column(db.String(160), nullable=False)
    bank = db.Column(db.String(160))
    issue_date = db.Column(db.Date)
    # StreamBase.due_date is the payable / presentation date
    cheque_status = db.Column(db.String(20), default="issued")  # issued/presented/cleared/returned/cancelled
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


class PettyCashReplenishment(StreamBase, db.Model):
    """Petty cash float replenishments held by named employees."""
    __tablename__ = "petty_cash"
    employee_name = db.Column(db.String(160), nullable=False)
    department = db.Column(db.String(120))
    purpose = db.Column(db.String(300))
    float_limit = db.Column(MONEY, default=0)
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


class FixedWeeklyCost(StreamBase, db.Model):
    """A cost that recurs on a chosen weekday (wages, fuel, couriers)."""
    __tablename__ = "fixed_weekly_costs"
    weekday = db.Column(db.Integer, nullable=False, default=0)  # 0=Mon .. 6=Sun
    effective_from = db.Column(db.Date)
    effective_to = db.Column(db.Date)
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


class FixedMonthlyCost(StreamBase, db.Model):
    """A cost that recurs on a chosen day of the month (rent, salaries)."""
    __tablename__ = "fixed_monthly_costs"
    day_of_month = db.Column(db.Integer, nullable=False, default=1)
    effective_from = db.Column(db.Date)
    effective_to = db.Column(db.Date)
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


# ------------------------------------------------------------
# Inflow streams
# ------------------------------------------------------------

class CustomerCollection(StreamBase, db.Model):
    """Expected collections from customers, planned week by week.

    `collection_type` separates a scheduled instalment from a down
    payment, an advance, a one-off invoice settlement or a retention
    release, so the weekly planner and the revenue analysis can tell
    them apart.
    """
    __tablename__ = "customer_collections"
    customer_no = db.Column(db.String(40))          # customer account number, where known
    customer = db.Column(db.String(160), nullable=False)
    collection_type = db.Column(db.String(20), default="instalment")
    contract_ref = db.Column(db.String(80))
    instalment_no = db.Column(db.Integer)
    invoice_ref = db.Column(db.String(80))
    method = db.Column(db.String(40), default="transfer")
    certainty = db.Column(db.Integer, default=100)  # 0-100 %
    revenue_type_id = db.Column(db.Integer, db.ForeignKey("revenue_types.id"))


class AdhocInflow(StreamBase, db.Model):
    """Non-trading or irregular cash in (grants, asset sales, shareholder funds)."""
    __tablename__ = "adhoc_inflows"
    source = db.Column(db.String(160), nullable=False)
    certainty = db.Column(db.Integer, default=100)
    revenue_type_id = db.Column(db.Integer, db.ForeignKey("revenue_types.id"))


# ------------------------------------------------------------
# Either direction
# ------------------------------------------------------------

class OneOffItem(StreamBase, db.Model):
    """A single dated receipt or payment that fits no other table."""
    __tablename__ = "one_off_items"
    direction = db.Column(db.String(3), nullable=False, default="out")  # 'in' | 'out'
    counterparty = db.Column(db.String(160))
    revenue_type_id = db.Column(db.Integer, db.ForeignKey("revenue_types.id"))
    cost_category_id = db.Column(db.Integer, db.ForeignKey("cost_categories.id"))


# ============================================================
# Helpers
# ============================================================

def D(v):
    """Coerce anything to Decimal, safely."""
    if v is None or v == "":
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))
