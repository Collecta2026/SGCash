"""Scheme of delegation — roles, capabilities and the editable matrix.

Every editable table carries five capabilities: view, enter, edit,
delete and approve.  The matrix is seeded with a sensible default on
first run and is then editable in the admin console, so Scientific
Gate's own scheme of delegation can be set without touching code.

Two rules are enforced in code and cannot be granted away:
  * nobody approves a row they entered themselves;
  * the admin console belongs to the System Administrator alone.

Menus do not hide what a role cannot do — they dim it and say why, so
people can see the shape of the system and ask for access.
"""
from models import db, RolePermission, get_setting, set_setting
from streams import STREAM_ORDER, STREAMS

ROLES = [
    ("admin", "System Administrator", "مدير النظام"),
    ("md", "Managing Director", "العضو المنتدب"),
    ("cfo", "Chief Financial Officer", "المدير المالي"),
    ("account_manager", "Finance Manager", "المدير المالي التنفيذي"),
    ("credit_control", "Credit Control", "متابعة التحصيل"),
    ("sales_admin", "Sales Administrator", "إدارة المبيعات"),
    ("viewer", "Viewer (read only)", "مشاهدة فقط"),
]
ROLE_KEYS = [r[0] for r in ROLES]
ROLE_LABELS = {r[0]: r[1] for r in ROLES}
ROLE_LABELS_AR = {r[0]: r[2] for r in ROLES}

#: Roles that exist only to key in their own figures. For these the menu
#: HIDES everything they cannot use rather than dimming it — they should not
#: see the shape of a system they have no part in.
ENTRY_ONLY_ROLES = {"credit_control", "sales_admin"}

ACTIONS = [
    ("view", "View", "عرض"),
    ("enter", "Enter", "إدخال"),
    ("edit", "Edit", "تعديل"),
    ("delete", "Delete", "حذف"),
    ("approve", "Review & approve", "مراجعة واعتماد"),
]
ACTION_KEYS = [a[0] for a in ACTIONS]

# --- Core (non-stream) capabilities -------------------------------------
CORE_CAPS = [
    ("dashboard", "Dashboard", "لوحة المتابعة"),
    ("forecast", "Weekly forecast", "التدفق الأسبوعي"),
    ("reports", "Reports & charts", "التقارير والرسوم"),
    ("trend", "Rolling trend forecast", "التوقعات حسب الاتجاه"),
    ("banks_view", "View bank balances", "عرض أرصدة البنوك"),
    ("banks_edit", "Maintain bank balances", "تعديل أرصدة البنوك"),
    ("opening_edit", "Edit opening balances", "تعديل الرصيد الافتتاحي"),
    ("imports", "Upload data files", "رفع ملفات البيانات"),
    ("exports", "Export & print reports", "تصدير وطباعة التقارير"),
    ("approvals", "Approvals queue", "قائمة الاعتمادات"),
    ("week_signoff", "Confirm the weekly cash flow", "اعتماد التدفق الأسبوعي"),
    ("audit_report", "Audit trail & completeness", "سجل المراجعة واكتمال البيانات"),
]
CORE_CAP_KEYS = [c[0] for c in CORE_CAPS]


def stream_cap(stream_key, action):
    return f"{stream_key}_{action}"


STREAM_CAP_KEYS = [stream_cap(s, a) for s in STREAM_ORDER for a in ACTION_KEYS]
CAP_KEYS = CORE_CAP_KEYS + STREAM_CAP_KEYS

CAP_LABELS = {c[0]: c[1] for c in CORE_CAPS}
CAP_LABELS_AR = {c[0]: c[2] for c in CORE_CAPS}
for _s in STREAM_ORDER:
    for _a, _en, _ar in ACTIONS:
        CAP_LABELS[stream_cap(_s, _a)] = f"{STREAMS[_s]['en']} — {_en}"
        CAP_LABELS_AR[stream_cap(_s, _a)] = f"{STREAMS[_s]['ar']} — {_ar}"


# --- Default scheme of delegation ---------------------------------------
# Streams each role may work on, and with which actions. A role absent from
# STREAM_GRANTS gets the blanket DEFAULT_STREAM_ACTIONS entry.
DEFAULT_STREAM_ACTIONS = {
    "admin":           ["view", "enter", "edit", "delete", "approve"],
    "md":              ["view", "enter", "edit", "approve"],
    "cfo":             ["view", "enter", "edit", "approve"],
    "account_manager": ["view", "enter", "edit", "delete", "approve"],
    "credit_control":  [],            # only the grants below
    "sales_admin":     [],
    "viewer":          ["view"],
}

#: Narrower grants that override the blanket list, per role and stream.
#: Credit Control owns money coming in; Sales Admin owns what the contract says.
STREAM_GRANTS = {
    # Customer collections carries instalments, down payments and advances, so
    # one table covers everything these two seats key in.
    "credit_control": {"collections": ["view", "enter", "edit"]},
    "sales_admin": {"collections": ["view", "enter", "edit"]},
}

DEFAULT_CORE = {
    "admin": CORE_CAP_KEYS,
    "md": ["dashboard", "forecast", "reports", "trend", "banks_view", "exports",
           "approvals", "audit_report"],
    "cfo": ["dashboard", "forecast", "reports", "trend", "banks_view", "banks_edit",
            "opening_edit", "imports", "exports", "approvals", "week_signoff",
            "audit_report"],
    "account_manager": ["dashboard", "forecast", "reports", "trend", "banks_view",
                        "banks_edit", "opening_edit", "imports", "exports",
                        "approvals", "week_signoff", "audit_report"],
    # No core capabilities at all: no dashboard, no forecast, no cash position,
    # no reports. They land straight on the table they maintain.
    "credit_control": [],
    "sales_admin": [],
    "viewer": ["dashboard", "forecast", "reports", "banks_view"],
}

#: Roles that may decide items in the approvals queue (editable in the console).
DEFAULT_APPROVERS = ["admin", "cfo", "md", "account_manager"]

#: Who receives the weekly cash review once a week is confirmed.
DEFAULT_REVIEW_RECIPIENTS = ["md", "cfo"]

#: Who gets the "please enter next week's figures" request.
DEFAULT_REQUEST_RECIPIENTS = ["credit_control", "sales_admin", "account_manager"]

OPEN_ENDPOINTS = {"login", "logout", "setup", "static", "set_lang", "healthz",
                  "brand_logo", "run_notices"}


# ============================================================
# Matrix storage
# ============================================================

def default_allowed(role):
    allowed = set(DEFAULT_CORE.get(role, []))
    blanket = DEFAULT_STREAM_ACTIONS.get(role, ["view"])
    grants = STREAM_GRANTS.get(role, {})
    for s in STREAM_ORDER:
        for a in grants.get(s, blanket):
            allowed.add(stream_cap(s, a))
    return allowed


def seed_matrix(force=False):
    """Write the default matrix on first run. Never overwrites unless forced."""
    if not force and RolePermission.query.first() is not None:
        return
    for role in ROLE_KEYS:
        allowed = set(CAP_KEYS) if role == "admin" else default_allowed(role)
        for cap in CAP_KEYS:
            row = db.session.get(RolePermission, (role, cap))
            if row is None:
                row = RolePermission(role=role, cap=cap)
                db.session.add(row)
            row.allowed = cap in allowed
    db.session.commit()


def get_matrix():
    m = {r: {c: False for c in CAP_KEYS} for r in ROLE_KEYS}
    for rp in RolePermission.query.all():
        if rp.role in m and rp.cap in m[rp.role]:
            m[rp.role][rp.cap] = bool(rp.allowed)
    for c in CAP_KEYS:
        m["admin"][c] = True
    return m


def set_permission(role, cap, allowed):
    if role == "admin":
        return
    row = db.session.get(RolePermission, (role, cap))
    if row is None:
        row = RolePermission(role=role, cap=cap)
        db.session.add(row)
    row.allowed = bool(allowed)
    db.session.commit()


def role_key(user):
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    return (user.role or "").lower()


def is_admin(user):
    return role_key(user) == "admin"


def has_perm(user, cap):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    role = role_key(user)
    if role == "admin":
        return True
    if not cap:
        return True
    row = db.session.get(RolePermission, (role, cap))
    return bool(row and row.allowed)


def can(user, stream_key, action):
    return has_perm(user, stream_cap(stream_key, action))


def hides_menu(user):
    """True when this role should not even see what it cannot use."""
    return role_key(user) in ENTRY_ONLY_ROLES


def visible_streams(user):
    """Streams to put on the menu.

    Most roles see the full list, dimmed where they lack access. An
    entry-only role sees only its own tables.
    """
    if hides_menu(user):
        return [s for s in STREAM_ORDER if can(user, s, "view")]
    return list(STREAM_ORDER)


def landing_stream(user):
    """Where a user with no dashboard lands when they sign in."""
    for s in STREAM_ORDER:
        if can(user, s, "view"):
            return s
    return None


#: Written when every role is unticked. An empty string would read back as
#: "unset" and silently restore the defaults, which would keep emailing people
#: the administrator had just removed.
NONE_MARKER = "-"


def _get_roles(key, default):
    raw = get_setting(key, ",".join(default))
    if raw == NONE_MARKER:
        return []
    return [r for r in (raw or "").split(",") if r in ROLE_KEYS]


def _set_roles(key, roles):
    clean = [r for r in roles if r in ROLE_KEYS]
    set_setting(key, ",".join(clean) if clean else NONE_MARKER)


def get_approver_roles():
    return _get_roles("approver_roles", DEFAULT_APPROVERS)


def set_approver_roles(roles):
    _set_roles("approver_roles", roles)


def get_review_roles():
    return _get_roles("review_roles", DEFAULT_REVIEW_RECIPIENTS)


def set_review_roles(roles):
    _set_roles("review_roles", roles)


def get_request_roles():
    return _get_roles("request_roles", DEFAULT_REQUEST_RECIPIENTS)


def set_request_roles(roles):
    _set_roles("request_roles", roles)


def can_approve_any(user):
    if is_admin(user):
        return True
    if role_key(user) not in get_approver_roles():
        return False
    return any(can(user, s, "approve") for s in STREAM_ORDER)


def users_for_roles(roles):
    """Active users holding any of `roles`, for notification."""
    from models import User
    roles = [r for r in roles if r in ROLE_KEYS]
    if not roles:
        return []
    return [u for u in User.query.filter(User.role.in_(roles)).all() if u.active]


def streams_for_role(role):
    """Which tables a role is expected to keep up to date."""
    return [s for s in STREAM_ORDER
            if _matrix_allows(role, stream_cap(s, "enter"))]


def _matrix_allows(role, cap):
    if role == "admin":
        return True
    row = db.session.get(RolePermission, (role, cap))
    return bool(row and row.allowed)


def dim_reason(user, cap, lang="en"):
    """Tooltip explaining why a menu entry is dimmed, in the active language."""
    if has_perm(user, cap):
        return ""
    label = (CAP_LABELS_AR if lang == "ar" else CAP_LABELS).get(cap, cap)
    role = (ROLE_LABELS_AR if lang == "ar" else ROLE_LABELS).get(role_key(user), "")
    if lang == "ar":
        return f"صلاحية «{label}» غير ممنوحة لدور {role}"
    return f"“{label}” is not granted to the {role} role"
