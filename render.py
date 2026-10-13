"""SVG diagram of a simulator state, with an optional highlighted probe path.

Every line is clipped to the outline of the shapes it joins (boxes, circles), lines are drawn under the boxes, and
components that are not built yet appear as faint dashed placeholders so the layout never has unexplained gaps.
"""
from html import escape
import math

W = 1200
COL = {"ink": "#1B2A41", "muted": "#7D8998", "navy": "#0B3C5D", "teal": "#0B6E79", "rust": "#C8553D", "amber": "#E9A23B",
       "green": "#2E7D32", "purple": "#5B4B8A", "prod": "#E6F2FF", "prodL": "#147EBA", "dr": "#FDF0E3", "drill": "#DFF3DA",
       "ad": "#FFF8D6", "down": "#FDECEA", "grey": "#B8C2CC", "ghost": "#C9D1DA"}

# y = 0..80 is the vendor band
LAYOUT = {
    1: {"H": 690, "gutter": 600,
        "regions": {"A": (15, 92, 575, 478), "B": (610, 92, 575, 478)},
        "tgw": {"TGW-A": (505, 205), "TGW-B": (695, 205)},
        "vpc": {"VPC-A": (35, 140, 330), "MIR-B": (35, 345, 220), "BUB-A": (265, 345, 200),
                "VPC-B": (835, 140, 330), "MIR-A": (945, 345, 220), "BUB-B": (735, 345, 200)},
        "edge": {"A": (480, 500), "B": (720, 500)},
        "plant": {"A": (300, 620), "B": (900, 620)},
        "vendor": {"V1": (230, 40), "V3": (600, 40), "DC1": (970, 40)}},
    2: {"H": 740, "gutter": 565,
        "regions": {"A": (175, 92, 380, 300), "B": (175, 405, 380, 300), "C": (575, 92, 610, 613)},
        "tgw": {"TGW-A": (485, 200), "TGW-B": (485, 515), "TGW-C": (630, 400)},
        "vpc": {"VPC-A": (190, 140, 225), "VPC-B": (190, 453, 225), "DR-C": (690, 140, 235), "SS-C": (940, 140, 235),
                "MIR-A": (690, 300, 235), "MIR-B": (940, 300, 235), "BUB-C": (940, 470, 235)},
        "edge": {"A": (300, 335), "B": (300, 650), "C": (720, 640)},
        "plant": {"A": (88, 335, 150, 56), "B": (88, 650, 150, 56)},
        "vendor": {"V1": (230, 40), "V3": (600, 40), "DC1": (970, 40)}},
}
GHOST = {"MIR-A": "Mirror of PLC-A", "MIR-B": "Mirror of PLC-B", "BUB-A": "Drill bubble us-east-1", "BUB-B": "Drill bubble us-west-2",
         "BUB-C": "Drill bubble us-east-2", "DR-C": "DR VPC (DR-C)", "SS-C": "Shared Services (AD) us-east-2"}
FILL = {"prod": (COL["prod"], COL["prodL"]), "mirror": (COL["dr"], COL["rust"]), "bubble": (COL["drill"], COL["green"]),
        "dr": (COL["dr"], COL["amber"]), "ad": (COL["ad"], "#B58B00")}
DOT = {"running": COL["green"], "stopped": COL["muted"], "down": COL["rust"]}
TGW_R = 40


def _t(x, y, s, size=12, color=None, weight="normal", anchor="start", italic=False):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="Calibri, Segoe UI, Arial, sans-serif" font-size="{size}" fill="{color or COL["ink"]}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{" font-style=\"italic\"" if italic else ""}>{escape(str(s))}</text>')


def _vpc_box(st, name):
    x, y, w = LAYOUT[st["option"]]["vpc"][name]
    hosts = [h for h in st["hosts"] if h["vpc"] == name]
    return x, y, w, 44 + 20 * max(len(hosts), 1), hosts


def _edge_h(st, k):
    return 62 if st["fw"].get(k, {}).get("nat") else 44


def geometry(st):
    """node id -> ('rect', x, y, w, h) | ('circle', cx, cy, r) for everything drawn (built or placeholder)."""
    lay = LAYOUT[st["option"]]
    g = {}
    for v, (x, y, w) in lay["vpc"].items():
        if v in st["vpcs"]:
            bx, by, bw, bh, _ = _vpc_box(st, v)
            g[v] = ("rect", bx, by, bw, bh)
        else:
            g[v] = ("rect", x, y, w, 56)
    for t, (cx, cy) in lay["tgw"].items():
        g[t] = ("circle", cx, cy, TGW_R)
    for k, (cx, cy) in lay["edge"].items():
        g["edge-" + k] = ("rect", cx - 105, cy - 22, 210, _edge_h(st, k))
    for k, v in lay["plant"].items():
        cx, cy, pw, ph = (v + (240, 40)) if len(v) == 2 else v
        g["plant-" + k] = ("rect", cx - pw / 2, cy - ph / 2, pw, ph)
    for k, (cx, cy) in lay["vendor"].items():
        g["vendor-" + k] = ("rect", cx - 125, cy - 24, 250, 46)
    return g


def _center(s):
    return (s[1], s[2]) if s[0] == "circle" else (s[1] + s[3] / 2, s[2] + s[4] / 2)


def _clip(s, toward):
    """Point where the segment from the shape's centre towards `toward` leaves the shape."""
    cx, cy = _center(s)
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    if s[0] == "circle":
        d = math.hypot(dx, dy)
        return cx + dx / d * (s[3] + 1), cy + dy / d * (s[3] + 1)
    hw, hh = s[3] / 2 + 1, s[4] / 2 + 1
    t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def _seg(g, a, b):
    sa, sb = g[a], g[b]
    return _clip(sa, _center(sb)), _clip(sb, _center(sa))


def _route(st, g, a, b):
    """Points for a connection a -> b. Vendor tunnels run down to a bus under the vendor band and along the gutter
    between the Regions, so they never cut across a Region."""
    rev = False
    if b.startswith("vendor-") and a.startswith("TGW-"):
        a, b, rev = b, a, True
    if a.startswith("vendor-") and b.startswith("TGW-"):
        gx = LAYOUT[st["option"]]["gutter"]
        vx, vy = _center(g[a])
        tx, ty = _center(g[b])
        pts = [(vx, vy + 24), (vx, 84), (gx, 84), (gx, ty), _clip(g[b], (gx, ty))]
        return pts[::-1] if rev else pts
    if rev:
        a, b = b, a
    return list(_seg(g, a, b))


def _poly(pts, color, width=2.0, dash=None, opacity=1.0, marker=False):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"'
            f'{f" stroke-dasharray=\"{dash}\"" if dash else ""} opacity="{opacity}"{" marker-end=\"url(#arr)\"" if marker else ""}/>')


def _line(p1, p2, color, width=2.0, dash=None, opacity=1.0, marker=False):
    return (f'<line x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}" stroke="{color}" stroke-width="{width}" '
            f'stroke-linecap="round"{f" stroke-dasharray=\"{dash}\"" if dash else ""} opacity="{opacity}"{" marker-end=\"url(#arr)\"" if marker else ""}/>')


def svg(st, path=None, added_targets=(), title=""):
    lay = LAYOUT[st["option"]]
    H = lay["H"]
    g = geometry(st)
    back, mid, front = [], [], []          # lines under boxes, boxes, overlay

    # ---------------- regions
    for k, (x, y, w, h) in lay["regions"].items():
        if k not in st["regions"]:
            continue
        up = st["regions"][k]["up"]
        built = k != "C" or "TGW-C" in st["tgws"] or "DR-C" in st["vpcs"]
        stroke = COL["teal"] if k == "C" else (COL["rust"] if st["option"] == 1 else COL["muted"])
        back.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{COL["down"] if not up else "#FFFFFF"}" '
                    f'stroke="{stroke}" stroke-width="2" stroke-dasharray="8 5" opacity="{1 if built else 0.5}"/>')
        sub = {1: "production + DR for the other Region", 2: "production only" if k != "C" else "DR account"}[st["option"]]
        back.append(_t(x + 14, y + 22, f'{st["regions"][k]["name"]} ({k})', 16, stroke, "bold"))
        back.append(_t(x + 14, y + 38, sub if built else "not built yet", 11, COL["muted"]))
        if not up:
            back.append(_t(x + w - 14, y + 24, "REGION DOWN", 15, COL["rust"], "bold", "end"))

    # ---------------- connecting lines (under the boxes)
    for pk in lay["plant"]:
        for ek in lay["edge"]:
            if st["edges"].get(ek):
                p1, p2 = _seg(g, "plant-" + pk, "edge-" + ek)
                back.append(_line(p1, p2, COL["grey"], 1.5 if pk == ek else 1.1, "4 5", 0.9 if pk == ek else 0.35))
    for t, tg in st["tgws"].items():
        up_t = st["regions"][tg["region"]]["up"]
        for a, v in tg["atts"].items():
            if v["type"] == "vpn" and "vendor-" + v["vendor"] in g:
                back.append(_poly(_route(st, g, "vendor-" + v["vendor"], t), COL["purple"] if up_t else COL["rust"], 1.3, "3 4", 0.45))
            if v["type"] == "sdwan":
                p1, p2 = _seg(g, t, "edge-" + tg["region"])
                back.append(_line(p1, p2, COL["grey"], 3))
    drawn = set()
    for t, tg in st["tgws"].items():
        for a, v in tg["atts"].items():
            if v["type"] == "peering" and a not in drawn:
                drawn.add(a)
                other = v["peer"][0]
                ok = st["regions"][tg["region"]]["up"] and st["regions"][st["tgws"][other]["region"]]["up"]
                p1, p2 = _seg(g, t, other)
                back.append(_line(p1, p2, COL["purple"] if ok else COL["rust"], 4, None if ok else "10 6", 0.9))
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                back.append(f'<rect x="{mx - 32:.1f}" y="{my - 21:.1f}" width="64" height="16" rx="3" fill="#FFFFFF" opacity="0.9"/>')
                back.append(_t(mx, my - 9, a, 11, COL["purple"] if ok else COL["rust"], "bold", "middle"))
    for v, vv in st["vpcs"].items():
        if v not in lay["vpc"] or not vv["att"]:
            continue
        tgw, att = vv["att"]
        p1, p2 = _seg(g, v, tgw)
        props = sorted({rn for rn, r in st["tgws"][tgw]["rts"].items() for z in r["routes"] if z["target"] == att and z["kind"] == "propagated"})
        assoc = any(att in r["assoc"] for r in st["tgws"][tgw]["rts"].values())
        bad = vv["kind"] == "mirror" and props
        col = COL["rust"] if bad else (COL["amber"] if vv["kind"] == "mirror" else COL["grey"])
        back.append(_line(p1, p2, col, 5 if bad else 3, None if assoc else "4 5"))

    # ---------------- VPC boxes (and placeholders)
    for v in lay["vpc"]:
        if v not in st["vpcs"]:
            if v in GHOST:
                _, x, y, w, h = g[v]
                mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#FAFBFC" stroke="{COL["ghost"]}" stroke-width="1.4" stroke-dasharray="5 4"/>')
                mid.append(_t(x + 10, y + 22, GHOST[v], 12, COL["muted"], "bold"))
                mid.append(_t(x + 10, y + 40, "not built yet", 11, COL["muted"], italic=True))
            continue
        vv = st["vpcs"][v]
        x, y, w, h, hosts = _vpc_box(st, v)
        fill, line = FILL[vv["kind"]]
        down = not st["regions"][vv["region"]]["up"]
        mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{COL["down"] if down else fill}" stroke="{line}" stroke-width="1.6"/>')
        mid.append(_t(x + 10, y + 18, vv["label"], 13, COL["ink"], "bold"))
        status = ""
        if vv["kind"] == "mirror" and vv["att"]:
            tgw, att = vv["att"]
            props = sorted({rn for rn, r in st["tgws"][tgw]["rts"].items() for z in r["routes"] if z["target"] == att and z["kind"] == "propagated"})
            assoc = any(att in r["assoc"] for r in st["tgws"][tgw]["rts"].values())
            status = ("  PROPAGATED: " + ", ".join(props)) if props else ("  - associated, not propagated" if assoc else "  - attached only")
        mid.append(_t(x + 10, y + 33, ", ".join(vv["cidrs"]) + ("" if vv["att"] else "   (not attached)"), 11, COL["muted"]))
        if status:
            mid.append(_t(x + w - 10, y + 33, status.strip(" -"), 10, COL["rust"] if "PROPAGATED" in status else COL["muted"], "bold" if "PROPAGATED" in status else "normal", "end"))
        if not hosts:
            mid.append(_t(x + 10, y + 54, "empty" if vv["kind"] == "mirror" else "-", 11, COL["muted"], italic=True))
        for i, hh in enumerate(hosts):
            yy = y + 52 + 20 * i
            col = DOT.get(hh["state"], COL["muted"])
            if hh["copy_of"] and hh["state"] == "running":
                col = COL["prodL"]
            mid.append(f'<circle cx="{x + 16}" cy="{yy - 4}" r="5.5" fill="{col}"/>')
            mid.append(_t(x + 28, yy, hh["name"], 12, COL["ink"], "bold"))
            mid.append(_t(x + w - 10, yy, f'{hh["ip"]}  {hh["state"]}', 11, COL["muted"], anchor="end"))

    # ---------------- TGWs
    for t, (cx, cy) in lay["tgw"].items():
        if t not in st["tgws"]:
            mid.append(f'<circle cx="{cx}" cy="{cy}" r="{TGW_R}" fill="#FAFBFC" stroke="{COL["ghost"]}" stroke-width="1.6" stroke-dasharray="5 4"/>')
            mid.append(_t(cx, cy + 4, t, 13, COL["muted"], "bold", "middle"))
            mid.append(_t(cx, cy + TGW_R + 16, "not built yet", 11, COL["muted"], anchor="middle", italic=True))
            continue
        tg = st["tgws"][t]
        up = st["regions"][tg["region"]]["up"]
        hit = t in added_targets
        mid.append(f'<circle cx="{cx}" cy="{cy}" r="{TGW_R}" fill="{COL["purple"] if up else COL["grey"]}" stroke="{COL["green"] if hit else "#FFFFFF"}" stroke-width="{6 if hit else 2}"/>')
        mid.append(_t(cx, cy - 1, t, 14, "#FFFFFF", "bold", "middle"))
        mid.append(_t(cx, cy + 15, st["regions"][tg["region"]]["name"], 10, "#FFFFFF", anchor="middle"))
        d = "defaults ON" if (tg["default_prop"] or tg["default_assoc"]) else "defaults OFF"
        mid.append(_t(cx, cy + TGW_R + 16, d, 11, COL["rust"] if d == "defaults ON" else COL["teal"], "bold", "middle"))
        if hit:
            mid.append(_t(cx, cy - TGW_R - 8, "routes changed", 11, COL["green"], "bold", "middle"))

    # ---------------- Palo Alto + SD-WAN
    for k, (cx, cy) in lay["edge"].items():
        if k not in st["regions"]:
            continue
        _, x, y, w, h = g["edge-" + k]
        if not st["edges"].get(k):
            mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="44" rx="8" fill="#FAFBFC" stroke="{COL["ghost"]}" stroke-width="1.4" stroke-dasharray="5 4"/>')
            mid.append(_t(cx, cy - 3, f"Palo Alto + SD-WAN pair {k}", 12, COL["muted"], "bold", "middle"))
            mid.append(_t(cx, cy + 13, "not built yet", 11, COL["muted"], anchor="middle", italic=True))
            continue
        up = st["regions"][k]["up"]
        mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{"#F4F6F9" if up else COL["down"]}" stroke="{COL["muted"]}" stroke-width="1.5"/>')
        mid.append(_t(cx, cy - 3, f"Palo Alto + SD-WAN pair {k}", 13, COL["ink"], "bold", "middle"))
        fw = st["fw"].get(k, {"plants": [], "nat": {}})
        rules = ", ".join(c for c in fw["plants"] if c.startswith("10.") and c.endswith("/24")) or "-"
        mid.append(_t(cx, cy + 13, "pre-staged PLC rules: " + rules, 10, COL["muted"], anchor="middle"))
        if fw["nat"]:
            mid.append(_t(cx, cy + 30, "DNAT " + ", ".join(sorted(fw["nat"])), 10, COL["purple"], "bold", "middle"))

    # ---------------- vendors and plants
    for k, (cx, cy) in lay["vendor"].items():
        if k not in st["vendors"]:
            continue
        v = st["vendors"][k]
        _, x, y, w, h = g["vendor-" + k]
        mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#F3EFFA" stroke="{COL["purple"]}" stroke-width="1.6"/>')
        mid.append(_t(cx, cy - 5, f'{v["label"]} - {v["how"]}', 13, COL["purple"], "bold", "middle"))
        reach = "DNAT + PLCs" if "DNAT" in v["how"] else ("servers" if v["mode"] == "bgp" else "servers + PLCs")
        moved = any(len(r) > 1 for r in v["sel"].values())
        mid.append(_t(cx, cy + 12, v["cidr"] + "  ->  " + reach + ("  (backup peer)" if moved else ""), 11, COL["muted"], anchor="middle"))
    for k in lay["plant"]:
        _, x, y, w, h = g["plant-" + k]
        cx, cy = x + w / 2, y + h / 2
        mid.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#FFFFFF" stroke="{COL["navy"]}" stroke-width="1.6"/>')
        name = f"Plants {'east' if k == 'A' else 'west'}"
        if w < 200:
            mid.append(_t(cx, cy - 3, name, 13, COL["navy"], "bold", "middle"))
            mid.append(_t(cx, cy + 14, "PLCs, HMIs", 11, COL["navy"], anchor="middle"))
        else:
            mid.append(_t(cx, cy + 5, name + " (PLCs, HMIs)", 13, COL["navy"], "bold", "middle"))

    # ---------------- highlighted path (on top, clipped to the shapes so text stays readable)
    if path and path.get("hops"):
        hops = [h for h in path["hops"] if h in g]
        if len(hops) > 1 and hops[0].startswith("vendor-") and hops[1].startswith("edge-"):
            del hops[1]                                  # the VPN lands on the TGW; inspection shows as TGW -> firewall -> TGW
        hops = [h for i, h in enumerate(hops) if i == 0 or h != hops[i - 1]]
        col = {"ok": COL["green"], "blocked": COL["teal"]}.get(path.get("status"), COL["rust"])
        for a, b in zip(hops, hops[1:]):
            front.append(_poly(_route(st, g, a, b), col, 6, None, 0.75, marker=True))
        if path.get("status") in ("fail", "breach") and hops:
            x, y = _center(g[hops[-1]])
            front.append(f'<g stroke="{COL["rust"]}" stroke-width="6" stroke-linecap="round" opacity="0.9"><line x1="{x - 14}" y1="{y - 14}" x2="{x + 14}" y2="{y + 14}"/>'
                         f'<line x1="{x - 14}" y1="{y + 14}" x2="{x + 14}" y2="{y - 14}"/></g>')

    # ---------------- legend
    leg = []
    lx, ly = 20, H - 10
    for i, (lab, col) in enumerate([("running", COL["green"]), ("DR copy running", COL["prodL"]), ("stopped", COL["muted"]), ("down", COL["rust"])]):
        leg.append(f'<circle cx="{lx + i * 140}" cy="{ly - 4}" r="5.5" fill="{col}"/>')
        leg.append(_t(lx + 10 + i * 140, ly, lab, 11, COL["muted"]))
    leg.append(f'<rect x="{lx + 570}" y="{ly - 11}" width="22" height="12" rx="2" fill="#FAFBFC" stroke="{COL["ghost"]}" stroke-dasharray="4 3"/>')
    leg.append(_t(lx + 598, ly, "not built yet", 11, COL["muted"]))
    if title:
        leg.append(_t(W - 16, ly, title, 11, COL["muted"], anchor="end"))

    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" style="background:#fff">'
            '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>')
    return head + "".join(back + mid + front + leg) + "</svg>"
