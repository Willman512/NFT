"""Score a prospect: logistic model over features -> GO / MAYBE / SKIP with plain-English reasons."""
import math

from .db import latest_model


CORE_FREIGHT = ("manufacturer", "distributor", "food_producer", "flatbed_oversize", "reefer", "palletized",
                "controls_delivery", "forwarder")


def current_model(conn, icp):
    """Latest learned model, or the starting weights from config/icp.json."""
    row = latest_model(conn)
    prior = {sid: float(s["weight"]) for sid, s in icp["signals"].items()}
    if row is None:
        return {"version": 0, "bias": float(icp["bias"]), "weights": prior,
                "go": icp["thresholds"]["go"], "skip": icp["thresholds"]["skip"]}
    import json
    weights = dict(prior)
    weights.update(json.loads(row["weights"]))
    return {"version": row["version"], "bias": row["bias"], "weights": weights, "go": row["go"], "skip": row["skip"]}


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(min(z, 30), -30)))


def probability(features, model):
    z = model["bias"] + sum(model["weights"].get(f, 0.0) * v for f, v in features.items())
    return sigmoid(z)


def score(features, research, model, icp, policy):
    """Return (verdict, probability, reasons[list of str])."""
    p = probability(features, model)
    signals = icp["signals"]

    def label(fid):
        if fid.startswith("term:"):
            return f"Site mentions '{fid[5:]}' (learned from your wins/losses)"
        return signals.get(fid, {}).get("label", fid)

    contrib = sorted(((model["weights"].get(f, 0.0), f) for f, v in features.items() if v), reverse=True)
    pos = [f"+ {label(f)}" for w, f in contrib if w > 0.25][:4]
    neg = [f"- {label(f)}" for w, f in reversed(contrib) if w < -0.05][:4]

    verdict = "GO" if p >= model["go"] else "SKIP" if p < model["skip"] else "MAYBE"
    flags = []
    # Missing a real shipper costs more than a minute of checking: SKIP only when there's no freight evidence
    # at all, or a hard red flag (service business). Thin evidence with a real freight signal stays MAYBE.
    if verdict == "SKIP" and any(features.get(k) for k in CORE_FREIGHT) and not features.get("service_only"):
        verdict = "MAYBE"
        flags.append("Few signals on the site, but it does show real freight. Take a quick look by hand.")

    # Policy caps: these never get a clean GO no matter how good the score looks.
    if features.get("broker_3pl"):
        flags.append("RED FLAG: looks like a truck broker/3PL (a competitor). Confirm with your manager that TQL allows "
                     "taking freight from them; margins will be thin.")
        if verdict == "GO" and policy.get("broker_3pl_requires_manager_ok", True):
            verdict = "MAYBE"
    if features.get("forwarder") and policy.get("forwarders_require_manager_ok", True):
        flags.append("FORWARDER: books drayage, inland truckload and LTL. Confirm with your manager that TQL allows "
                     "taking forwarder freight before you contact them.")
    if features.get("no_na_presence"):
        flags.append("RED FLAG: no US/Canada/Mexico address or phone found — may be overseas-only (ocean/air only).")
        if verdict == "GO":
            verdict = "MAYBE"
    if features.get("thin_site"):
        flags.append("Couldn't read the site (JavaScript-heavy or blocked). Open it in a browser and judge by hand.")
        verdict = "MAYBE"
    if features.get("contact_info_missing"):
        flags.append("No phone or address on the site: get them from Google Maps before calling.")
    if features.get("stale_site"):
        flags.append(f"Site may be stale (© {research.get('copyright_year')}). Call the main line first.")
        if verdict == "GO":
            verdict = "MAYBE"
    if features.get("french_site"):
        flags.append("French-language site: write in English, greet with 'Bonjour [First Name],'.")
    if policy.get("require_crm_ownership_check", True) and verdict != "SKIP":
        flags.append("Before contact: check TQL's CRM that it isn't an existing customer or another rep's account.")

    return verdict, round(p, 3), pos + neg + flags
