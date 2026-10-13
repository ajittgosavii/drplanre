"""SVG diagram of a simulator state, with an optional highlighted probe path."""
from html import escape

W, H = 1200, 800
COL = {"ink": "#1B2A41", "muted": "#7D8998", "navy": "#0B3C5D", "teal": "#0B6E79", "rust": "#C8553D", "amber": "#E9A23B",
       "green": "#2E7D32", "purple": "#5B4B8A", "prod": "#E6F2FF", "prodL": "#147EBA", "dr": "#FDF0E3", "drill": "#DFF3DA",
       "ad": "#FFF8D6", "down": "#FDECEC", "grey": "#B8C2CC"}

LAYOUT = {
    1: {"regions": {"A": (15, 15, 575, 640), "B": (610, 15, 575, 640)},
        "tgw": {"TGW-A": (505, 130), "TGW-B": (695, 130)},
        "vpc": {"VPC-A": (35, 75, 330), "MIR-B": (35, 330, 250), "BUB-A": (305, 330, 250),
                "VPC-B": (835, 75, 330), "MIR-A": (915, 330, 250), "BUB-B": (645, 330, 250)},
        "edge": {"A": (300, 600), "B": (900, 600)},
        "plant": {"A": (300, 735), "B": (900, 735)}},
    2: {"regions": {"A": (15, 15, 440, 345), "B": (15, 380, 440, 345), "C": (480, 15, 705, 710)},
        "tgw": {"TGW-A": (390, 125), "TGW-B": (390, 490), "TGW-C": (555, 370)},
        "vpc": {"VPC-A": (30, 65, 270), "VPC-B": (30, 430, 270), "DR-C": (640, 65, 250), "SS-C": (920, 65, 250),
                "MIR-A": (640, 270, 250), "MIR-B": (920, 270, 250), "BUB-C": (640, 470, 250)},
        "edge": {"A": (390, 300), "B": (390, 665), "C": (1045, 665)},
        "plant": {"A": (230, 752), "B": (760, 752)}},
}
FILL = {"prod": (COL["prod"], COL["prodL"]), "mirror": (COL["dr"], COL["rust"]), "bubble": (COL["drill"], COL["green"]),
        "dr": (COL["dr"], COL["amber"]), "ad": (COL["ad"], "#B58B00")}
DOT = {"running": COL["green"], "stopped": COL["muted"], "down": COL["rust"]}


def _t(x, y, s, size=12, color=None, weight="normal", anchor="start", italic=False):
    return (f'<text x="{x}" y="{y}" font-family="Calibri, Segoe UI, Arial, sans-serif" font-size="{size}" fill="{color or COL["ink"]}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{" font-style=\"italic\"" if italic else ""}>{escape(str(s))}</text>')


def _vpc_box(st, name):
    lay = LAYOUT[st["option"]]["vpc"][name]
    hosts = [h for h in st["hosts"] if h["vpc"] == name]
    h = 44 + 21 * max(len(hosts), 1)
    return lay[0], lay[1], lay[2], h, hosts


def centers(st):
    lay = LAYOUT[st["option"]]
    c = {}
    for v in st["vpcs"]:
        if v in lay["vpc"]:
            x, y, w, h, _ = _vpc_box(st, v)
            c[v] = (x + w / 2, y + h / 2)
    for t, p in lay["tgw"].items():
        c[t] = p
    for k, p in lay["edge"].items():
        c["edge-" + k] = p
    for k, p in lay["plant"].items():
        c["plant-" + k] = p
    return c


def svg(st, path=None, added_targets=(), title=""):
    lay = LAYOUT[st["option"]]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" style="background:#fff;border:1px solid #E3E8EE;border-radius:10px">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>']
    cen = centers(st)
    # regions
    for k, (x, y, w, h) in lay["regions"].items():
        if k not in st["regions"]:
            continue
        up = st["regions"][k]["up"]
        built = k != "C" or "TGW-C" in st["tgws"] or "DR-C" in st["vpcs"]
        stroke = COL["teal"] if k == "C" else (COL["rust"] if st["option"] == 1 else COL["muted"])
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{COL["down"] if not up else "#FFFFFF"}" '
                   f'stroke="{stroke}" stroke-width="2" stroke-dasharray="8 5" opacity="{1 if built else 0.45}"/>')
        sub = {1: "production + DR for the other Region", 2: "production only" if k != "C" else "DR account"}[st["option"]]
        out.append(_t(x + 14, y + 24, f'{st["regions"][k]["name"]} ({k})', 17, stroke, "bold"))
        out.append(_t(x + 14, y + 42, sub if built else "not built yet", 12, COL["muted"]))
        if not up:
            out.append(_t(x + w - 14, y + 26, "REGION DOWN", 16, COL["rust"], "bold", "end"))
    # peerings + edge links
    drawn = set()
    for t, tg in st["tgws"].items():
        for a, v in tg["atts"].items():
            if v["type"] == "peering" and a not in drawn:
                drawn.add(a)
                (x1, y1), (x2, y2) = cen[t], cen[v["peer"][0]]
                ok = st["regions"][tg["region"]]["up"] and st["regions"][st["tgws"][v["peer"][0]]["region"]]["up"]
                out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COL["purple"] if ok else COL["rust"]}" stroke-width="4" '
                           f'{"" if ok else "stroke-dasharray=\"10 6\""} opacity="0.85"/>')
                out.append(_t((x1 + x2) / 2, (y1 + y2) / 2 - 10, a, 12, COL["purple"] if ok else COL["rust"], "bold", "middle"))
            if v["type"] == "sdwan":
                k = tg["region"]
                (x1, y1), (x2, y2) = cen[t], cen["edge-" + k]
                out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2 - 22}" stroke="{COL["grey"]}" stroke-width="3"/>')
    # plants to edges
    for pk, (px, py) in lay["plant"].items():
        for ek in lay["edge"]:
            if st["edges"].get(ek):
                ex, ey = lay["edge"][ek]
                out.append(f'<line x1="{px}" y1="{py - 20}" x2="{ex}" y2="{ey + 22}" stroke="{COL["grey"]}" stroke-width="1.5" stroke-dasharray="5 5"/>')
    # vpc attachments
    for v, vv in st["vpcs"].items():
        if v not in lay["vpc"] or not vv["att"]:
            continue
        tgw, att = vv["att"]
        x, y, w, h, _ = _vpc_box(st, v)
        tx, ty = cen[tgw]
        sx = x + w if tx > x + w / 2 else x
        props = [rn for rn, r in st["tgws"][tgw]["rts"].items() for z in r["routes"] if z["target"] == att and z["kind"] == "propagated"]
        assoc = any(att in r["assoc"] for r in st["tgws"][tgw]["rts"].values())
        bad = vv["kind"] == "mirror" and props
        col = COL["rust"] if bad else (COL["amber"] if vv["kind"] == "mirror" else COL["grey"])
        out.append(f'<line x1="{sx}" y1="{y + 22}" x2="{tx}" y2="{ty}" stroke="{col}" stroke-width="{5 if bad else 3}" {"" if assoc else "stroke-dasharray=\"4 5\""}/>')
        if vv["kind"] == "mirror":
            label = "PROPAGATED into " + ", ".join(sorted(set(props))) if bad else ("associated, not propagated" if assoc else "attached, not associated")
            lx = (sx + tx) / 2
            out.append(_t(lx, (y + 22 + ty) / 2 - 6, label, 11, COL["rust"] if bad else COL["muted"], "bold" if bad else "normal", "middle"))
    # tgws
    for t, (cx, cy) in lay["tgw"].items():
        if t not in st["tgws"]:
            continue
        tg = st["tgws"][t]
        up = st["regions"][tg["region"]]["up"]
        hit = t in added_targets
        out.append(f'<circle cx="{cx}" cy="{cy}" r="44" fill="{COL["purple"] if up else COL["grey"]}" stroke="{COL["green"] if hit else "none"}" stroke-width="6"/>')
        out.append(_t(cx, cy - 2, t, 15, "#FFFFFF", "bold", "middle"))
        out.append(_t(cx, cy + 15, st["regions"][tg["region"]]["name"], 11, "#FFFFFF", anchor="middle"))
        d = "defaults ON" if (tg["default_prop"] or tg["default_assoc"]) else "defaults OFF"
        out.append(_t(cx, cy + 62, d, 12, COL["rust"] if d == "defaults ON" else COL["teal"], "bold", "middle"))
        if hit:
            out.append(_t(cx, cy - 52, "routes changed", 12, COL["green"], "bold", "middle"))
    # edges
    for k, (cx, cy) in lay["edge"].items():
        if k not in st["regions"] or not st["edges"].get(k):
            continue
        up = st["regions"][k]["up"]
        out.append(f'<rect x="{cx - 95}" y="{cy - 22}" width="190" height="44" rx="8" fill="{"#F4F6F9" if up else COL["down"]}" stroke="{COL["muted"]}" stroke-width="1.5"/>')
        out.append(_t(cx, cy - 3, f"Palo Alto + SD-WAN {k}", 13, COL["ink"], "bold", "middle"))
        rules = ", ".join(c for c in st["fw"].get(k, []) if c.endswith("/24")) or "no PLC rules"
        out.append(_t(cx, cy + 14, "plant rules: " + rules, 10, COL["muted"], anchor="middle"))
    # vpcs
    for v, vv in st["vpcs"].items():
        if v not in lay["vpc"]:
            continue
        x, y, w, h, hosts = _vpc_box(st, v)
        fill, line = FILL[vv["kind"]]
        down = not st["regions"][vv["region"]]["up"]
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{COL["down"] if down else fill}" stroke="{line}" stroke-width="1.6"/>')
        out.append(_t(x + 10, y + 18, vv["label"], 13, COL["ink"], "bold"))
        out.append(_t(x + 10, y + 34, ", ".join(vv["cidrs"]) + ("" if vv["att"] else "   (not attached)"), 11, COL["muted"]))
        if not hosts:
            out.append(_t(x + 10, y + 56, "empty" if vv["kind"] == "mirror" else "-", 11, COL["muted"], italic=True))
        for i, hh in enumerate(hosts):
            yy = y + 52 + 21 * i
            col = DOT.get(hh["state"], COL["muted"])
            if hh["copy_of"] and hh["state"] == "running":
                col = COL["prodL"]
            out.append(f'<circle cx="{x + 16}" cy="{yy - 4}" r="5.5" fill="{col}"/>')
            out.append(_t(x + 28, yy, hh["name"], 12, COL["ink"], "bold"))
            out.append(_t(x + w - 10, yy, f'{hh["ip"]}  {hh["state"]}', 11, COL["muted"], anchor="end"))
    # plants
    for k, (cx, cy) in lay["plant"].items():
        out.append(f'<rect x="{cx - 120}" y="{cy - 20}" width="240" height="40" rx="8" fill="#FFFFFF" stroke="{COL["navy"]}" stroke-width="1.6"/>')
        out.append(_t(cx, cy + 5, f"Plants {'east' if k == 'A' else 'west'} (PLCs, HMIs)", 13, COL["navy"], "bold", "middle"))
    # path
    if path and path.get("hops"):
        pts = [cen[h] for h in path["hops"] if h in cen]
        if len(pts) >= 2:
            col = {"ok": COL["green"], "blocked": COL["teal"]}.get(path.get("status"), COL["rust"])
            pl = " ".join(f"{x},{y}" for x, y in pts)
            out.append(f'<polyline points="{pl}" fill="none" stroke="{col}" stroke-width="7" stroke-linejoin="round" opacity="0.55" marker-end="url(#arr)"/>')
            if path.get("status") in ("fail", "breach"):
                x, y = pts[-1]
                out.append(f'<g stroke="{COL["rust"]}" stroke-width="7" stroke-linecap="round"><line x1="{x - 16}" y1="{y - 16}" x2="{x + 16}" y2="{y + 16}"/>'
                           f'<line x1="{x - 16}" y1="{y + 16}" x2="{x + 16}" y2="{y - 16}"/></g>')
    # legend
    lx, ly = 20, H - 8
    for i, (lab, col) in enumerate([("running", COL["green"]), ("DR copy running", COL["prodL"]), ("stopped", COL["muted"]), ("down", COL["rust"])]):
        out.append(f'<circle cx="{lx + i * 150}" cy="{ly - 4}" r="5.5" fill="{col}"/>')
        out.append(_t(lx + 10 + i * 150, ly, lab, 11, COL["muted"]))
    if title:
        out.append(_t(W - 16, H - 14, title, 12, COL["muted"], anchor="end"))
    out.append("</svg>")
    return "".join(out)
