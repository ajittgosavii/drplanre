# DR Architecture Simulator

Interactive, step-by-step simulation of two production disaster-recovery designs for PLC-facing servers whose IP
addresses are hard-coded at the plants (same-IP recovery with AWS Elastic Disaster Recovery):

- **Option 1** - two Regions, each the other's DR (us-east-1 <-> us-west-2)
- **Option 2** - a dedicated DR Region us-east-2 in a separate DR account (us-east-1 + us-west-2 -> us-east-2)

Pure simulation - no AWS credentials, no AWS calls. Addresses, names and timings are illustrative.

## Scenarios
Build (rollout to DR ready) · Region failure (us-east-1 / us-west-2) · Failback · Isolated drill ·
What-if: mirror attachment mistake · Single-server failure. Toggle: firewall PLC rules not pre-staged.

Each step shows the narrative, owner, duration, risk, commands, checks, the route-table changes, a live diagram and
traffic probes (plants, cross-Region, replication, recovered copies, drill isolation) resolved through the model.

## How the model works
- Transit Gateway routing: table associated with the ingress attachment, longest prefix wins, static peering routes
  stay when the peer Region is down (blackhole), default association / propagation applied on attach.
- SD-WAN: plants use the edge advertising the most specific prefix (ties: local VPC origin, then home Region).
- Palo Alto: plant traffic entering a Region needs a rule for the destination.
- Probes are forward paths only; AD replication, DNS and DRS internals are not modelled.

## Run locally
```
pip install -r requirements.txt
streamlit run app.py
python test_scenarios.py      # outcome checks for every scenario
python smoke_app.py           # headless UI smoke test (streamlit AppTest)
```

## Deploy on Streamlit Community Cloud
New app -> this repository -> branch `main` -> main file `app.py`.
