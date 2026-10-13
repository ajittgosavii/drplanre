"""DR Architecture Simulator - step through both production DR options.

Option 1: us-east-1 and us-west-2 recover into each other.
Option 2: both recover into a dedicated DR Region us-east-2 in a separate DR account.
Pure simulation: no AWS calls. Run: streamlit run app.py
"""
import base64

import pandas as pd
import streamlit as st

import engine as E
import render as R
import scenarios as S

st.set_page_config(page_title="DR Architecture Simulator", page_icon="🛰️", layout="wide")

OPTIONS = {1: "Option 1 - two Regions (each the other's DR)", 2: "Option 2 - dedicated DR Region us-east-2"}
RISK = {"HIGH": "#C8553D", "HIGH*": "#C8553D", "MED-HIGH": "#E9A23B", "MEDIUM": "#E9A23B", "LOW": "#2E7D32", "UNPROVEN": "#5B4B8A"}
ICON = {"ok": "✅", "fail": "❌", "blocked": "🛡️", "breach": "⚠️", "n/a": "·"}

st.markdown("""<style>
.block-container{padding-top:1.4rem}
.chip{display:inline-block;padding:2px 10px;border-radius:12px;font-size:0.82rem;margin-right:6px;color:#fff}
.card{background:#F4F6F9;border-radius:10px;padding:14px 18px;margin-bottom:10px}
.small{color:#7D8998;font-size:0.85rem}
</style>""", unsafe_allow_html=True)


def dur(m):
    if m < 120:
        return f"{m:.0f} min"
    if m < 480:
        return f"{m / 60:.1f} h"
    return f"{m / 480:.0f} working days"


def show_svg(svg):
    b64 = base64.b64encode(svg.encode()).decode()
    st.markdown(f'<img src="data:image/svg+xml;base64,{b64}" style="width:100%"/>', unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load(name, option, whatif):
    steps, start = S.scenario(name, option, frozenset(whatif))
    states = [S.build(steps, start, i) for i in range(len(steps))]
    meta = [{k: v for k, v in s.items() if k != "fn"} for s in steps]
    return meta, states


def probe_rows(state, option):
    rows = []
    for p in S.probes(option):
        r = S.run_probe(state, p)
        if r["status"] == "n/a" and p["group"] in ("Recovered", "Drill isolation"):
            continue
        rows.append({"": ICON[r["status"]], "Group": p["group"], "Probe": p["label"], "Destination": p["ip"],
                     "Result": r["reason"], "Path": "  →  ".join(r.get("path", [])), "_id": p["id"], "_status": r["status"]})
    return rows


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### 🛰️ DR Architecture Simulator")
    st.caption("Same-IP recovery with AWS Elastic Disaster Recovery. Pure simulation - addresses and timings are illustrative.")
    option = st.radio("Design", [1, 2], format_func=lambda o: OPTIONS[o])
    scen = st.selectbox("Scenario", list(S.SCENARIOS))
    no_fw = st.checkbox("What-if: firewall PLC rules NOT pre-staged", help="The recovery Region's Palo Alto has no rules for the failed Region's PLC subnet.")
    vb = st.checkbox("What-if: vendors pre-configured a backup peer", help="Policy-based vendor VPNs carry the other Region's selectors on a backup peer (DPD), so they move tunnels on their own.")
    whatif = tuple(x for x, on in (("no_fw", no_fw), ("vendor_backup", vb)) if on)

meta, states = load(scen, option, whatif)
n = len(meta)
key = f"step::{option}::{scen}::{whatif}"
if key not in st.session_state:
    st.session_state[key] = 0


def go(i):
    st.session_state[key] = max(0, min(n - 1, i))


tab_sim, tab_side, tab_cmp, tab_about = st.tabs(["▶ Simulator", "⇆ Side by side", "📊 Compare", "ℹ About"])

# ================================================================ simulator
with tab_sim:
    st.markdown(f"#### {OPTIONS[option]}  ·  {scen}")
    c = st.columns([1, 1, 1, 1, 6])
    c[0].button("⏮ Start", on_click=go, args=(0,), width="stretch")
    c[1].button("◀ Back", on_click=go, args=(st.session_state[key] - 1,), width="stretch")
    c[2].button("Next ▶", on_click=go, args=(st.session_state[key] + 1,), width="stretch", type="primary")
    c[3].button("End ⏭", on_click=go, args=(n - 1,), width="stretch")
    with c[4]:
        st.slider("Step", 0, n - 1, key=key, label_visibility="collapsed")
    i = st.session_state[key]
    s, state = meta[i], states[i]
    prev = states[i - 1] if i else states[0]
    added, removed = E.route_diff(prev, state) if i else ([], [])
    clock = sum(m["minutes"] for m in meta[1:i + 1])

    rows = probe_rows(state, option)
    right, right2 = st.columns([3, 2], gap="large")
    with right:
        risk = f'<span class="chip" style="background:{RISK[s["risk"]]}">{s["risk"]}</span>' if s["risk"] else ""
        st.markdown(f'<div class="card"><div class="small">Step {i} of {n - 1} · {s["phase"] or ""} · elapsed {dur(clock)}</div>'
                    f'<div style="font-size:1.25rem;font-weight:700;margin:4px 0 6px 0">{s["title"]}</div>'
                    f'<span class="chip" style="background:#0B3C5D">{s["actor"]}</span>'
                    f'<span class="chip" style="background:#7D8998">{dur(s["minutes"]) if s["minutes"] else "instant"}</span>{risk}'
                    f'<p style="margin-top:10px">{s["text"]}</p></div>', unsafe_allow_html=True)
    with right2:
        if not (added or removed or s["commands"] or s["checks"]):
            st.caption("No route change, command or check in this step.")
        if added or removed:
            st.markdown("**Route changes in this step**")
            for t, rt, cidr, tgt, kind in added:
                st.markdown(f"🟢 `{t}` · {rt} · **{cidr}** → {tgt}" + (" *(propagated)*" if kind == "propagated" else ""))
            for t, rt, cidr, tgt, kind in removed:
                st.markdown(f"🔴 `{t}` · {rt} · ~~{cidr} → {tgt}~~")
        if s["commands"]:
            st.markdown("**Commands**")
            st.code("\n".join(s["commands"]), language="bash")
        if s["checks"]:
            st.markdown("**Checks**  \n" + "  \n".join("☐ " + x for x in s["checks"]))
        sb = E.split_brain(state)
        if sb:
            st.error("Split brain: " + "; ".join(f"{ip} runs on {', '.join(v)}" for ip, v in sb.items()))
        bad = [r for r in rows if r["_status"] in ("fail", "breach")]
        if bad:
            st.warning(f"{len(bad)} probe(s) failing - see Traffic probes.")
        else:
            st.success("All applicable probes behave as required.")
    labels = {r["_id"]: r["Probe"] for r in rows}
    pick = st.selectbox("Show path on the diagram", list(labels), format_func=lambda k: labels[k], key="pick-" + str(option))
    p = next(x for x in S.probes(option) if x["id"] == pick)
    res = S.run_probe(state, p)
    touched = {a[0] for a in added + removed}
    show_svg(R.svg(state, res, touched, f"{OPTIONS[option]} - step {i}"))
    st.caption(f'**{p["label"]}** → {res["reason"]}' + (f'  ·  {"  →  ".join(res.get("path", []))}' if res.get("path") else ""))

    t1, t2, t3, t4, t5 = st.tabs(["Traffic probes", "Route tables", "Servers", "SD-WAN / VPN adverts", "Timeline"])
    with t1:
        df = pd.DataFrame(rows).drop(columns=["_id", "_status"])
        st.dataframe(df, hide_index=True, width="stretch", column_config={"": st.column_config.TextColumn(width="small")})
        with st.expander("Custom route lookup"):
            src_opts = [("plant", "A"), ("plant", "B")] + [("vendor", v) for v in state["vendors"]] + [("vpc", v) for v in state["vpcs"]]
            cc = st.columns([2, 2, 1])
            src = cc[0].selectbox("From", src_opts, format_func=lambda x: f"Plants {'east' if x[1] == 'A' else 'west'}" if x[0] == "plant" else (state["vendors"][x[1]]["label"] if x[0] == "vendor" else state["vpcs"][x[1]]["label"]))
            dst = cc[1].text_input("To IP", "10.10.1.20")
            try:
                rr = E.send(state, src, dst.strip())
                cc[2].markdown("### " + ("✅" if rr["ok"] else "❌"))
                st.write(rr["reason"])
                st.code("\n".join(rr["path"]) or "-")
            except ValueError:
                st.error("Not a valid IPv4 address")
    with t2:
        addset = {(a[0], a[1], a[2]) for a in added}
        tg = list(state["tgws"])
        cols = st.columns(len(tg))
        for col, t in zip(cols, tg):
            with col:
                tt = state["tgws"][t]
                st.markdown(f'**{t}** · {state["regions"][tt["region"]]["name"]}  \n'
                            f'<span class="small">default association {"ON" if tt["default_assoc"] else "OFF"}, propagation {"ON" if tt["default_prop"] else "OFF"}</span>',
                            unsafe_allow_html=True)
                for rn, rt in tt["rts"].items():
                    st.markdown(f"*{rn}* — associated: {', '.join(rt['assoc']) or '-'}")
                    d = pd.DataFrame([{"Destination": r["cidr"], "Target": r["target"], "Type": r["kind"], "new": (t, rn, r["cidr"]) in addset}
                                      for r in sorted(rt["routes"], key=lambda r: (E.net(r["cidr"]).prefixlen, r["cidr"]))])
                    if d.empty:
                        st.caption("no routes")
                        continue
                    sty = d.style.apply(lambda row: ["background-color:#DFF3DA" if row["new"] else ""] * len(row), axis=1)
                    st.dataframe(sty, hide_index=True, width="stretch", column_config={"new": None})
    with t3:
        hd = pd.DataFrame([{"Server": h["name"], "IP": h["ip"], "Where": state["vpcs"][h["vpc"]]["label"],
                            "Region": state["regions"][state["vpcs"][h["vpc"]]["region"]]["name"], "Role": h["role"],
                            "State": h["state"], "Copy of": h["copy_of"] or ""} for h in state["hosts"]])
        st.dataframe(hd, hide_index=True, width="stretch")
        f = state["flags"]
        st.markdown(f'Replication: **{"CONTINUOUS" if f.get("agents") else "not protected"}**' + (" · reverse replication **ON**" if f.get("reverse") else "")
                    + (f' · fenced: **{state["regions"][f["fenced"]]["name"]}**' if f.get("fenced") else ""))
    with t4:
        for t in state["tgws"]:
            adv = E.advertised(state, t)
            if adv:
                st.markdown(f"**SD-WAN edge {state['tgws'][t]['region']}** ({state['regions'][state['tgws'][t]['region']]['name']}"
                            f"{', DOWN' if not E.tgw_up(state, t) else ''}) advertises: " + ", ".join(f"`{a}`" for a in adv))
        st.caption("Plants pick the most specific prefix; ties go to the edge whose route ends in that Region, then to the plant's home Region.")
        st.markdown("**Policy-based VPN selectors** (destination -> tunnel Region, in order)")
        for vk, vv in state["vendors"].items():
            if vv["mode"] == "policy":
                st.markdown(f"{vv['label']}: " + ", ".join(f"`{c}` -> {' then '.join(state['regions'][r]['name'] for r in regs)}" for c, regs in vv["sel"].items()))
        st.markdown("**BGP VPN adverts**")
        for vk, vv in state["vendors"].items():
            if vv["mode"] != "bgp":
                continue
            for t in state["tgws"]:
                adv = E.advertised(state, t, "vendor", vk)
                if adv:
                    st.markdown(f"{vv['label']} via **{t}** ({state['regions'][state['tgws'][t]['region']]['name']}): " + ", ".join(f"`{a}`" for a in adv))
    with t5:
        tl = pd.DataFrame([{"#": j, "": "▶" if j == i else ("✓" if j < i else ""), "Phase": m["phase"], "Step": m["title"], "Who": m["actor"],
                            "Duration": dur(m["minutes"]) if m["minutes"] else "-", "Risk": m["risk"] or ""} for j, m in enumerate(meta)])
        st.dataframe(tl, hide_index=True, width="stretch")

# ================================================================ side by side
with tab_side:
    st.markdown(f"#### Both designs · {scen}")
    cols = st.columns(2, gap="large")
    for col, o in zip(cols, (1, 2)):
        with col:
            m2, s2 = load(scen, o, whatif)
            j = st.slider(OPTIONS[o], 0, len(m2) - 1, len(m2) - 1, key=f"side-{o}-{scen}")
            st.markdown(f"**{j}. {m2[j]['title']}**  \n<span class='small'>{m2[j]['actor']} · elapsed {dur(sum(x['minutes'] for x in m2[1:j + 1]))}</span>", unsafe_allow_html=True)
            rws = probe_rows(s2[j], o)
            pr = next(x for x in S.probes(o) if x["id"] == "p1")
            show_svg(R.svg(s2[j], S.run_probe(s2[j], pr), {a[0] for a in sum(E.route_diff(s2[j - 1], s2[j]), [])} if j else set()))
            st.dataframe(pd.DataFrame(rws)[["", "Probe", "Result"]], hide_index=True, width="stretch")

# ================================================================ compare
with tab_cmp:
    st.markdown("#### Scenario metrics - computed from the simulation")
    recs = []
    for name in S.SCENARIOS:
        for o in (1, 2):
            m2, s2 = load(name, o, whatif)
            adds = sum(len(E.route_diff(s2[j - 1], s2[j])[0]) for j in range(1, len(s2)))
            dels = sum(len(E.route_diff(s2[j - 1], s2[j])[1]) for j in range(1, len(s2)))
            tg = {d[0] for j in range(1, len(s2)) for d in sum(E.route_diff(s2[j - 1], s2[j]), [])}
            worst = max(sum(1 for r in probe_rows(x, o) if r["_status"] in ("fail", "breach")) for x in s2)
            end = sum(1 for r in probe_rows(s2[-1], o) if r["_status"] in ("fail", "breach"))
            recs.append({"Scenario": name, "Design": f"Option {o}", "Steps": len(m2) - 1, "Duration": dur(sum(x["minutes"] for x in m2)),
                         "Route adds": adds, "Route deletes": dels, "TGWs changed": ", ".join(sorted(tg)) or "-",
                         "HIGH-risk steps": sum(1 for x in m2 if (x["risk"] or "").startswith("HIGH")),
                         "Worst failing probes": worst, "Failing at end": end})
    st.dataframe(pd.DataFrame(recs), hide_index=True, width="stretch")
    st.caption("Route adds / deletes count every route-table entry (propagated and static). Failing probes count production, replication and recovered-copy probes plus drill-isolation breaches.")
    st.markdown("#### What the numbers say")
    a, b = st.columns(2)
    a.markdown("""**Option 1 - two Regions**
- One route set at failover (surviving TGW), smaller build.
- Every mirror attachment happens in a **production** TGW with defaults ON: the S1 window is a HIGH-risk step per PLC subnet, both Regions.
- The *mirror mistake* what-if takes a **healthy** Region off the air.
- While one Region is down, the **surviving Region is unprotected** (its staging was in the failed Region) - see the replication probe at the end of a Region failure.""")
    b.markdown("""**Option 2 - dedicated us-east-2**
- Two route sets at failover (TGW-C + surviving TGW) - see *Cutover set 2*.
- Mirrors attach to a TGW with defaults OFF: no S1 window; the residual risk is a manual propagation, caught by R2.
- A Region failure loads only us-east-2; drills never run in a production Region.
- The surviving Region **stays protected** during a failure - it still replicates to us-east-2.""")

# ================================================================ about
with tab_about:
    st.markdown("""
#### What this simulates
Two production DR designs for PLC-facing servers whose IPs are hard-coded at the plants:

| | Option 1 | Option 2 |
|---|---|---|
| Where DR lives | In both production Regions | us-east-2, separate DR account |
| Same-IP recovery | Mirror VPC (identical CIDR) per PLC subnet in the other Region | Mirror VPCs in us-east-2 |
| Cutover | One static /24 route set in the surviving TGW | Two route sets: TGW-C + surviving TGW |
| Mirror attachment | Production TGW, defaults ON - S1 window | TGW-C defaults OFF by design (R2 verify) |

#### Vendor and data-centre access
- **Vendor 1**: **policy-based** Site-to-Site VPNs into the Transit Gateway of BOTH production Regions; reaches the PLC-facing servers and the plants'
  PLCs over SD-WAN. Each tunnel carries its own Region's destinations (traffic selectors), so after a Region failure the vendor must move them to the
  other tunnel - unless it pre-configured a **backup peer** (what-if toggle).
- **Vendor 2**: policy-based VPNs into both TGWs; reaches the servers only through private 1:1 DNAT ranges (100.64.10.0/24 -> PLC-A,
  100.64.20.0/24 -> PLC-B) on each Region's Palo Alto, and the plants' PLCs over SD-WAN.
- **Data centre 1**: BGP VPN into both TGWs - follows the routes by itself.
- **Inspection**: all vendor and data-centre traffic is inspected by the Palo Alto of the Region it enters (the TGW VPN table points at the firewall).
  Every round trip is checked: the reply must pass every Palo Alto that saw the request (inspection state, DNAT).
- At failover the PLC /24 goes into the surviving TGW's spoke, SD-WAN and **firewall** tables. Option 1: the surviving Palo Alto holds vendor 2's DNAT
  pre-staged and translates before routing. Option 2: vendors and data centres are inspected in the surviving Region, then cross to us-east-2 (vendor 2's
  NAT range as two **/25s**, DNAT on the us-east-2 Palo Alto); replies are routed back through the same firewalls.
- **SD-WAN**: a pair in each production Region (and in us-east-2 for Option 2); both Regions are active.

#### How the model works
- **Transit Gateway routing**: a packet entering a TGW is looked up in the table associated with its attachment; longest prefix wins; static routes to a peering stay even when the peer Region is down (blackhole).
- **Propagation**: attaching a VPC to a TGW with defaults ON associates and propagates it automatically - the trap behind the mirror what-if.
- **SD-WAN**: plants use the edge that advertises the most specific prefix; ties go to an edge whose route ends in a local VPC, then the home Region.
- **Palo Alto**: plant and vendor traffic entering a Region needs a rule for the destination; DNAT is a firewall attachment in each TGW (translate,
  then route again); the what-if toggle removes the pre-staged PLC rules and DNAT rules.
- **Vendor VPNs**: policy-based tunnels follow their selectors (first live tunnel in order) and drop replies that arrive on the other tunnel; BGP tunnels
  follow the most specific prefix any tunnel advertises.
- **Probes** are one-direction forward paths. Return paths, AD replication, DNS and DRS internals are not modelled.

Addresses (10.10/16, 10.20/16, 10.30/16), server names and durations are illustrative. The building blocks (same-IP recovery, isolated drill,
failover test) were proven in a lab; the three-Region routing is a design to be proven in its first failover test.
""")
