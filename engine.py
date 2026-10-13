"""Network and server model for the DR simulator.

The state is a plain dict so a step can be replayed by deep-copying it. Routing follows AWS Transit Gateway rules:
a packet entering a TGW through an attachment is looked up in the route table associated with that attachment,
longest prefix wins, and static routes across a peering stay in place even when the peer Region is down.
SD-WAN picks the edge that advertises the most specific prefix (ties: an edge whose route ends in a local VPC,
then the plant's home Region). Paths are forward-only; every probe is one direction.
"""
import copy
import ipaddress

PLANTS = "172.16.0.0/16"


def net(c):
    return ipaddress.ip_network(c)


def contains(cidr, ip):
    return ipaddress.ip_address(ip) in net(cidr)


# ---------------------------------------------------------------- building blocks
def new_state(option):
    return {"option": option, "regions": {}, "tgws": {}, "vpcs": {}, "hosts": [], "fw": {}, "edges": {},
            "flags": {}, "clock": 0}


def add_region(st, key, name):
    st["regions"][key] = {"name": name, "up": True}
    st["fw"].setdefault(key, [])


def add_tgw(st, name, region, default_assoc=True, default_prop=True, assoc_rt="spoke", prop_rts=("spoke",), rts=("spoke",)):
    st["tgws"][name] = {"region": region, "default_assoc": default_assoc, "default_prop": default_prop,
                        "assoc_rt": assoc_rt, "prop_rts": list(prop_rts), "atts": {},
                        "rts": {r: {"assoc": [], "routes": []} for r in rts}}


def add_vpc(st, name, region, cidrs, kind, label):
    st["vpcs"][name] = {"region": region, "cidrs": list(cidrs), "kind": kind, "label": label, "att": None}


def add_host(st, name, ip, vpc, role, state="running", copy_of=None):
    st["hosts"].append({"name": name, "ip": ip, "vpc": vpc, "role": role, "state": state, "copy_of": copy_of})


def host(st, name):
    return next(h for h in st["hosts"] if h["name"] == name)


def remove_host(st, name):
    st["hosts"] = [h for h in st["hosts"] if h["name"] != name]


def add_route(st, tgw, rt, cidr, target, kind="static"):
    routes = st["tgws"][tgw]["rts"][rt]["routes"]
    routes[:] = [r for r in routes if r["cidr"] != cidr]
    routes.append({"cidr": cidr, "target": target, "kind": kind})


def del_route(st, tgw, rt, cidr):
    routes = st["tgws"][tgw]["rts"][rt]["routes"]
    routes[:] = [r for r in routes if r["cidr"] != cidr]


def associate(st, tgw, att, rt):
    for r in st["tgws"][tgw]["rts"].values():
        if att in r["assoc"]:
            r["assoc"].remove(att)
    st["tgws"][tgw]["rts"][rt]["assoc"].append(att)


def propagate(st, tgw, att, rt):
    a = st["tgws"][tgw]["atts"][att]
    if a["type"] == "vpc":
        for c in st["vpcs"][a["vpc"]]["cidrs"]:
            add_route(st, tgw, rt, c, att, "propagated")
    elif a["type"] == "sdwan":
        add_route(st, tgw, rt, PLANTS, att, "propagated")


def unpropagate(st, tgw, att, rt=None):
    for name, r in st["tgws"][tgw]["rts"].items():
        if rt and name != rt:
            continue
        r["routes"][:] = [x for x in r["routes"] if not (x["target"] == att and x["kind"] == "propagated")]


def attach(st, tgw, att, vpc=None, kind="vpc", peer=None, assoc=None, prop=None):
    """Create an attachment. assoc / prop None = the TGW's defaults decide (the real-world trap)."""
    t = st["tgws"][tgw]
    t["atts"][att] = {"type": kind, "vpc": vpc, "peer": peer}
    if vpc:
        st["vpcs"][vpc]["att"] = (tgw, att)
    if assoc is None and t["default_assoc"]:
        assoc = t["assoc_rt"]
    if assoc:
        associate(st, tgw, att, assoc)
    if kind == "peering":
        return                                   # peerings never propagate
    rts = prop if prop is not None else (t["prop_rts"] if t["default_prop"] else [])
    for rt in rts:
        propagate(st, tgw, att, rt)


def peer(st, tgw1, tgw2, att, rt1, rt2):
    attach(st, tgw1, att, kind="peering", peer=(tgw2, att), assoc=rt1)
    attach(st, tgw2, att, kind="peering", peer=(tgw1, att), assoc=rt2)


def region_down(st, key):
    st["regions"][key]["up"] = False
    for h in st["hosts"]:
        if st["vpcs"][h["vpc"]]["region"] == key and h["state"] == "running":
            h["state"] = "down"


def region_up(st, key, start_hosts=False):
    st["regions"][key]["up"] = True
    for h in st["hosts"]:
        if st["vpcs"][h["vpc"]]["region"] == key and h["state"] == "down":
            h["state"] = "running" if start_hosts else "stopped"


# ---------------------------------------------------------------- lookups
def lpm(routes, ip):
    best = None
    for r in routes:
        if contains(r["cidr"], ip) and (best is None or net(r["cidr"]).prefixlen > net(best["cidr"]).prefixlen):
            best = r
    return best


def rt_of(st, tgw, att):
    for name, r in st["tgws"][tgw]["rts"].items():
        if att in r["assoc"]:
            return name
    return None


def tgw_up(st, tgw):
    return st["regions"][st["tgws"][tgw]["region"]]["up"]


def sdwan_rt(st, tgw):
    t = st["tgws"][tgw]
    att = next((a for a, v in t["atts"].items() if v["type"] == "sdwan"), None)
    return att, (rt_of(st, tgw, att) if att else None)


def sdwan_choice(st, ip, site_region):
    """Which Region's SD-WAN edge the plants use for ip: (score, region, tgw, prefix) or None."""
    best = None
    for tgw, t in st["tgws"].items():
        reg = t["region"]
        if not st["edges"].get(reg) or not tgw_up(st, tgw):
            continue
        att, rt = sdwan_rt(st, tgw)
        if not rt:
            continue
        adv = [r for r in t["rts"][rt]["routes"] if r["target"] != att]
        m = lpm(adv, ip)
        if not m:
            continue
        local = t["atts"][m["target"]]["type"] == "vpc"
        score = (net(m["cidr"]).prefixlen, local, reg == site_region)
        if best is None or score > best[0]:
            best = (score, reg, tgw, m["cidr"])
    return best


def advertised(st, tgw):
    """Prefixes the SD-WAN edge of this TGW advertises to the plants."""
    att, rt = sdwan_rt(st, tgw)
    if not rt or not st["edges"].get(st["tgws"][tgw]["region"]):
        return []
    return sorted({r["cidr"] for r in st["tgws"][tgw]["rts"][rt]["routes"] if r["target"] != att}, key=lambda c: (net(c).prefixlen, c))


def _deliver(st, vpc, ip, hops, text):
    v = st["vpcs"][vpc]
    hops.append(vpc)
    if not st["regions"][v["region"]]["up"]:
        return {"ok": False, "hops": hops, "path": text, "reason": f"{v['label']} is in a Region that is down"}
    hs = [h for h in st["hosts"] if h["vpc"] == vpc and h["ip"] == ip]
    run = [h for h in hs if h["state"] == "running"]
    if run:
        h = run[0]
        what = f"DR copy of {h['copy_of']}" if h["copy_of"] else h["name"]
        text.append(f"delivered to {what} ({ip})")
        return {"ok": True, "hops": hops, "path": text, "reason": f"served by {what} in {v['label']}", "served_by": h["name"]}
    if hs:
        return {"ok": False, "hops": hops, "path": text, "reason": f"{hs[0]['name']} in {v['label']} is {hs[0]['state']}"}
    if v["kind"] == "mirror":
        return {"ok": False, "hops": hops, "path": text, "reason": f"traffic arrives in the EMPTY {v['label']} - nothing runs there"}
    return {"ok": False, "hops": hops, "path": text, "reason": f"no server with {ip} in {v['label']}"}


def send(st, src, ip):
    """src = ("vpc", name) or ("plant", region_key). Returns ok, hops (diagram node ids), path text, reason."""
    hops, text = [], []
    if src[0] == "plant":
        hops.append("plant-" + src[1])
        ch = sdwan_choice(st, ip, src[1])
        if not ch:
            return {"ok": False, "hops": hops, "path": text, "reason": "SD-WAN has no route to " + ip}
        _, reg, tgw, prefix = ch
        hops.append("edge-" + reg)
        text.append(f"SD-WAN -> {st['regions'][reg]['name']} edge (best advert {prefix})")
        if not any(contains(c, ip) for c in st["fw"].get(reg, [])):
            return {"ok": False, "hops": hops, "path": text, "reason": f"Palo Alto {st['regions'][reg]['name']}: no rule for plants -> {ip} (dropped)"}
        text.append(f"Palo Alto {st['regions'][reg]['name']} allows")
        cur_tgw, cur_att = tgw, sdwan_rt(st, tgw)[0]
    else:
        vpc = st["vpcs"][src[1]]
        hops.append(src[1])
        if not st["regions"][vpc["region"]]["up"]:
            return {"ok": False, "hops": hops, "path": text, "reason": f"source {vpc['label']} is down"}
        if any(contains(c, ip) for c in vpc["cidrs"]):
            return _deliver(st, src[1], ip, hops[:-1], ["local VPC route"])
        if not vpc["att"]:
            return {"ok": False, "hops": hops, "path": text, "reason": f"{vpc['label']} is not attached to any TGW - no path out (isolated)"}
        cur_tgw, cur_att = vpc["att"]
    for _ in range(8):
        hops.append(cur_tgw)
        if not tgw_up(st, cur_tgw):
            return {"ok": False, "hops": hops, "path": text, "reason": f"{cur_tgw} is in a Region that is down"}
        rt = rt_of(st, cur_tgw, cur_att)
        if not rt:
            return {"ok": False, "hops": hops, "path": text, "reason": f"{cur_tgw}: attachment {cur_att} has no route-table association"}
        m = lpm(st["tgws"][cur_tgw]["rts"][rt]["routes"], ip)
        if not m:
            return {"ok": False, "hops": hops, "path": text, "reason": f"{cur_tgw} table '{rt}': no route to {ip} (blackhole)"}
        text.append(f"{cur_tgw} [{rt}] {m['cidr']} -> {m['target']}" + (" (propagated)" if m["kind"] == "propagated" else ""))
        a = st["tgws"][cur_tgw]["atts"][m["target"]]
        if a["type"] == "vpc":
            return _deliver(st, a["vpc"], ip, hops, text)
        if a["type"] == "sdwan":
            hops.append("edge-" + st["tgws"][cur_tgw]["region"])
            ok = contains(PLANTS, ip)
            return {"ok": ok, "hops": hops, "path": text, "reason": "delivered to the plants" if ok else "sent to SD-WAN, unknown destination"}
        nxt_tgw, nxt_att = a["peer"]
        if not tgw_up(st, nxt_tgw):
            hops.append(nxt_tgw)
            return {"ok": False, "hops": hops, "path": text, "reason": f"peering {m['target']}: the peer Region is down - static route still points there (blackhole)"}
        cur_tgw, cur_att = nxt_tgw, nxt_att
    return {"ok": False, "hops": hops, "path": text, "reason": "routing loop"}


# ---------------------------------------------------------------- snapshots / diffs
def route_set(st):
    out = set()
    for tg, t in st["tgws"].items():
        for rn, r in t["rts"].items():
            for x in r["routes"]:
                out.add((tg, rn, x["cidr"], x["target"], x["kind"]))
    return out


def route_diff(before, after):
    a, b = route_set(before), route_set(after)
    return sorted(b - a), sorted(a - b)


def split_brain(st):
    seen = {}
    for h in st["hosts"]:
        if h["state"] == "running" and h["role"] in ("db", "app"):
            seen.setdefault(h["ip"], []).append(h["name"])
    return {ip: n for ip, n in seen.items() if len(n) > 1}


def clone(st):
    return copy.deepcopy(st)
