"""Bulk upload for every data table.

The stream registry already describes each table's fields, so templates,
parsing, validation and the commit are all generated from it — add a
field to a table and its upload template gains a column automatically.

Nothing is written until the user has seen the preview: rows that fail
validation are listed with the reason and the row number, and the
accepted rows are shown with their totals before the commit.
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from models import db, D, CURRENCIES, ST_DRAFT
from streams import STREAMS, STREAM_ORDER

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y",
                "%d %b %Y", "%d %B %Y", "%Y/%m/%d")


def importable_fields(key):
    """Fields that make sense in a spreadsheet — everything but long notes."""
    spec = STREAMS[key]
    return [f for f in spec["fields"] if f["type"] != "textarea"] + \
           [f for f in spec["fields"] if f["type"] == "textarea"]


def columns(key):
    return [(f["name"], f["en"], f) for f in importable_fields(key)]


# ============================================================
# Templates
# ============================================================

def build_template(key):
    """An .xlsx template: headers, a worked example row, and guidance."""
    import xlsxwriter
    spec = STREAMS[key]
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "default_date_format": "yyyy-mm-dd"})
    head = wb.add_format({"bold": True, "bg_color": "#1B5584", "font_color": "#FFFFFF",
                          "border": 1, "text_wrap": True, "valign": "vcenter"})
    note = wb.add_format({"italic": True, "font_color": "#5d6b78"})
    ws = wb.add_worksheet("Data")

    cols = columns(key)
    for i, (_name, label, f) in enumerate(cols):
        ws.write(0, i, label + (" *" if f.get("required") else ""), head)
        ws.set_column(i, i, max(14, min(30, len(label) + 6)))
    for i, (_name, _label, f) in enumerate(cols):
        ws.write(1, i, _example(f), note)
    ws.freeze_panes(1, 0)

    guide = wb.add_worksheet("How to use")
    guide.set_column(0, 0, 26)
    guide.set_column(1, 1, 88)
    guide.write(0, 0, "Table", head)
    guide.write(0, 1, spec["en"], head)
    lines = [
        ("Row 1", "Column headings — do not change or reorder them."),
        ("Row 2", "A worked example. Delete it before uploading, or leave it and "
                  "reject it at the preview."),
        ("Dates", "Any of 2026-03-08, 08/03/2026 or 8 Mar 2026. Day first."),
        ("Amounts", "Numbers only, positive. Commas are ignored. The direction of "
                    "cash comes from the table, not from a minus sign."),
        ("Currency", "EGP or USD. Blank is treated as EGP."),
        ("Required", "Columns marked * must be filled in."),
        ("Categories", "Type the category name exactly as it appears in the system; "
                       "an unrecognised name is reported at the preview."),
        ("Status", "Uploaded rows arrive as drafts, for review and approval like any "
                   "other entry."),
    ]
    for r, (a, b) in enumerate(lines, start=1):
        guide.write(r, 0, a)
        guide.write(r, 1, b)

    wb.close()
    return buf.getvalue()


def _example(f):
    t = f["type"]
    if t == "date":
        return "2026-03-08"
    if t == "money":
        return 125000
    if t == "int":
        return 1
    if t == "pct":
        return 100
    if t == "currency":
        return "EGP"
    if t == "direction":
        return "out"
    if t == "weekday":
        return "Sunday"
    if t == "select":
        return f.get("options", [("", "")])[0][1]
    if t in ("revenue_type", "cost_category"):
        return "(category name)"
    return "example"


# ============================================================
# Reading
# ============================================================

def read_table(storage, key):
    """Read an uploaded .xlsx or .csv into a list of {field_name: raw}."""
    name = (getattr(storage, "filename", "") or "").lower()
    raw = storage.read()
    if name.endswith((".xlsx", ".xlsm")):
        rows = _read_xlsx(raw)
    else:
        rows = _read_csv(raw)
    if not rows:
        return []
    header = [str(h or "").strip().lower().rstrip("*").strip() for h in rows[0]]
    lookup = {}
    for fname, label, f in columns(key):
        lookup[label.lower()] = fname
        lookup[fname.lower()] = fname
        lookup[label.lower().replace(".", "")] = fname
    out = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(str(c).strip() for c in row if c is not None):
            continue
        rec = {"_row": n}
        for i, h in enumerate(header):
            fname = lookup.get(h)
            if fname and i < len(row):
                rec[fname] = row[i]
        out.append(rec)
    return out


def _read_csv(raw):
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(raw):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


# ============================================================
# Validation
# ============================================================

def validate(key, records, refs=None):
    """Split records into (accepted, rejected). Nothing is written."""
    spec = STREAMS[key]
    refs = refs or reference_lookup()
    accepted, rejected = [], []
    for rec in records:
        clean, errors = {}, []
        for f in importable_fields(key):
            val, err = coerce(f, rec.get(f["name"]), refs)
            if err:
                errors.append(f"{f['en']}: {err}")
            else:
                clean[f["name"]] = val
        amount = clean.get("amount")
        if amount is None or D(amount) <= 0:
            errors.append("Amount: a positive figure is required")
        if errors:
            rejected.append({"_row": rec.get("_row"), "errors": errors, "raw": rec})
        else:
            clean["_row"] = rec.get("_row")
            accepted.append(clean)
    return accepted, rejected


def coerce(f, raw, refs):
    """Return (value, error). Blank optional fields return (None, None)."""
    t = f["type"]
    required = f.get("required")
    blank = raw is None or (isinstance(raw, str) and not raw.strip())

    if blank:
        if t == "currency":
            return "EGP", None
        if t == "pct":
            return 100, None
        if t == "select":
            # The web form defaults a dropdown to its first option; an upload
            # must behave the same way or the two routes disagree.
            opts = f.get("options") or []
            return (opts[0][0] if opts else None), None
        if t == "direction":
            return "out", None
        if required:
            return None, "required"
        return None, None

    if t in ("text", "textarea"):
        return str(raw).strip()[:300], None

    if t == "money":
        try:
            v = D(str(raw).replace(",", "").replace("٬", "").strip())
        except (InvalidOperation, ValueError):
            return None, f"'{raw}' is not a number"
        if v < 0:
            return None, "must not be negative"
        return v.quantize(Decimal("0.01")), None

    if t == "date":
        return _date(raw)

    if t in ("int", "pct", "weekday"):
        v, err = _int(raw, f, t)
        return v, err

    if t == "currency":
        v = str(raw).strip().upper()
        return (v, None) if v in CURRENCIES else (None, f"must be one of {', '.join(CURRENCIES)}")

    if t == "direction":
        v = str(raw).strip().lower()
        if v in ("in", "out"):
            return v, None
        if v in ("cash in", "receipt", "inflow"):
            return "in", None
        if v in ("cash out", "payment", "outflow"):
            return "out", None
        return None, "must be 'in' or 'out'"

    if t == "select":
        want = str(raw).strip().lower()
        for code, en, ar in f.get("options", []):
            if want in (code.lower(), en.lower(), (ar or "").lower()):
                return code, None
        allowed = ", ".join(o[1] for o in f.get("options", []))
        return None, f"'{raw}' is not one of: {allowed}"

    if t in ("revenue_type", "cost_category"):
        table = refs["revenue"] if t == "revenue_type" else refs["cost"]
        want = str(raw).strip().lower()
        if want in table:
            return table[want], None
        return None, f"'{raw}' is not a known category"

    return str(raw).strip(), None


def _date(raw):
    if isinstance(raw, datetime):
        return raw.date(), None
    if isinstance(raw, date):
        return raw, None
    text = str(raw).strip()
    if " " in text and ":" in text:
        text = text.split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            continue
    return None, f"'{raw}' is not a date the system recognises"


def _int(raw, f, t):
    try:
        v = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        if t == "weekday":
            names = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                     "friday": 4, "saturday": 5, "sunday": 6}
            v = names.get(str(raw).strip().lower())
            if v is None:
                return None, f"'{raw}' is not a weekday"
            return v, None
        return None, f"'{raw}' is not a whole number"
    lo, hi = f.get("min"), f.get("max")
    if t == "pct":
        lo, hi = 0, 100
    if t == "weekday":
        lo, hi = 0, 6
    if lo is not None and v < lo:
        return None, f"must be at least {lo}"
    if hi is not None and v > hi:
        return None, f"must not exceed {hi}"
    return v, None


def reference_lookup():
    from models import RevenueType, CostCategory
    rev, cost = {}, {}
    for r in RevenueType.query.all():
        rev[r.code.lower()] = r.id
        rev[r.name_en.lower()] = r.id
        if r.name_ar:
            rev[r.name_ar.lower()] = r.id
    for c in CostCategory.query.all():
        cost[c.code.lower()] = c.id
        cost[c.name_en.lower()] = c.id
        if c.name_ar:
            cost[c.name_ar.lower()] = c.id
    return {"revenue": rev, "cost": cost}


# ============================================================
# Preview and commit
# ============================================================

def summarise(key, accepted):
    spec = STREAMS[key]
    totals = {c: Decimal("0") for c in CURRENCIES}
    for row in accepted:
        cur = row.get("currency") or "EGP"
        if cur in totals:
            totals[cur] += D(row.get("amount"))
    dates = [r["due_date"] for r in accepted if r.get("due_date")]
    return {"count": len(accepted), "totals": totals,
            "first": min(dates) if dates else None,
            "last": max(dates) if dates else None,
            "direction": spec["direction"]}


def commit(key, accepted, user, status=ST_DRAFT):
    """Insert the accepted rows. Returns the number written."""
    spec = STREAMS[key]
    model = spec["model"]
    written = 0
    for row in accepted:
        data = {k: v for k, v in row.items() if not k.startswith("_")}
        obj = model(**data)
        obj.status = status
        obj.created_by = user
        obj.updated_by = user
        db.session.add(obj)
        written += 1
    db.session.commit()
    return written


def importable_streams():
    return list(STREAM_ORDER)
