"""Charts, drawn as inline SVG on the server.

No charting library and no CDN: the reports must print correctly, work
on a locked-down network, and survive the application being opened years
from now.  Every chart is a self-contained `<svg>` string.

Colour follows the data-visualisation method: categorical hues are
assigned in a fixed order and never cycled; a ninth category folds into
"Other".  The categorical ramp below is a validated palette (adjacent-pair
CVD ΔE ≥ 8, normal-vision ΔE ≥ 15 on a light surface).  Every chart ships
with its own data table beside it on the page, which is what licenses the
three lower-contrast hues.
"""
from decimal import Decimal
from html import escape

# Brand
BRAND_DEEP = "#1B5584"
BRAND = "#2E7FB8"
BRAND_BRIGHT = "#4FC0F0"
BRAND_PALE = "#9CD8E4"

INK = "#17232e"
MUTED = "#5d6b78"
GRID = "#e3e9ee"
SURFACE = "#ffffff"

POS = "#1baf7a"          # money in
NEG = "#d5322a"          # money out / negative balance
NEG_FILL = "#f6d3d0"
POS_FILL = "#d8efe4"

#: Fixed categorical order — never cycled, never re-ordered by rank.
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
               "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER_HUE = "#8d99a4"


def _f(v):
    return float(v if v is not None else 0)


def _money(v, dp=0):
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "–"


def _nice_step(span, target=5):
    """A round axis step: 1, 2, 2.5 or 5 times a power of ten."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target)
    import math
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * mag:
            return mult * mag
    return 10 * mag


def _scale(lo, hi, target=5):
    """Axis bounds and step, always including zero."""
    lo = min(0.0, lo)
    hi = max(0.0, hi)
    if lo == hi:
        return -1.0, 1.0, 1.0
    step = _nice_step(hi - lo, target)
    import math
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    return lo, hi, step


def _open(w, h, title, desc=""):
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'role="img" preserveAspectRatio="xMidYMid meet" '
            f'aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title>'
            + (f'<desc>{escape(desc)}</desc>' if desc else ''))


def _empty(w, h, message):
    return (_open(w, h, message) +
            f'<text x="{w/2}" y="{h/2}" text-anchor="middle" fill="{MUTED}" '
            f'font-size="13">{escape(message)}</text></svg>')


# ============================================================
# Cash balance over time — area, with threshold and projection
# ============================================================

def cash_balance(points, threshold=0.0, projected_from=None, title="Cash balance",
                 width=900, height=300, currency="",
                 legend_confirmed="Confirmed", legend_projected="Projected"):
    """`points` is a list of (label, value). Values below zero shade red.

    `projected_from` is the index at which the line stops being the
    confirmed position and becomes projection; from there the stroke is
    dashed and the fill lightens, the way a forecast should look
    different from a fact.
    """
    if not points:
        return _empty(width, height, "No cash position to plot")

    pad_l, pad_r, pad_t, pad_b = 72, 18, 18, 42
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    vals = [_f(v) for _, v in points]
    lo, hi, step = _scale(min(vals + [threshold]), max(vals + [threshold]))
    n = len(points)

    def x(i):
        return pad_l + (iw * i / max(1, n - 1))

    def y(v):
        return pad_t + ih - ((_f(v) - lo) / (hi - lo) * ih)

    zero_y = y(0)
    cut = n - 1 if projected_from is None else max(0, min(n - 1, projected_from))

    out = [_open(width, height, title,
                 f"{n} periods, from {points[0][0]} to {points[-1][0]}")]

    # grid + y axis
    g = lo
    while g <= hi + 1e-9:
        gy = y(g)
        out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
                   f'fill="{MUTED}" font-size="11">{_money(g)}</text>')
        g += step

    # the area, split at zero so shortfalls read red
    pts = [(x(i), y(v)) for i, v in enumerate(vals)]
    area = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    out.append(f'<clipPath id="above"><rect x="{pad_l}" y="{pad_t}" '
               f'width="{iw}" height="{max(0, zero_y - pad_t):.1f}"/></clipPath>')
    out.append(f'<clipPath id="below"><rect x="{pad_l}" y="{zero_y:.1f}" '
               f'width="{iw}" height="{max(0, pad_t + ih - zero_y):.1f}"/></clipPath>')
    poly = f'{pad_l},{zero_y:.1f} {area} {x(n-1):.1f},{zero_y:.1f}'
    out.append(f'<polygon points="{poly}" fill="{POS_FILL}" clip-path="url(#above)"/>')
    out.append(f'<polygon points="{poly}" fill="{NEG_FILL}" clip-path="url(#below)"/>')

    # zero line, then the threshold if it is a real floor
    out.append(f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width - pad_r}" '
               f'y2="{zero_y:.1f}" stroke="{INK}" stroke-width="1.5"/>')
    if threshold and threshold != 0:
        ty = y(threshold)
        out.append(f'<line x1="{pad_l}" y1="{ty:.1f}" x2="{width - pad_r}" y2="{ty:.1f}" '
                   f'stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="2 4"/>')
        out.append(f'<text x="{width - pad_r}" y="{ty - 5:.1f}" text-anchor="end" '
                   f'fill="{MUTED}" font-size="10">Minimum buffer</text>')

    # confirmed segment, then projected segment
    solid = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts[:cut + 1])
    if len(pts[:cut + 1]) > 1:
        out.append(f'<polyline points="{solid}" fill="none" stroke="{BRAND_DEEP}" '
                   f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    if cut < n - 1:
        dashed = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts[cut:])
        out.append(f'<polyline points="{dashed}" fill="none" stroke="{BRAND}" '
                   f'stroke-width="2" stroke-dasharray="6 4" stroke-linecap="round"/>')

    # markers, with a surface ring so overlaps stay readable
    for i, (px, py) in enumerate(pts):
        colour = NEG if vals[i] < 0 else BRAND_DEEP
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{colour}" '
                   f'stroke="{SURFACE}" stroke-width="2"><title>'
                   f'{escape(str(points[i][0]))}: {_money(vals[i])} {escape(currency)}'
                   f'</title></circle>')

    # x labels — thinned so they never collide
    every = max(1, n // 12)
    for i, (label, _v) in enumerate(points):
        if i % every and i != n - 1:
            continue
        out.append(f'<text x="{x(i):.1f}" y="{height - 22}" text-anchor="middle" '
                   f'fill="{MUTED}" font-size="10">{escape(str(label))}</text>')

    # legend
    lx = pad_l
    out.append(f'<line x1="{lx}" y1="{height - 7}" x2="{lx + 16}" y2="{height - 7}" '
               f'stroke="{BRAND_DEEP}" stroke-width="2"/>'
               f'<text x="{lx + 22}" y="{height - 4}" fill="{MUTED}" font-size="10">'
               f'{escape(str(legend_confirmed))}</text>')
    if cut < n - 1:
        lx += 96
        out.append(f'<line x1="{lx}" y1="{height - 7}" x2="{lx + 16}" y2="{height - 7}" '
                   f'stroke="{BRAND}" stroke-width="2" stroke-dasharray="5 3"/>'
                   f'<text x="{lx + 22}" y="{height - 4}" fill="{MUTED}" font-size="10">'
                   f'{escape(str(legend_projected))}</text>')
    out.append("</svg>")
    return "".join(out)


# ============================================================
# Money in / out — grouped bars
# ============================================================

def money_in_out(points, title="Money in and out", width=900, height=300,
                 projected_from=None, currency="", legend_in="Money in",
                 legend_out="Money out", legend_projected="Projected"):
    """`points` is a list of (label, money_in, money_out)."""
    if not points:
        return _empty(width, height, "Nothing to plot")

    pad_l, pad_r, pad_t, pad_b = 72, 18, 18, 44
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    highs = [max(_f(a), _f(b)) for _, a, b in points]
    lo, hi, step = _scale(0, max(highs) if highs else 0)
    n = len(points)
    slot = iw / max(1, n)
    bw = min(26, max(6, (slot - 10) / 2 - 1))     # 2px gap between the pair

    def y(v):
        return pad_t + ih - ((_f(v) - lo) / (hi - lo) * ih)

    out = [_open(width, height, title)]
    g = lo
    while g <= hi + 1e-9:
        gy = y(g)
        out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
                   f'fill="{MUTED}" font-size="11">{_money(g)}</text>')
        g += step

    base = y(0)
    for i, (label, cash_in, cash_out) in enumerate(points):
        cx = pad_l + slot * i + slot / 2
        projected = projected_from is not None and i >= projected_from
        for k, (val, colour) in enumerate(((cash_in, POS), (cash_out, NEG))):
            bx = cx - bw - 1 + k * (bw + 2)
            by = y(val)
            bh = max(0, base - by)
            op = ' opacity="0.55"' if projected else ""
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" '
                       f'height="{bh:.1f}" rx="3" fill="{colour}"{op}>'
                       f'<title>{escape(str(label))} — '
                       f'{"in" if k == 0 else "out"} {_money(val)} {escape(currency)}'
                       f'</title></rect>')
        every = max(1, n // 14)
        if i % every == 0 or i == n - 1:
            out.append(f'<text x="{cx:.1f}" y="{height - 24}" text-anchor="middle" '
                       f'fill="{MUTED}" font-size="10">{escape(str(label))}</text>')

    out.append(f'<line x1="{pad_l}" y1="{base:.1f}" x2="{width - pad_r}" '
               f'y2="{base:.1f}" stroke="{INK}" stroke-width="1.5"/>')
    for k, (name, colour) in enumerate(((legend_in, POS), (legend_out, NEG))):
        lx = pad_l + k * 110
        out.append(f'<rect x="{lx}" y="{height - 13}" width="11" height="11" rx="2" '
                   f'fill="{colour}"/><text x="{lx + 17}" y="{height - 4}" '
                   f'fill="{MUTED}" font-size="10">{escape(str(name))}</text>')
    if projected_from is not None and projected_from < n:
        lx = pad_l + 230
        out.append(f'<rect x="{lx}" y="{height - 13}" width="11" height="11" rx="2" '
                   f'fill="{MUTED}" opacity="0.55"/><text x="{lx + 17}" y="{height - 4}" '
                   f'fill="{MUTED}" font-size="10">{escape(str(legend_projected))}</text>')
    out.append("</svg>")
    return "".join(out)


# ============================================================
# Share of a total — donut
# ============================================================

def donut(slices, title="Share", width=580, height=300, centre_label="",
          centre_value="", max_slices=8):
    """`slices` is a list of (name, value). A ninth category folds to Other."""
    import math
    data = [(str(n), _f(v)) for n, v in slices if _f(v) > 0]
    if not data:
        return _empty(width, height, "Nothing to plot")
    data.sort(key=lambda s: -s[1])
    if len(data) > max_slices:
        head, tail = data[:max_slices - 1], data[max_slices - 1:]
        data = head + [("Other", sum(v for _, v in tail))]
    total = sum(v for _, v in data) or 1.0

    cx, cy = height / 2 + 6, height / 2
    r_out, r_in = height * 0.36, height * 0.21
    out = [_open(width, height, title)]
    angle = -math.pi / 2

    for i, (name, val) in enumerate(data):
        frac = val / total
        sweep = frac * 2 * math.pi
        colour = OTHER_HUE if name == "Other" else CATEGORICAL[i % len(CATEGORICAL)]
        end = angle + sweep
        large = 1 if sweep > math.pi else 0
        x1, y1 = cx + r_out * math.cos(angle), cy + r_out * math.sin(angle)
        x2, y2 = cx + r_out * math.cos(end), cy + r_out * math.sin(end)
        x3, y3 = cx + r_in * math.cos(end), cy + r_in * math.sin(end)
        x4, y4 = cx + r_in * math.cos(angle), cy + r_in * math.sin(angle)
        path = (f"M {x1:.2f} {y1:.2f} A {r_out:.2f} {r_out:.2f} 0 {large} 1 {x2:.2f} {y2:.2f} "
                f"L {x3:.2f} {y3:.2f} A {r_in:.2f} {r_in:.2f} 0 {large} 0 {x4:.2f} {y4:.2f} Z")
        out.append(f'<path d="{path}" fill="{colour}" stroke="{SURFACE}" '
                   f'stroke-width="2"><title>{escape(name)}: {_money(val)} '
                   f'({frac * 100:.1f}%)</title></path>')
        angle = end

    if centre_value:
        out.append(f'<text x="{cx:.1f}" y="{cy - 2:.1f}" text-anchor="middle" '
                   f'fill="{INK}" font-size="17" font-weight="700">'
                   f'{escape(str(centre_value))}</text>')
    if centre_label:
        out.append(f'<text x="{cx:.1f}" y="{cy + 15:.1f}" text-anchor="middle" '
                   f'fill="{MUTED}" font-size="10">{escape(str(centre_label))}</text>')

    # Legend: the ring takes a square on one side, the legend the rest. Labels
    # are truncated to the width actually left for them, so a long category
    # name can never run into its own percentage.
    lx = height + 14
    pct_x = width - 8
    avail = pct_x - (lx + 16) - 52                 # 52px reserved for "100.0%"
    max_chars = max(8, int(avail / 6.1))           # ~6.1px per character at 11px
    ly = 22
    step = min(19, max(14, (height - 30) / max(1, len(data))))
    for i, (name, val) in enumerate(data):
        colour = OTHER_HUE if name == "Other" else CATEGORICAL[i % len(CATEGORICAL)]
        label = name if len(name) <= max_chars else name[:max_chars - 1] + "…"
        out.append(f'<rect x="{lx}" y="{ly - 9}" width="10" height="10" rx="2" '
                   f'fill="{colour}"/>'
                   f'<text x="{lx + 16}" y="{ly}" fill="{INK}" font-size="11">'
                   f'{escape(label)}<title>{escape(name)}</title></text>'
                   f'<text x="{pct_x}" y="{ly}" text-anchor="end" fill="{MUTED}" '
                   f'font-size="11">{val / total * 100:.1f}%</text>')
        ly += step
    out.append("</svg>")
    return "".join(out)


# ============================================================
# Ranked horizontal bars
# ============================================================

def ranked_bars(items, title="Ranked", width=720, height=None, currency="",
                colour=BRAND, max_rows=14):
    """`items` is a list of (name, value), largest first."""
    data = [(str(n), _f(v)) for n, v in items if _f(v)]
    if not data:
        return _empty(width, 120, "Nothing to plot")
    data.sort(key=lambda s: -abs(s[1]))
    data = data[:max_rows]
    row_h = 26
    pad_t, pad_b, pad_l, pad_r = 10, 12, 185, 84
    height = height or (pad_t + pad_b + row_h * len(data))
    iw = width - pad_l - pad_r
    top = max(abs(v) for _, v in data) or 1.0

    out = [_open(width, height, title)]
    for i, (name, val) in enumerate(data):
        y = pad_t + i * row_h
        bar = abs(val) / top * iw
        c = NEG if val < 0 else colour
        label = name if len(name) <= 32 else name[:31] + "…"
        out.append(f'<text x="{pad_l - 10}" y="{y + 17}" text-anchor="end" '
                   f'fill="{INK}" font-size="11.5">{escape(label)}</text>')
        out.append(f'<rect x="{pad_l}" y="{y + 6}" width="{bar:.1f}" height="{row_h - 12}" '
                   f'rx="3" fill="{c}"><title>{escape(name)}: {_money(val)} '
                   f'{escape(currency)}</title></rect>')
        out.append(f'<text x="{width - 8}" y="{y + 17}" text-anchor="end" '
                   f'fill="{INK if val >= 0 else NEG}" font-size="11.5" '
                   f'font-weight="600">{_money(val)}</text>')
    out.append("</svg>")
    return "".join(out)


# ============================================================
# Sparkline — a small trend beside a figure
# ============================================================

def sparkline(values, width=132, height=34, colour=BRAND_DEEP):
    vals = [_f(v) for v in values]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    pts = []
    for i, v in enumerate(vals):
        x = 2 + (width - 4) * i / (len(vals) - 1)
        y = height - 3 - (v - lo) / (hi - lo) * (height - 6)
        pts.append(f"{x:.1f},{y:.1f}")
    last_neg = vals[-1] < 0
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}" fill="none" '
            f'stroke="{NEG if last_neg else colour}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')
