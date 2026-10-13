"""Outcome checks for every scenario: python test_scenarios.py (or pytest)."""
import scenarios as S


def states(name, option, whatif=frozenset()):
    steps, start = S.scenario(name, option, whatif)
    return [S.build(steps, start, i) for i in range(len(steps))]


def status(st, option, pid):
    p = next(x for x in S.probes(option) if x["id"] == pid)
    return S.run_probe(st, p)["status"]


def prod_ok(st, option):
    return all(status(st, option, p) in ("ok", "n/a") for p in ("p1", "p2", "p3", "p4", "p5", "p6", "v1", "v2", "v3", "v4", "v5", "n1", "n2", "dc1", "dc2"))


def test_build_keeps_production_and_replicates():
    for o in (1, 2):
        sts = states("Build - rollout to DR ready", o)
        for st in sts:
            assert prod_ok(st, o), o
        assert status(sts[-1], o, "p5") == "ok" and status(sts[-1], o, "p6") == "ok"


def test_failover_a():
    for o in (1, 2):
        sts = states("Region failure - us-east-1 lost", o)
        assert status(sts[0], o, "p1") == "ok"
        assert status(sts[1], o, "p1") == "fail"
        end = sts[-1]
        for p in ("p1", "p2", "p3", "p7"):
            assert status(end, o, p) == "ok", (o, p, S.run_probe(end, next(x for x in S.probes(o) if x["id"] == p)))
        assert not S.E.split_brain(end)
        assert status(end, o, "p6") == ("fail" if o == 1 else "ok")   # Option 1 leaves us-west-2 unprotected


def test_option2_needs_second_route_set():
    sts = states("Region failure - us-east-1 lost", 2)
    i = next(i for i, s in enumerate(S.failover(2, "A")) if s["title"].startswith("Cutover set 2"))
    st = S.build(S.failover(2, "A"), S.ready(2), i - 1)
    for n in ("SQLA01-DR",):
        S.E.add_host(st, n, "10.10.1.20", "MIR-A", "db", copy_of="SQLA01")
    assert status(st, 2, "p1") == "ok"      # plants via set 1
    assert status(st, 2, "p3") == "fail"    # us-west-2 still on the dead peering


def test_failover_b():
    for o in (1, 2):
        end = states("Region failure - us-west-2 lost", o)[-1]
        for p in ("p1", "p2", "p4", "p8"):
            assert status(end, o, p) == "ok", (o, p)


def test_failback():
    for o in (1, 2):
        end = states("Failback - return to us-east-1", o)[-1]
        assert prod_ok(end, o), o
        r = S.run_probe(end, next(x for x in S.probes(o) if x["id"] == "p1"))
        assert r.get("served_by") == "SQLA01", r


def test_drill_isolated():
    for o in (1, 2):
        sts = states("Isolated drill - us-east-1 servers", o)
        mid = sts[-2]
        for p in ("d1", "d2", "d3"):
            assert status(mid, o, p) == "blocked", (o, p)
        assert status(mid, o, "d4") == "ok"
        assert all(prod_ok(s, o) for s in sts)


def test_hijack():
    s1 = states("What-if - mirror attachment mistake", 1)
    assert status(s1[1], 1, "p1") == "fail" and status(s1[1], 1, "p3") == "fail"
    assert prod_ok(s1[3], 1) and prod_ok(s1[4], 1)
    s2 = states("What-if - mirror attachment mistake", 2)
    assert prod_ok(s2[1], 2) and prod_ok(s2[2], 2)
    assert status(s2[3], 2, "p1") == "fail"
    assert prod_ok(s2[4], 2)


def test_firewall_not_prestaged():
    for o in (1, 2):
        end = states("Region failure - us-east-1 lost", o, frozenset({"no_fw"}))[-1]
        r = S.run_probe(end, next(x for x in S.probes(o) if x["id"] == "p1"))
        assert r["status"] == "fail" and "Palo Alto" in r["reason"], r


def test_vendors_follow_the_cutover():
    for o in (1, 2):
        steps = S.failover(o, "A")
        sts = states("Region failure - us-east-1 lost", o)
        sw = next(i for i, x in enumerate(steps) if x["title"].startswith("Policy-based vendor VPNs"))
        for i in range(1, sw):                                   # policy-based: even PLC access waits for the vendor
            assert status(sts[i], o, "v2") == "fail" and status(sts[i], o, "v3") == "fail", (o, i)
        for st in sts[sw:]:
            for p in ("v2", "v3", "v4", "v5", "n2", "dc2"):
                assert status(st, o, p) == "ok", (o, p)
        end = sts[-1]
        r = S.run_probe(end, next(x for x in S.probes(o) if x["id"] == "n1"))
        assert r["status"] == "ok" and r["dnat"] == ("B" if o == 1 else "C"), r   # DNAT moves to the recovery Region's Palo Alto
        assert status(end, o, "v1") == "ok" and status(end, o, "dc1") == "ok"


def test_vendor_backup_peer_keeps_plc_access():
    for o in (1, 2):
        for st in states("Region failure - us-east-1 lost", o, frozenset({"vendor_backup"})):
            for p in ("v2", "v3", "v4", "v5"):
                assert status(st, o, p) == "ok", (o, p)


def test_option2_vendors_need_set_2():
    steps = S.failover(2, "A")
    i = next(i for i, x in enumerate(steps) if x["title"].startswith("Cutover set 2"))
    st = S.build(steps, S.ready(2), len(steps) - 1)
    for t, rt, c, _ in S.cutover_routes(2, "A")["TGW-B"]:
        S.E.del_route(st, t, rt, c)
    assert status(st, 2, "v1") == "fail" and status(st, 2, "n1") == "fail" and status(st, 2, "p1") == "ok"


def test_mirror_mistake_hits_vendors():
    s1 = states("What-if - mirror attachment mistake", 1)
    assert status(s1[1], 1, "dc1") == "fail"                          # BGP site follows the propagated /24 into the empty mirror
    assert status(s1[1], 1, "v1") == "ok" and status(s1[1], 1, "n1") == "ok"   # policy-based vendors follow selectors, not routes


def test_failback_deletes_every_cutover_route():
    for o in (1, 2):
        end = states("Failback - return to us-east-1", o)[-1]
        assert S.E.route_set(end) == S.E.route_set(S.ready(o)), o
        assert end["vendors"] == S.ready(o)["vendors"], o      # selectors back to normal


def test_single_server():
    for o in (1, 2):
        sts = states("Single-server failure (Region healthy)", o)
        assert status(sts[1], o, "p1") == "fail" and status(sts[-1], o, "p1") == "ok"


if __name__ == "__main__":
    n = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); n += 1; print("ok", k)
    print(n, "tests passed")
