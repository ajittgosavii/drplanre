"""Network and server model for the DR simulator.

The state is a plain dict so a step can be replayed by deep-copying it. Routing follows AWS Transit Gateway rules:
a packet entering a TGW through an attachment is looked up in the route table associated with that attachment,
longest prefix wins, and static routes across a peering stay in place even when the peer Region is down.
SD-WAN (plants) and BGP VPNs (vendors) pick the TGW that advertises the most specific prefix (ties: a route that ends
in that Region, then the home / first Region). A Palo Alto is a TGW attachment: it applies 1:1 destination NAT for the
ranges it holds, then the packet is looked up again in the firewall's route table. Paths are forward-only; round trips
are two probes.
"""
import copy
import ipaddress

PLANTS = "172.16.0.0/16"
NAT_SPACE = "100.64.0.0/10"
ORDER = {"A": 0, "B": 1, "C": 2}


def net(c):
    return ipaddress.ip_network(c)


def contains(cidr, ip):
    return ipaddress.ip_address(ip) in net(cidr)


# ---------------------------------------------------------------- building blocks
def new_state(option):
    return {"option": option, "regions": {}, "tgws": {}, "vpcs": {}, "hosts": [], "fw": {}, "edges": {},
            "vendors": {}, "flags": {}}


def add_region(st, key, name):
    st["regions"][key] = {"name": name, "up": True}
    st["fw"].setdefault(key, {"plants": [], "vendors": [], "nat": {}})


def add_vendor(st, key, label, cidr, ip, how, mode="bgp", sel=None):
    """mode bgp = route-based, follows the most specific advert of any live tunnel.
    mode policy = policy-based IPsec: sel maps destination prefix -> ordered Regions whose tunnel carries it
    (first live one wins; a second Region = backup peer pre-configured on the vendor side)."""
    st["vendors"][key] = {"label": label, "cidr": cidr, "ip": ip, "how": how, "mode": mode, "sel": dict(sel or {})}


def policy_region(st, vendor, ip):
    """Region whose tunnel a policy-based vendor uses for ip, or None (no selector / no live tunnel)."""
    v = st["vendors"][vendor]
    best = None
    for c, regs in v["sel"].items():
        if contains(c, ip) and (best is None or net(c).prefixlen > net(best).prefixlen):
            best = c
    if best is None:
        return None, None
    for r in v["sel"][best]:
        if st["regions"].get(r, {}).get("up") and any(t["region"] == r and any(a["type"] == "vpn" and a["vendor"] == vendor for a in t["atts"].values())
                                                       for t in st["tgws"].values()):
            return r, best
    return None, best


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
        cidrs = st["vpcs"][a["vpc"]]["cidrs"]
    elif a["type"] == "sdwan":
        cidrs = [PLANTS]
    elif a["type"] == "vpn":
        cidrs = [st["vendors"][a["vendor"]]["cidr"]]
    else:
        cidrs = []
    for c in cidrs:
        add_route(st, tgw, rt, c, att, "propagated")


def unpropagate(st, tgw, att, rt=None):
    for name, r in st["tgws"][tgw]["rts"].items():
        if rt and name != rt:
            continue
        r["routes"][:] = [x for x in r["routes"] if not (x["target"] == att and x["kind"] == "propagated")]


def attach(st, tgw, att, vpc=None, kind="vpc", peer=None, assoc=None, prop=None, vendor=None):
    """Create an attachment. assoc / prop None = the TGW's defaults decide (the real-world trap)."""
    t = st["tgws"][tgw]
    t["atts"][att] = {"type": kind, "vpc": vpc, "peer": peer, "vendor": vendor}
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


def split_halves(cidr):
    """The two more-specific halves of a prefix - lets a cutover ADD routes over a same-length route instead of replacing it."""
    return [str(n) for n in net(cidr).subnets(prefixlen_diff=1)]


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


def tgw_of_region(st, reg):
    return next((t for t, v in st["tgws"].items() if v["region"] == reg), None)


def sdwan_rt(st, tgw):
    t = st["tgws"][tgw]
    att = next((a for a, v in t["atts"].items() if v["type"] == "sdwan"), None)
    return att, (rt_of(st, tgw, att) if att else None)


def _ingresses(st, kind, key):
    """(tgw, attachment) pairs through which plants (kind=plant) or a vendor (kind=vendor) can enter AWS."""
    out = []
    for tgw, t in st["tgws"].items():
        if not tgw_up(st, tgw):
            continue
        for a, v in t["atts"].items():
            if kind == "plant" and v["type"] == "sdwan" and st["edges"].get(t["region"]):
                out.append((tgw, a))
            if kind == "vendor" and v["type"] == "vpn" and v["vendor"] == key:
                out.append((tgw, a))
    return out


def ingress_choice(st, ip, kind, key, home=None):
    """Best entry for ip: (score, region, tgw, attachment, prefix) or None. Most specific advert wins, then a route that
    ends in that Region, then the home Region, then the first Region."""
    best = None
    for tgw, att in _ingresses(st, kind, key):
        t = st["tgws"][tgw]
        rt = rt_of(st, tgw, att)
        if not rt:
            continue
        skip = "sdwan" if kind == "plant" else "vpn"
        adv = [r for r in t["rts"][rt]["routes"] if t["atts"][r["target"]]["type"] != skip]
        m = lpm(adv, ip)
        if not m:
            continue
        reg = t["region"]
        local = _ends_local(st, tgw, m["target"], ip)
        score = (net(m["cidr"]).prefixlen, local, reg == home, -ORDER[reg])
        if best is None or score > best[0]:
            best = (score, reg, tgw, att, m["cidr"])
    return best


def _ends_local(st, tgw, att, ip):
    """True when a route's target delivers ip in this Region (looks through a firewall attachment to its own table)."""
    t = st["tgws"][tgw]
    a = t["atts"][att]
    if a["type"] == "firewall":
        rt = rt_of(st, tgw, att)
        m = lpm(t["rts"][rt]["routes"], ip) if rt else None
        return bool(m) and t["atts"][m["target"]]["type"] not in ("peering", "firewall")
    return a["type"] != "peering"


def advertised(st, tgw, kind="plant", key=None):
    """Prefixes this TGW advertises to the plants (SD-WAN) or to a vendor (its VPN)."""
    t = st["tgws"][tgw]
    for t_, att in _ingresses(st, kind, key):
        if t_ != tgw:
            continue
        rt = rt_of(st, tgw, att)
        skip = "sdwan" if kind == "plant" else "vpn"
        return sorted({r["cidr"] for r in t["rts"][rt]["routes"] if t["atts"][r["target"]]["type"] != skip},
                      key=lambda c: (net(c).prefixlen, c))
    return []


def _res(ok, reason, hops, text, **kw):
    d = {"ok": ok, "reason": reason, "hops": hops, "path": text, "firewalls": [], "dnat": None}
    d.update(kw)
    return d


def _deliver(st, vpc, ip, hops, text, ctx):
    v = st["vpcs"][vpc]
    hops.append(vpc)
    if not st["regions"][v["region"]]["up"]:
        return _res(False, f"{v['label']} is in a Region that is down", hops, text, **ctx)
    hs = [h for h in st["hosts"] if h["vpc"] == vpc and h["ip"] == ip]
    run = [h for h in hs if h["state"] == "running"]
    if run:
        h = run[0]
        what = f"DR copy of {h['copy_of']}" if h["copy_of"] else h["name"]
        text.append(f"delivered to {what} ({ip})")
        return _res(True, f"served by {what} in {v['label']}", hops, text, served_by=h["name"], served_vpc=vpc, **ctx)
    if hs:
        return _res(False, f"{hs[0]['name']} in {v['label']} is {hs[0]['state']}", hops, text, **ctx)
    if v["kind"] == "mirror":
        return _res(False, f"traffic arrives in the EMPTY {v['label']} - nothing runs there", hops, text, **ctx)
    return _res(False, f"no server with {ip} in {v['label']}", hops, text, **ctx)


def send(st, src, ip, from_ip=None):
    """src = ("vpc", name) | ("plant", region_key) | ("vendor", vendor_key).
    Returns ok, reason, hops (diagram node ids), path text, firewalls traversed, dnat (Region that translated)."""
    hops, text = [], []
    ctx = {"firewalls": [], "dnat": None}
    if src[0] in ("plant", "vendor"):
        kind, key = src
        hops.append(f"{kind}-{key}")
        who = "SD-WAN" if kind == "plant" else f"{st['vendors'][key]['label']} VPN"
        if kind == "vendor" and st["vendors"][key]["mode"] == "policy":
            reg, sel = policy_region(st, key, ip)
            if not reg:
                why = "no selector for it" if sel is None else f"selector {sel} only on tunnels that are down - no backup peer"
                return _res(False, f"{who} (policy-based) cannot send to {ip}: {why}", hops, text, **ctx)
            tgw = tgw_of_region(st, reg)
            att = next(a for a, v in st["tgws"][tgw]["atts"].items() if v["type"] == "vpn" and v["vendor"] == key)
            prefix = f"selector {sel}"
        else:
            ch = ingress_choice(st, ip, kind, key, home=key if kind == "plant" else None)
            if not ch:
                return _res(False, f"{who} has no route to {ip}", hops, text, **ctx)
            _, reg, tgw, att, prefix = ch
        hops.append("edge-" + reg)
        text.append(f"{who} -> {st['regions'][reg]['name']} (best advert {prefix})")
        rules = st["fw"][reg]["plants" if kind == "plant" else "vendors"]
        if not any(contains(c, ip) for c in rules):
            src_lbl = "plants" if kind == "plant" else st["vendors"][key]["label"]
            return _res(False, f"Palo Alto {st['regions'][reg]['name']}: no rule for {src_lbl} -> {ip} (dropped)", hops, text, **ctx)
        text.append(f"Palo Alto {st['regions'][reg]['name']} allows")
        cur_tgw, cur_att = tgw, att
    else:
        vpc = st["vpcs"][src[1]]
        hops.append(src[1])
        if not st["regions"][vpc["region"]]["up"]:
            return _res(False, f"source {vpc['label']} is down", hops, text, **ctx)
        if any(contains(c, ip) for c in vpc["cidrs"]):
            return _deliver(st, src[1], ip, hops[:-1], ["local VPC route"], ctx)
        if not vpc["att"]:
            return _res(False, f"{vpc['label']} is not attached to any TGW - no path out (isolated)", hops, text, **ctx)
        cur_tgw, cur_att = vpc["att"]
    for _ in range(14):
        hops.append(cur_tgw)
        if not tgw_up(st, cur_tgw):
            return _res(False, f"{cur_tgw} is in a Region that is down", hops, text, **ctx)
        rt = rt_of(st, cur_tgw, cur_att)
        if not rt:
            return _res(False, f"{cur_tgw}: attachment {cur_att} has no route-table association", hops, text, **ctx)
        m = lpm(st["tgws"][cur_tgw]["rts"][rt]["routes"], ip)
        if not m:
            return _res(False, f"{cur_tgw} table '{rt}': no route to {ip} (blackhole)", hops, text, **ctx)
        text.append(f"{cur_tgw} [{rt}] {m['cidr']} -> {m['target']}" + (" (propagated)" if m["kind"] == "propagated" else ""))
        a = st["tgws"][cur_tgw]["atts"][m["target"]]
        if a["type"] == "vpc":
            return _deliver(st, a["vpc"], ip, hops, text, ctx)
        if a["type"] == "sdwan":
            hops.append("edge-" + st["tgws"][cur_tgw]["region"])
            ok = contains(PLANTS, ip)
            return _res(ok, "delivered to the plants over SD-WAN" if ok else "sent to SD-WAN, unknown destination", hops, text, **ctx)
        if a["type"] == "vpn":
            v = st["vendors"][a["vendor"]]
            hops.append("vendor-" + a["vendor"])
            ok = contains(v["cidr"], ip)
            if ok and v["mode"] == "policy" and from_ip:
                want, sel = policy_region(st, a["vendor"], from_ip)
                here = st["tgws"][cur_tgw]["region"]
                if want != here:
                    return _res(False, f"{v['label']} (policy-based): reply arrives on the {st['regions'][here]['name']} tunnel but its selector "
                                f"for {from_ip} is on {st['regions'][want]['name'] if want else 'no live tunnel'} - dropped", hops, text, **ctx)
            return _res(ok, f"delivered to {v['label']} over its VPN" if ok else f"sent to {v['label']} VPN, wrong destination", hops, text, **ctx)
        if a["type"] == "firewall":
            reg = st["tgws"][cur_tgw]["region"]
            hops.append("edge-" + reg)
            fw = st["fw"][reg]
            ctx["firewalls"].append(reg)
            nat = next(((nc, real) for nc, real in fw["nat"].items() if contains(nc, ip)), None)
            if nat:
                new = str(net(nat[1]).network_address + (int(ipaddress.ip_address(ip)) - int(net(nat[0]).network_address)))
                text.append(f"Palo Alto {st['regions'][reg]['name']} DNAT {ip} -> {new}")
                ip, ctx["dnat"] = new, reg
            else:
                text.append(f"Palo Alto {st['regions'][reg]['name']} inspects")
            cur_att = m["target"]            # re-enter the same TGW from the firewall attachment
            continue
        nxt_tgw, nxt_att = a["peer"]
        if not tgw_up(st, nxt_tgw):
            hops.append(nxt_tgw)
            return _res(False, f"peering {m['target']}: the peer Region is down - static route still points there (blackhole)", hops, text, **ctx)
        cur_tgw, cur_att = nxt_tgw, nxt_att
    return _res(False, "routing loop", hops, text, **ctx)


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
