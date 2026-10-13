"""Topologies, scenario playbooks and traffic probes for both DR options.

Option 1: us-east-1 (A) and us-west-2 (B) recover into each other.
Option 2: A and B recover into us-east-2 (C), a separate DR account.
Every scenario is a list of steps; the state at step i is the start state with steps 1..i applied.
Addresses, names and timings are illustrative.
"""
from functools import lru_cache
import engine as E

REG = {"A": "us-east-1", "B": "us-west-2", "C": "us-east-2"}
PLC = {"A": "10.10.1.0/24", "B": "10.20.1.0/24"}
VPCR = {"A": "10.10.0.0/16", "B": "10.20.0.0/16"}
DR_SUMMARY = "10.30.0.0/16"
SQL = {"A": "SQLA01", "B": "SQLB01"}
APP = {"A": "APPA01", "B": "APPB01"}
IP = {"SQLA01": "10.10.1.20", "APPA01": "10.10.1.10", "SQLB01": "10.20.1.20", "APPB01": "10.20.1.10"}
DC = {"A": ("DCA01", "10.10.50.10"), "B": ("DCB01", "10.20.50.10"), "C": ("DCC01", "10.30.16.10")}
OTHER = {"A": "B", "B": "A"}


def step(title, actor, minutes, text, fn=None, commands=(), checks=(), risk=None, phase=None):
    return {"title": title, "actor": actor, "minutes": minutes, "text": text, "fn": fn or (lambda st: None),
            "commands": list(commands), "checks": list(checks), "risk": risk, "phase": phase}


# ================================================================ production today
def prod(option):
    st = E.new_state(option)
    for k in ("A", "B") + (("C",) if option == 2 else ()):
        E.add_region(st, k, REG[k])
    for k in ("A", "B"):
        o = "10.10" if k == "A" else "10.20"
        E.add_vpc(st, "VPC-" + k, k, [VPCR[k]], "prod", f"Production VPC {k}")
        E.add_host(st, APP[k], f"{o}.1.10", "VPC-" + k, "app")
        E.add_host(st, SQL[k], f"{o}.1.20", "VPC-" + k, "db")
        E.add_host(st, "RPT" + k + "01", f"{o}.5.10", "VPC-" + k, "client")
        E.add_host(st, DC[k][0], DC[k][1], "VPC-" + k, "dc")
        t = "TGW-" + k
        E.add_tgw(st, t, k, default_assoc=True, default_prop=True, assoc_rt="spoke",
                  prop_rts=("spoke", "sdwan", "peering"), rts=("spoke", "sdwan", "peering"))
        E.attach(st, t, "vpc-" + k, vpc="VPC-" + k)
        E.attach(st, t, "sdwan-" + k, kind="sdwan", assoc="sdwan", prop=["spoke", "peering"])
        st["edges"][k] = True
        st["fw"][k] = [VPCR[k]]
    E.peer(st, "TGW-A", "TGW-B", "peer-AB", "peering", "peering")
    for k in ("A", "B"):
        for rt in ("spoke", "sdwan"):
            E.add_route(st, "TGW-" + k, rt, VPCR[OTHER[k]], "peer-AB")
    if option == 2:
        st["edges"]["C"] = False
    return st


# ================================================================ rollout (build) playbooks
def _mirror_name(k):
    return "MIR-" + k


def rollout(option, whatif=frozenset()):
    S = []
    S.append(step("Production today", "-", 0, "Both Regions serve their own plants. Each Region's PLC subnet holds only PLC-facing servers (app + SQL). "
                  "The existing peering A-B carries cross-Region traffic. Nothing is protected yet.", phase="Start"))
    if option == 1:
        S.append(step("Stage 1 - DR Console in both Regions", "Cloud team", 2 * 480,
                      "A private console (EC2) in each Region, plus peer roles. No change to any traffic.",
                      lambda st: st["flags"].update(console=True),
                      ["aws cloudformation deploy --stack-name drs-ui-host ... (each Region)"], ["Console lists both Regions' servers"], phase="Build"))
        def s2(st):
            E.add_host(st, "STG-A", "10.10.200.10", "VPC-A", "staging")
            E.add_host(st, "STG-B", "10.20.200.10", "VPC-B", "staging")
        S.append(step("Stage 2 - DRS initialised + VPC endpoints (both Regions)", "Cloud + Network", 2 * 480,
                      "DRS is initialised in BOTH Regions (each receives the other's servers). Staging subnets and replication servers live inside the production VPCs. "
                      "Interface endpoints with private DNS change name resolution for every workload in those VPCs (S2).", s2,
                      ["aws cloudformation deploy --stack-name drs-recovery-prereqs ... (both Regions)", "aws cloudformation deploy --stack-name drs-agent-source ... (both Regions)"],
                      ["Names resolve to the intended endpoints", "Application smoke tests pass"], risk="MED-HIGH", phase="Build"))
        S.append(step("Stages 3-4 - DRS agents + replication waves", "DR team + server owners", 10 * 480,
                      "Agents on every PLC-facing server. Initial sync crosses the existing peering A-B and both firewalls - throttle first, pilot 2 servers, then waves.",
                      lambda st: st["flags"].update(agents=True),
                      ["drs_agent_select.py --select <wave> --check-only", "aws drs update-replication-configuration --bandwidth-throttling <Mbps>"],
                      ["All servers CONTINUOUS", "Recovery points within 30 min"], risk="MEDIUM", phase="Build"))
        for k in ("A", "B"):
            rk = OTHER[k]
            t, att, mv = "TGW-" + rk, "mir-" + k, _mirror_name(k)
            def off(st, t=t):
                st["tgws"][t]["default_assoc"] = False
                st["tgws"][t]["default_prop"] = False
            def att_fn(st, k=k, rk=rk, t=t, att=att, mv=mv):
                E.add_vpc(st, mv, rk, [PLC[k]], "mirror", f"Mirror of PLC-{k}")
                E.attach(st, t, att, vpc=mv)
            def assoc_fn(st, t=t, att=att):
                E.associate(st, t, att, "spoke")
            def on(st, t=t):
                st["tgws"][t]["default_assoc"] = True
                st["tgws"][t]["default_prop"] = True
            S.append(step(f"Stage 5 - S1: {t} defaults OFF for the window", "Network team", 30,
                          f"{t} is a production TGW with default association and propagation ON. Attaching a mirror now would propagate {PLC[k]} - more specific than "
                          f"the live route - into every table. So S1 switches the defaults off first; existing attachments are untouched.", off,
                          [f"aws ec2 modify-transit-gateway --transit-gateway-id <{t}> --options DefaultRouteTablePropagation=disable,DefaultRouteTableAssociation=disable"],
                          ["Record the defaults and the propagation table's associations", "Baseline test plant -> PLC server OK", "Freeze other TGW changes"], risk="HIGH", phase="Build"))
            S.append(step(f"Stage 5 - Attach the mirror of PLC-{k} to {t}", "App team + Network team", 20,
                          f"Mirror VPC {PLC[k]} (identical CIDR, empty) is created in {REG[rk]} and attached. With the defaults off, the attachment is neither associated nor propagated.",
                          att_fn, [f"aws cloudformation deploy --stack-name drs-mirror-{REG[k]}-plc --template-file drs-mirror-subnet.yaml ..."],
                          ["get-transit-gateway-attachment-propagations -> EMPTY"], risk="HIGH", phase="Build"))
            S.append(step(f"Stage 5 - Associate the mirror (no propagation), verify, defaults back", "Network team", 30,
                          f"Associate with the spoke table so recovered servers can reach out. Verify: exact-match {PLC[k]} is EMPTY in every {t} table and the baseline test still passes. Then restore the defaults.",
                          lambda st, a=assoc_fn, o=on: (a(st), o(st)),
                          [f"aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id <spoke> --transit-gateway-attachment-id <{att}>",
                           f"aws ec2 search-transit-gateway-routes --filters Name=route-search.exact-match,Values={PLC[k]}   # EMPTY",
                           f"aws ec2 modify-transit-gateway --transit-gateway-id <{t}> --options DefaultRouteTablePropagation=enable,DefaultRouteTableAssociation=enable"],
                          ["DR readiness: Same-IP recovery OK"], risk="HIGH", phase="Build"))
        def s6(st):
            for k in ("A", "B"):
                E.add_vpc(st, "BUB-" + k, k, ["10.39.0.0/24"], "bubble", f"Drill bubble {REG[k]}")
            st["flags"].update(prepared=True)
        S.append(step("Stage 6 - Drill bubbles, tags, DR preparation", "DR team", 2 * 480,
                      "One isolated drill VPC per Region (never attached). DrTier / DrWave tags; DR preparation writes launch settings: mirror subnet + each server's OWN IP.", s6,
                      ["aws cloudformation deploy --stack-name drs-drill-network ... (both Regions)", "DR Console -> DR preparation -> Apply"],
                      ["DR readiness READY in both directions"], phase="Build"))
        def s8(st):
            if "no_fw" not in whatif:
                st["fw"]["B"].append(PLC["A"])
                st["fw"]["A"].append(PLC["B"])
            st["flags"].update(cutover_approved=True)
        S.append(step("Stage 8 - Firewall / SD-WAN pre-staging + pre-approved cutover", "Interface + Network + CAB", 10 * 480,
                      "Palo Alto in each Region gets rules for the OTHER Region's PLC flows (same addresses, carry no traffic yet). SD-WAN route learning confirmed. "
                      "The cutover / rollback route commands are registered as a pre-approved standard change." +
                      (" WHAT-IF: the firewall rules were NOT pre-staged." if "no_fw" in whatif else ""), s8,
                      ["dr_engine.py cutover-plan --direction us-east-1->us-west-2", "dr_engine.py cutover-plan --direction us-west-2->us-east-1"],
                      ["Rules + route learning confirmed in writing", "Standard change approved"], phase="Build"))
    else:
        def s1(st):
            E.add_vpc(st, "DR-C", "C", ["10.30.0.0/20"], "dr", "DR VPC (DR-C)")
            st["flags"].update(dr_account=True)
        S.append(step("Stage 1 - DR account + us-east-2 foundation", "Platform team", 10 * 480,
                      "New DR account (Account Factory), SCP allows us-east-2, logging + security services, DR VPC 10.30.0.0/20 with staging, console and endpoint subnets. "
                      "Nothing touches production.", s1, ["Account Factory: new account 'DR'", "aws cloudformation deploy --stack-name dr-vpc ..."],
                      ["Account governed, DR VPC created"], phase="Build"))
        def s2(st):
            E.add_tgw(st, "TGW-C", "C", default_assoc=False, default_prop=False, assoc_rt="dr", prop_rts=(), rts=("dr", "peering", "sdwan"))
            E.attach(st, "TGW-C", "vpc-DR", vpc="DR-C", assoc="dr", prop=["dr", "peering", "sdwan"])
            E.peer(st, "TGW-A", "TGW-C", "peer-AC", "peering", "peering")
            E.peer(st, "TGW-B", "TGW-C", "peer-BC", "peering", "peering")
            E.add_route(st, "TGW-C", "dr", VPCR["A"], "peer-AC")
            E.add_route(st, "TGW-C", "dr", VPCR["B"], "peer-BC")
            for k in ("A", "B"):
                for rt in ("spoke", "sdwan"):
                    E.add_route(st, "TGW-" + k, rt, DR_SUMMARY, "peer-" + k + "C")
        S.append(step("Stage 2 - TGW us-east-2 (defaults OFF) + peerings A-C, B-C + DR-C routes (R1)", "Network team", 12 * 480,
                      "TGW-C is created with default association and propagation DISABLED. New peerings A-C and B-C. The ONE production-side network change: "
                      f"a {DR_SUMMARY} route in TGW A and TGW B (R1 - one route table at a time, supernet/subnet searches empty first).", s2,
                      ["aws ec2 create-transit-gateway --options DefaultRouteTableAssociation=disable,DefaultRouteTablePropagation=disable",
                       "aws ec2 create-transit-gateway-peering-attachment ... (A-C, B-C)",
                       f"aws ec2 create-transit-gateway-route --destination-cidr-block {DR_SUMMARY} --transit-gateway-attachment-id <peer-AC|peer-BC>"],
                      ["supernet-of / subnet-of searches for DR-C were EMPTY before", "Baseline A<->B and plant tests still pass"], risk="MEDIUM", phase="Build"))
        def s3(st):
            E.attach(st, "TGW-C", "sdwan-C", kind="sdwan", assoc="sdwan", prop=["dr", "peering"])
            st["edges"]["C"] = True
            st["fw"]["C"] = [DR_SUMMARY] + ([] if "no_fw" in whatif else [PLC["A"], PLC["B"]])
        S.append(step("Stage 3 - Palo Alto + SD-WAN edge in us-east-2", "Interface team", 18 * 480,
                      "Inspection VPC (appliance mode) and an SD-WAN edge in us-east-2. Rules for replication AND - pre-staged - plants <-> PLC-A and plants <-> PLC-B. "
                      "They carry no traffic until a cutover." + (" WHAT-IF: the PLC rules were NOT pre-staged." if "no_fw" in whatif else ""), s3,
                      ["Panorama: pre-stage rules plants <-> 10.10.1.0/24, 10.20.1.0/24 (both directions)"], ["SD-WAN learns TGW-C routes by BGP - confirmed in writing"], phase="Build"))
        def s4(st):
            E.add_vpc(st, "SS-C", "C", ["10.30.16.0/24"], "ad", "Shared Services (AD) us-east-2")
            E.attach(st, "TGW-C", "vpc-SS", vpc="SS-C", assoc="dr", prop=["dr", "peering", "sdwan"])
            E.add_host(st, DC["C"][0], DC["C"][1], "SS-C", "dc")
        S.append(step("Stage 4 - AWS Managed AD: add Region us-east-2", "AD team", 5 * 480,
                      "Two domain controllers in us-east-2 (replicas of the existing directory). Recovered servers will log on locally.", s4,
                      ["aws ds add-region --directory-id <d-...> --region-name us-east-2 --vpc-settings ..."], ["Two DCs active in us-east-2, AD site defined"], phase="Build"))
        def s6(st):
            E.add_host(st, "STG-C", "10.30.1.10", "DR-C", "staging")
            st["flags"].update(console=True)
        S.append(step("Stages 5-6 - DR Console + DRS foundation", "Cloud team", 12 * 480,
                      "One console in the DR account (survives the loss of A or B). DRS initialised in us-east-2; staging subnet + replication servers in DR-C. "
                      "Source-side install documents + endpoints in A and B (S2 - source side only).", s6,
                      ["aws cloudformation deploy --stack-name drs-recovery-prereqs (DR account, us-east-2)", "aws cloudformation deploy --stack-name drs-agent-source (App account, us-east-1 + us-west-2)"],
                      ["Network check: no open requests"], risk="MED-HIGH", phase="Build"))
        S.append(step("Stage 7 - DRS agents + replication (us-east-1, then us-west-2)", "DR team + server owners", 18 * 480,
                      "Every PLC-facing server replicates over the NEW peerings A-C / B-C and firewall C - the A-B peering is not loaded.",
                      lambda st: st["flags"].update(agents=True), ["drs_agent_select.py --select <wave> --check-only"], ["All servers CONTINUOUS"], risk="MEDIUM", phase="Build"))
        def s8a(st):
            for k in ("A", "B"):
                E.add_vpc(st, _mirror_name(k), "C", [PLC[k]], "mirror", f"Mirror of PLC-{k}")
                E.attach(st, "TGW-C", "mir-" + k, vpc=_mirror_name(k))
        S.append(step("Stage 8 - Attach both mirrors to TGW-C", "App team + Network team", 60,
                      "Mirror VPCs (identical CIDRs, empty) in the DR account. TGW-C's defaults are OFF by construction - attaching adds no association and no route anywhere. "
                      "No S1 'defaults off' window needed.", s8a, ["aws cloudformation deploy --stack-name drs-mirror-... (x2, DR account)"],
                      ["get-transit-gateway-attachment-propagations -> EMPTY"], risk="LOW", phase="Build"))
        def s8b(st):
            for k in ("A", "B"):
                E.associate(st, "TGW-C", "mir-" + k, "dr")
        S.append(step("Stage 8 - Associate mirrors with the DR table (R2), verify", "Network team", 30,
                      "Associated with the DR table only, never propagated. Verify exact-match /24 EMPTY in every TGW-C table.", s8b,
                      ["aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id <dr> --transit-gateway-attachment-id <mir-A|mir-B>"],
                      ["exact-match 10.10.1.0/24 and 10.20.1.0/24 EMPTY in dr, peering, sdwan"], risk="LOW", phase="Build"))
        def s9(st):
            E.add_vpc(st, "BUB-C", "C", ["10.39.0.0/24"], "bubble", "Drill bubble us-east-2")
            st["flags"].update(prepared=True, cutover_approved=True)
        S.append(step("Stages 9-11 - Drill bubble, DR preparation, pre-approved cutover", "DR team + Network + CAB", 8 * 480,
                      "One drill bubble serves both directions. Launch settings: mirror + own IP. The cutover is TWO route sets per direction (TGW-C + the surviving production TGW), "
                      "registered as one pre-approved change.", s9, ["drs-drill-network.yaml (DR account)", "DR Console -> DR preparation -> Apply"],
                      ["DR readiness READY for us-east-1 -> us-east-2 and us-west-2 -> us-east-2"], phase="Build"))
    S.append(step("DR ready", "-", 0, "Both directions READY. Production traffic is exactly as it was on day one - verify with the probes.", phase="Ready"))
    return S


def build(steps, start, upto):
    st = E.clone(start)
    for s in steps[1:upto + 1]:
        s["fn"](st)
    return st


@lru_cache(maxsize=None)
def _ready_cached(option, whatif):
    return build(rollout(option, whatif), prod(option), len(rollout(option, whatif)) - 1)


def ready(option, whatif=frozenset()):
    return E.clone(_ready_cached(option, frozenset(whatif)))


# ================================================================ failover / failback
def failover(option, k):
    """Real Region failure of k (A or B)."""
    sv = OTHER[k]
    S = [step("Normal operation - DR ready", "-", 0, "Both Regions in production, replication CONTINUOUS.", phase="Start")]
    S.append(step(f"{REG[k]} outage", "-", 0, f"Region {REG[k]} is lost: its TGW, VPCs, servers and SD-WAN edge disappear. Plants of {REG[k]} lose their servers. "
                  "Note: static routes in the surviving TGWs still point at the dead peering.", lambda st: E.region_down(st, k), phase="Incident"))
    S.append(step("Declare DR", "Incident manager", 15, "DR declared for " + REG[k] + "; plants informed. The pre-approved change is opened.", phase="Decide"))
    console = f"console in {REG[sv]}" if option == 1 else "console in the DR account (us-east-2)"
    S.append(step("DR readiness check", "DR team", 5, f"From the {console}: direction {REG[k]} -> {REG[sv] if option == 1 else REG['C']}. "
                  f"{REG[k]} unreachable is expected - facts come from the DRS copies (launch settings, recovery points).",
                  checks=["Recovery points within 30 min", "Same-IP mirror OK", "Capacity OK"], phase="Decide"))
    S.append(step("Fence the primaries", "DR team", 5, f"Any {REG[k]} primary still reachable is stopped; none may start while its copy runs (same IP, same AD computer account).",
                  lambda st: st["flags"].update(fenced=k), checks=["No primary running in PLC-" + k], phase="Fence"))
    if option == 1:
        def cut(st):
            for rt in ("spoke", "sdwan"):
                E.add_route(st, "TGW-" + sv, rt, PLC[k], "mir-" + k)
        S.append(step(f"Cutover - one route set in TGW-{sv}", "Network team (pre-approved)", 10,
                      f"Static {PLC[k]} -> mirror attachment in the surviving TGW's spoke and SD-WAN tables. The /24 beats the /16 to the dead peering; nothing is deleted. "
                      f"SD-WAN learns the /24 by BGP and plants of {REG[k]} now route to {REG[sv]} (the mirror is still empty).", cut,
                      [f"aws ec2 create-transit-gateway-route --region {REG[sv]} --transit-gateway-route-table-id <spoke|sdwan> \\",
                       f"    --destination-cidr-block {PLC[k]} --transit-gateway-attachment-id <mir-{k}>"],
                      ["Routes visible in both tables", f"SD-WAN learned {PLC[k]} via {REG[sv]}"], risk="HIGH*", phase="Cutover"))
    else:
        def cut1(st):
            for rt in ("dr", "peering", "sdwan"):
                E.add_route(st, "TGW-C", rt, PLC[k], "mir-" + k)
        def cut2(st):
            for rt in ("spoke", "sdwan"):
                E.add_route(st, "TGW-" + sv, rt, PLC[k], "peer-" + sv + "C")
        S.append(step("Cutover set 1 - TGW us-east-2", "Network team (pre-approved)", 10,
                      f"Static {PLC[k]} -> mirror attachment in TGW-C's dr, peering and sdwan tables. SD-WAN edge C advertises the /24 - plants of {REG[k]} route to us-east-2. "
                      f"But {REG[sv]} workloads still follow the /16 to the dead peering.", cut1,
                      [f"aws ec2 create-transit-gateway-route --region us-east-2 --transit-gateway-route-table-id <dr|peering|sdwan> \\",
                       f"    --destination-cidr-block {PLC[k]} --transit-gateway-attachment-id <mir-{k}>"], ["Routes visible in all three TGW-C tables"], risk="HIGH*", phase="Cutover"))
        S.append(step(f"Cutover set 2 - surviving TGW-{sv}", "Network team (pre-approved)", 10,
                      f"Static {PLC[k]} -> peering {sv}-C in TGW-{sv}'s spoke and SD-WAN tables, so {REG[sv]} workloads reach the recovered servers via us-east-2. "
                      "This second set is what Option 1 does not need - and what the tooling does not print yet (runbook).", cut2,
                      [f"aws ec2 create-transit-gateway-route --region {REG[sv]} --transit-gateway-route-table-id <spoke|sdwan> \\",
                       f"    --destination-cidr-block {PLC[k]} --transit-gateway-attachment-id <peer-{sv}C>"], ["Routes visible", f"{REG[sv]} reporting probe OK"], risk="HIGH*", phase="Cutover"))
    mv = _mirror_name(k)
    def rec_db(st):
        E.add_host(st, SQL[k] + "-DR", IP[SQL[k]], mv, "db", copy_of=SQL[k])
    def rec_app(st):
        E.add_host(st, APP[k] + "-DR", IP[APP[k]], mv, "app", copy_of=APP[k])
    S.append(step("Recover phase 1 - database", "DR Console", 20, f"DRS launches {SQL[k]} into the mirror with its OWN IP {IP[SQL[k]]}. The console checks SQL through SSM before the next phase.", rec_db,
                  [f"aws drs start-recovery --region {REG[sv] if option == 1 else REG['C']} --source-servers sourceServerID=<s-{SQL[k]}>"], ["MSSQLSERVER running, databases online"], phase="Recover"))
    S.append(step("Recover phase 2 - application", "DR Console", 12, f"{APP[k]} launches into the mirror with {IP[APP[k]]}; joins AD with its existing computer account (secure channel check).", rec_app,
                  checks=["App service running", "App writes to the recovered SQL"], phase="Recover"))
    S.append(step("Verify - pilot plant, then all plants", "DR team + plants", 10, "Same IPs, same names: PLCs at the plants reach their servers with no configuration change. RPO = last replicated write." +
                  (f" NOTE: {REG[sv]} is now UNPROTECTED - its DR staging lived in the failed Region, so its replication probe fails until failback." if option == 1 else
                   f" {REG[sv]} stays protected: it still replicates to us-east-2."),
                  checks=["Pilot plant PLC/HMI OK", "All plants OK", "RPO / RTO recorded"], phase="Verify"))
    return S


def failback(option, k):
    sv = OTHER[k]
    S = [step(f"Running in DR since the {REG[k]} failure", "-", 0, "Copies serve the plants from the " + ("other production Region." if option == 1 else "DR Region."), phase="Start")]
    def up(st):
        E.region_up(st, k, start_hosts=False)
        for h in st["hosts"]:
            if st["vpcs"][h["vpc"]]["region"] == k and h["role"] not in ("db", "app"):
                h["state"] = "running"
    S.append(step(f"{REG[k]} is back - primaries kept STOPPED", "Cloud team", 0, "The Region returns. The original PLC servers must stay stopped: the copies hold their IPs and AD accounts.", up,
                  checks=[f"{SQL[k]}, {APP[k]} stopped"], phase="Prepare"))
    S.append(step("Reverse replication to the original Region", "DR team", 240, "DRS replicates the recovery instances back to the original Region until CONTINUOUS (cross-account path still to be proven in the first test).",
                  lambda st: st["flags"].update(reverse=True), ["aws drs reverse-replication --recovery-instance-id <i-...>"], ["Reverse replication CONTINUOUS"], risk="UNPROVEN", phase="Prepare"))
    def stop_copies(st):
        for h in st["hosts"]:
            if h["copy_of"] and h["state"] == "running":
                h["state"] = "stopped"
    S.append(step("Change window - stop the copies", "DR team", 10, "Short planned outage starts: copies stopped so nothing runs twice.", stop_copies, phase="Window"))
    def del_routes(st):
        if option == 1:
            for rt in ("spoke", "sdwan"):
                E.del_route(st, "TGW-" + sv, rt, PLC[k])
        else:
            for rt in ("dr", "peering", "sdwan"):
                E.del_route(st, "TGW-C", rt, PLC[k])
            for rt in ("spoke", "sdwan"):
                E.del_route(st, "TGW-" + sv, rt, PLC[k])
    S.append(step("Delete the failover route" + ("" if option == 1 else " sets (both TGWs)"), "Network team (pre-approved)", 10,
                  "The /24s are deleted; traffic falls back to the original Region's routes. SD-WAN withdraws the /24.", del_routes,
                  [f"aws ec2 delete-transit-gateway-route ... --destination-cidr-block {PLC[k]}" + ("" if option == 1 else "   # TGW-C x3 and TGW-" + sv + " x2")], phase="Window"))
    def start_p(st):
        E.host(st, SQL[k])["state"] = "running"
        E.host(st, APP[k])["state"] = "running"
    S.append(step("Start the primaries (db -> app)", "DR team", 15, "Original servers start with their original IPs, now holding the data replicated back.", start_p,
                  checks=["SQL online", "App OK", "Pilot plant OK"], phase="Window"))
    def reprotect(st):
        remove = [h["name"] for h in st["hosts"] if h["copy_of"]]
        for n in remove:
            E.remove_host(st, n)
        st["flags"].pop("fenced", None)
        st["flags"].pop("reverse", None)
    S.append(step("Re-protect and clean up", "DR team", 60, "Copies terminated, original direction re-protected; readiness READY.", reprotect, checks=["DR readiness READY"], phase="Done"))
    return S


def failback_start(option, k):
    st = ready(option)
    f = failover(option, k)
    return build(f, st, len(f) - 1)


# ================================================================ drill, what-ifs, single server
def drill(option, k="A"):
    bub = ("BUB-" + OTHER[k]) if option == 1 else "BUB-C"
    S = [step("Normal operation - DR ready", "-", 0, f"Isolated drill of {REG[k]}'s PLC servers while production keeps running.", phase="Start")]
    S.append(step("Pre-check: is the bubble really isolated?", "DR Console", 5,
                  "Routes, TGW attachments, peering, IGW/NAT, DHCP and NACL are simulated against every DC and source IP. The drill is refused if any path exists.",
                  checks=["No TGW attachment, no IGW/NAT", "Amazon DNS (not the DCs)", "NACL only 443 to endpoints"], phase="Pre-check"))
    S.append(step("Create per-drill SSM endpoints", "DR Console", 5, "ssm / ssmmessages / ec2messages endpoints in the bubble so copies can be checked without any route out.",
                  lambda st: st["flags"].update(drill_endpoints=True), phase="Drill"))
    S.append(step("Launch the database drill copy", "DR Console", 20, "StartRecovery(isDrill) into the bubble via a TEMPORARY launch-template version - the saved DR settings stay untouched.",
                  lambda st: E.add_host(st, SQL[k] + "-DRILL", "10.39.0.20", bub, "drill", copy_of=SQL[k]),
                  [f"aws drs start-recovery --is-drill --source-servers sourceServerID=<s-{SQL[k]}>"], phase="Drill"))
    S.append(step("Launch the application drill copy", "DR Console", 12, "App copy in the bubble, pointed at the drill database by a hosts-file pin.",
                  lambda st: E.add_host(st, APP[k] + "-DRILL", "10.39.0.10", bub, "drill", copy_of=APP[k]), phase="Drill"))
    S.append(step("Prove isolation from inside the copies", "DR Console", 10,
                  "Through SSM: TCP to every DC, to production servers and to the internet MUST fail; services + SQL + app-to-drill-db must work. See the drill probes.",
                  checks=["DCs 53/88/135/389/445 blocked", "Production 1433/445/3389 blocked", "1.1.1.1:443 blocked", "SQL + app healthy"], phase="Prove"))
    def end(st):
        for n in (SQL[k] + "-DRILL", APP[k] + "-DRILL"):
            E.remove_host(st, n)
        st["flags"].pop("drill_endpoints", None)
    S.append(step("Terminate copies, delete endpoints, file the report", "DR Console", 10, "Nothing left behind; the report (json + md) goes into the change record.", end, phase="Done"))
    return S


def hijack_start(option):
    st = ready(option)
    t = "TGW-B" if option == 1 else "TGW-C"
    del st["tgws"][t]["atts"]["mir-A"]
    for r in st["tgws"][t]["rts"].values():
        r["assoc"][:] = [a for a in r["assoc"] if a != "mir-A"]
        r["routes"][:] = [x for x in r["routes"] if x["target"] != "mir-A"]
    st["vpcs"]["MIR-A"]["att"] = None
    return st


def hijack(option):
    S = [step("Mirror of PLC-A built, not attached yet", "-", 0, "Production healthy. The next change attaches the mirror (identical CIDR 10.10.1.0/24) to a Transit Gateway.", phase="Start")]
    if option == 1:
        S.append(step("Mirror attached to TGW-B with defaults ON (S1 skipped)", "Network team - mistake", 10,
                      "TGW-B is a production TGW with default propagation ON. The attachment propagates 10.10.1.0/24 into spoke, sdwan and peering tables. "
                      "That /24 is MORE specific than the live routes, and SD-WAN edge B advertises it to every plant.",
                      lambda st: E.attach(st, "TGW-B", "mir-A", vpc="MIR-A"), risk="HIGH", phase="Mistake"))
        S.append(step("Impact - healthy production is unreachable", "-", 0,
                      "Nothing failed, yet plants of us-east-1 and the us-west-2 reporting servers now send PLC traffic into an empty VPC. "
                      "This is the permanent HIGH risk of Option 1: it repeats for every PLC subnet in both Regions.", phase="Impact"))
        S.append(step("Emergency rollback - disable the propagation", "Network team", 5, "Seconds to fix once noticed: disable propagation (or delete the mirror stack).",
                      lambda st: E.unpropagate(st, "TGW-B", "mir-A"),
                      ["aws ec2 disable-transit-gateway-route-table-propagation --transit-gateway-route-table-id <rt> --transit-gateway-attachment-id <mir-A>"], phase="Rollback"))
        S.append(step("The S1 way - associate only, verify", "Network team", 20, "Associated with the spoke table, no propagation. Exact-match /24 EMPTY everywhere. Probes back to green.",
                      lambda st: E.associate(st, "TGW-B", "mir-A", "spoke"), phase="Correct"))
    else:
        S.append(step("Mirror attached to TGW-C - defaults OFF by design", "Network team", 10,
                      "TGW-C was created with default association and propagation disabled: the attachment adds no association and no route. Production unaffected.",
                      lambda st: E.attach(st, "TGW-C", "mir-A", vpc="MIR-A"), risk="LOW", phase="Attach"))
        S.append(step("Associate with the DR table only (R2)", "Network team", 10, "Explicit association, no propagation. Probes stay green.",
                      lambda st: E.associate(st, "TGW-C", "mir-A", "dr"), phase="Attach"))
        S.append(step("Residual risk - someone propagates the mirror into TGW-C's SD-WAN table", "Network team - mistake", 5,
                      "Defaults off removes the ACCIDENT, not every mistake. A manual propagation into the SD-WAN table makes edge C advertise 10.10.1.0/24 - "
                      "plants follow it into the empty mirror. This is why R2 verifies exact-match /24 in every TGW-C table.",
                      lambda st: E.propagate(st, "TGW-C", "mir-A", "sdwan"), risk="HIGH", phase="Mistake"))
        S.append(step("R2 verification catches it - disable the propagation", "Network team", 5, "Exact-match search is not empty -> propagation disabled. Probes back to green.",
                      lambda st: E.unpropagate(st, "TGW-C", "mir-A"), ["aws ec2 disable-transit-gateway-route-table-propagation ..."], phase="Rollback"))
    return S


def single_server(option):
    S = [step("Normal operation - DR ready", "-", 0, "One server fails while the Region is healthy.", phase="Start")]
    S.append(step("SQLA01 fails (Region healthy)", "-", 0, "Hardware / OS failure of one database server in us-east-1.", lambda st: E.host(st, "SQLA01").update(state="down"), phase="Incident"))
    S.append(step("Do NOT fail one server over cross-Region", "DR team", 5,
                  "Moving one IP to another Region would need a /32-style route and break the app server next to it in the same VPC, which keeps using the local subnet route.",
                  phase="Decide"))
    S.append(step("Recover IN PLACE - same subnet, same IP, same Region", "DR team", 30,
                  "EC2 auto-recovery, an AWS Backup restore, or a DRS point-in-time snapshot launched into the ORIGINAL subnet with the same IP.",
                  lambda st: E.host(st, "SQLA01").update(state="running"), ["aws drs start-recovery ... (launch settings: original subnet, original IP)"],
                  ["SQL online", "Plants OK"], phase="Recover"))
    return S


# ================================================================ catalogue
SCENARIOS = {
    "Build - rollout to DR ready": (lambda o, w: rollout(o, w), lambda o, w: prod(o)),
    "Region failure - us-east-1 lost": (lambda o, w: failover(o, "A"), lambda o, w: ready(o, w)),
    "Region failure - us-west-2 lost": (lambda o, w: failover(o, "B"), lambda o, w: ready(o, w)),
    "Failback - return to us-east-1": (lambda o, w: failback(o, "A"), lambda o, w: failback_start(o, "A")),
    "Isolated drill - us-east-1 servers": (lambda o, w: drill(o, "A"), lambda o, w: ready(o, w)),
    "What-if - mirror attachment mistake": (lambda o, w: hijack(o), lambda o, w: hijack_start(o)),
    "Single-server failure (Region healthy)": (lambda o, w: single_server(o), lambda o, w: ready(o, w)),
}


def scenario(name, option, whatif=frozenset()):
    steps_fn, start_fn = SCENARIOS[name]
    return steps_fn(option, frozenset(whatif)), start_fn(option, frozenset(whatif))


# ================================================================ probes
def probes(option):
    stg = {"A": "10.20.200.10" if option == 1 else "10.30.1.10", "B": "10.10.200.10" if option == 1 else "10.30.1.10"}
    bub = "BUB-B" if option == 1 else "BUB-C"
    dc_for_a = DC["B"] if option == 1 else DC["C"]
    dc_for_b = DC["A"] if option == 1 else DC["C"]
    return [
        {"id": "p1", "group": "Plants", "label": "Plants east -> SQLA01 (PLC traffic)", "src": ("plant", "A"), "ip": IP["SQLA01"], "expect": "deliver"},
        {"id": "p2", "group": "Plants", "label": "Plants west -> SQLB01 (PLC traffic)", "src": ("plant", "B"), "ip": IP["SQLB01"], "expect": "deliver"},
        {"id": "p3", "group": "Cross-Region", "label": "RPTB01 (us-west-2) -> SQLA01", "src": ("vpc", "VPC-B"), "ip": IP["SQLA01"], "expect": "deliver"},
        {"id": "p4", "group": "Cross-Region", "label": "RPTA01 (us-east-1) -> SQLB01", "src": ("vpc", "VPC-A"), "ip": IP["SQLB01"], "expect": "deliver"},
        {"id": "p5", "group": "Replication", "label": "Replication us-east-1 -> staging", "src": ("vpc", "VPC-A"), "ip": stg["A"], "expect": "deliver", "needs_flag": "agents"},
        {"id": "p6", "group": "Replication", "label": "Replication us-west-2 -> staging", "src": ("vpc", "VPC-B"), "ip": stg["B"], "expect": "deliver", "needs_flag": "agents"},
        {"id": "p7", "group": "Recovered", "label": f"Recovered PLC-A copy -> {dc_for_a[0]} (logon)", "src": ("vpc", "MIR-A"), "ip": dc_for_a[1], "expect": "deliver", "needs_copy": "MIR-A"},
        {"id": "p8", "group": "Recovered", "label": f"Recovered PLC-B copy -> {dc_for_b[0]} (logon)", "src": ("vpc", "MIR-B"), "ip": dc_for_b[1], "expect": "deliver", "needs_copy": "MIR-B"},
        {"id": "d1", "group": "Drill isolation", "label": f"Drill copy -> {dc_for_a[0]} (must be blocked)", "src": ("vpc", bub), "ip": dc_for_a[1], "expect": "block", "needs_copy": bub},
        {"id": "d2", "group": "Drill isolation", "label": "Drill copy -> production SQLA01 (must be blocked)", "src": ("vpc", bub), "ip": IP["SQLA01"], "expect": "block", "needs_copy": bub},
        {"id": "d3", "group": "Drill isolation", "label": "Drill copy -> internet 1.1.1.1 (must be blocked)", "src": ("vpc", bub), "ip": "1.1.1.1", "expect": "block", "needs_copy": bub},
        {"id": "d4", "group": "Drill isolation", "label": "Drill app -> drill database (must work)", "src": ("vpc", bub), "ip": "10.39.0.20", "expect": "deliver", "needs_copy": bub},
    ]


def run_probe(st, p):
    if p.get("needs_flag") and not st["flags"].get(p["needs_flag"]):
        return {"status": "n/a", "reason": "not protected yet", "hops": [], "path": []}
    if p.get("needs_copy"):
        v = p["needs_copy"]
        if v not in st["vpcs"] or not any(h["vpc"] == v and h["copy_of"] and h["state"] == "running" for h in st["hosts"]):
            return {"status": "n/a", "reason": "no copy running there", "hops": [], "path": []}
    if p["src"][0] == "vpc" and p["src"][1] not in st["vpcs"]:
        return {"status": "n/a", "reason": "source not built", "hops": [], "path": []}
    if p["src"][0] == "vpc" and not st["regions"][st["vpcs"][p["src"][1]]["region"]]["up"]:
        return {"status": "n/a", "reason": "source Region is down", "hops": [], "path": []}
    r = E.send(st, p["src"], p["ip"])
    if p["expect"] == "deliver":
        r["status"] = "ok" if r["ok"] else "fail"
    else:
        r["status"] = "blocked" if not r["ok"] else "breach"
    return r
