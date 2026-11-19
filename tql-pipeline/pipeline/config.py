"""Load config files and resolve paths."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"


def load_json(name):
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_icp():
    return load_json("icp.json")


def load_policy():
    return load_json("policy.json")


def load_suppression():
    """Return {domain: reason} from config/suppression.csv."""
    import csv
    from .util import normalize_domain

    path = CONFIG_DIR / "suppression.csv"
    out = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = normalize_domain(row.get("domain", ""))
                if d:
                    out[d] = row.get("reason", "") or "suppressed"
    return out
