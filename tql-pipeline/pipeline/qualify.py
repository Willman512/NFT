"""Hard requirements every pipeline lead must meet, as far as the evidence can tell:

  1. NO OWN FLEET      — no active FMCSA registration with trucks under their name in their states, and nothing on
                          their site about their own trucks/drivers.
  2. SHIPS INTERSTATE  — plants/locations in 2+ states, Canada, real cross-border, or nationwide/multi-state shipping.
  3. OUTSIDE CARRIERS  — carrier/freight-terms language on their site, or (no fleet + ships interstate + they arrange
                          delivery): the goods cross state lines on someone else's trucks. Customer-pickup-only fails.

Each gate is pass / fail / unknown with the evidence. A lead is queued only if all three pass; otherwise it's held
with the reason (call to verify, or drop). Nothing held is exported to Prospecting Desk.
"""
import re

from . import fmcsa
from .extract import STATE_NAMES, US_STATES

OWN_FLEET = re.compile(
    # "our trailers"/"our trucks" is often the PRODUCT (trailer/truck-body makers), so require delivery context.
    r"\bour (own )?(fleet|drivers|delivery trucks|delivery fleet|truck fleet)\b|\bour own trucks\b|"
    r"\bour trucks (deliver|haul|make|run|are on the road)\b|\bprivate fleet\b|"
    r"\bfleet of (over |more than )?\d+\b|\bcompany[- ](owned )?(trucks|drivers|fleet)\b|\bwe deliver (it )?(ourselves|"
    r"with our own)\b|\b(cdl|class a|truck) drivers? (wanted|needed|positions?|jobs?|openings?)\b|"
    r"\b(hiring|now hiring|join our team as an?) (cdl |class a |local |otr |regional )?(truck )?drivers?\b|"
    r"\bdriver (positions?|openings?|careers?)\b|\bin-house (trucking|transportation|fleet)\b|"
    # route delivery: snack, bread and beverage companies that deliver to stores on their own trucks
    r"\bdirect[- ]store[- ]deliver(y|ies)\b|\bDSD\b|\bcompany[- ]owned (delivery )?routes\b|"
    r"\b\d{2,} (company[- ]owned |delivery |sales )?routes\b|\broute (sales|drivers?|trucks?)\b|"
    r"\b(our|company) (route|delivery) (trucks|drivers|sales reps?)\b|\bdelivery routes?\b|"
    # beer/beverage wholesalers deliver on their own trucks as standard practice
    r"\b(beer|beverage|wine) (distributor|wholesaler|distributing|distribution company)\b", re.I)
CARRIER_LANG = re.compile(
    r"\bLTL\b|\bless[- ]than[- ]truckload\b|\btruckloads?\b|\bfreight (carrier|terms|charges?|quote|policy|class|"
    r"prepaid|collect|allowed)\b|\bcommon carrier\b|\bcarrier of (your|the customer's) choice\b|\bf\.?o\.?b\.?\b|"
    r"\bship(ped|s)? (via|by) (truck|common carrier|freight|ltl)\b|\bthird[- ]party (carriers?|freight|trucking)\b|"
    r"\bfreight (is|will be) (arranged|quoted)\b|\bwe (can )?arrange (freight|shipping|delivery)\b", re.I)
LOCAL_ONLY = re.compile(r"\b(local delivery only|deliver(y|ing)? within \d+ miles|within a \d+[- ]mile radius)\b", re.I)
MULTI_STATE = re.compile(r"\b(ship|deliver|serv|distribut|sell)\w* (to |products )?(customers |dealers )?"
                         r"(in |across |throughout )(all )?(\d+|fifty|several|multiple) states\b|\bnationwide\b|"
                         r"\b(across|throughout) (the )?(us|u\.s\.|united states|country|north america)\b", re.I)


def _states(research):
    sts = set()
    for loc in research.get("us_locations", []):
        _, _, rest = loc.partition(",")
        if rest.split():
            sts.add(rest.split()[0].upper())
    return {s for s in sts if s in US_STATES}


def evaluate(conn, row, text, research, features, meta, policy):
    """-> {"fleet": (status, evidence), "interstate": (...), "carriers": (...), "qualified": bool, "reasons": [...]}"""
    min_units = int(policy.get("fleet_min_power_units", 2))
    states = _states(research) | {p.split(", ")[1][:2] for p in meta.get("plants", []) if ", " in p
                                  and p.split(", ")[1][:2] in US_STATES}
    cities = sorted({loc.split(",")[0].strip() for loc in research.get("us_locations", [])} |
                    {p.split(",")[0].strip() for p in meta.get("plants", [])})

    # 1. fleet
    site_fleet = OWN_FLEET.search(text or "")
    regs = [r for r in fmcsa.match(conn, row["name"] or "", sorted(states), cities) if r["power_units"] >= min_units]
    checked = states and states <= fmcsa.states_checked(conn)
    if regs:
        r = max(regs, key=lambda x: x["power_units"])
        fleet = ("fail", f"FMCSA: USDOT {r['dot']} ({r['classdef'].title()}), {r['power_units']} trucks registered in {r['state']}")
    elif site_fleet:
        fleet = ("fail", f"Site mentions its own trucks/drivers: \"{site_fleet.group(0)}\"")
    elif checked:
        fleet = ("pass", f"No FMCSA fleet registration under this name in {', '.join(sorted(states))}; site shows no fleet")
    else:
        fleet = ("unknown", "FMCSA fleet check not run for " + (", ".join(sorted(states)) or "an unknown state"))

    # 2. interstate
    xborder = research.get("ca_locations") or any(m.lower() not in ("mexico", "méxico") for m in research.get("mx_mentions", []))
    multi = MULTI_STATE.search(text or "")
    named = {s for s in STATE_NAMES if re.search(r"\b" + s + r"\b", text or "")}
    if len(states) >= 2:
        inter = ("pass", f"Locations in {len(states)} states: {', '.join(sorted(states))}")
    elif xborder:
        inter = ("pass", "Locations or customers in Canada/Mexico")
    elif multi:
        inter = ("pass", f"Site: \"{multi.group(0)}\"")
    elif len(named) >= 3 and not LOCAL_ONLY.search(text or ""):
        inter = ("pass", f"Site names {len(named)} states/provinces it works in ({', '.join(sorted(named)[:5])}…)")
    elif LOCAL_ONLY.search(text or ""):
        inter = ("fail", f"Site: \"{LOCAL_ONLY.search(text).group(0)}\"")
    else:
        inter = ("unknown", "No evidence of shipping outside " + (next(iter(states)) if states else "its home state"))

    # 3. outside carriers
    lang = CARRIER_LANG.search(text or "")
    pickup_only = features.get("customer_pickup") and not features.get("controls_delivery") and not lang
    if pickup_only:
        carriers = ("fail", "Customers pick up (customer-routed freight)")
    elif fleet[0] == "fail":
        carriers = ("fail", "Runs its own trucks")
    elif lang:
        carriers = ("pass", f"Site: \"{lang.group(0)}\"")
    elif (features.get("forwarder") or features.get("naics_forwarder")) and fleet[0] == "pass":
        carriers = ("pass", "Freight forwarder with no trucks of its own: it books carriers for every move")
    elif fleet[0] == "pass" and inter[0] == "pass":
        carriers = ("pass", "Inferred: ships across state lines with no trucks of its own, so carriers haul it")
    else:
        carriers = ("unknown", "Can't tell who hauls their freight")

    gates = {"fleet": fleet, "interstate": inter, "carriers": carriers}
    reasons = [f"{name}: {st.upper()} — {ev}" for name, (st, ev) in gates.items()]
    return {**gates, "qualified": all(st == "pass" for st, _ in gates.values()), "reasons": reasons}
