#!/usr/bin/env python3
"""Validate data/career_paths.json after any edit.

Usage:  python3 scripts/validate_career_data.py
Exits non-zero with a list of problems, or prints per-family counts on success.
"""
import json
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "career_paths.json"

FIELDS = {
    "id", "name", "category", "income_range_cad", "time_to_income", "time_to_income_years",
    "training_required", "training_cost_level", "job_market_signal", "risk_level",
    "business_potential", "freelance_potential", "sales_requirement", "ai_exposure_level",
    "ai_disruption_risk", "ai_leverage_potential", "human_moat", "three_to_five_year_ai_note",
    "defensive_skills", "ai_tools_to_learn", "best_suited_for", "poor_fit_for",
    "first_90_days", "year_one_plan", "two_year_plan", "notes",
    "career_family", "pathway_type", "income_predictability", "warnings",
}
ENUMS = {
    "training_cost_level": {"Low", "Medium", "High"},
    "job_market_signal": {"Hot", "Warm", "Balanced", "Cool", "Cold"},
    "risk_level": {"Low", "Medium", "High"},
    "sales_requirement": {"Low", "Medium", "High"},
    "business_potential": {"Low", "Medium", "High", "Very High"},
    "freelance_potential": {"Low", "Medium", "High", "Very High"},
    "ai_exposure_level": {"Low", "Medium", "High", "Very High"},
    "ai_disruption_risk": {"Low", "Medium", "High"},
    "ai_leverage_potential": {"Low", "Medium", "High", "Very High"},
    "career_family": {"construction", "health", "energy", "finance", "sales", "creative", "trades",
                      "legal", "public_sector", "logistics", "transport", "entrepreneurship",
                      "education", "hospitality", "science", "agriculture", "manufacturing", "media",
                      "security", "service_business", "environment", "admin", "technical"},
    "pathway_type": {"stable_job", "credentialled_path", "apprenticeship", "entrepreneurial",
                     "freelance", "passion_path", "hybrid", "stepping_stone", "field_work",
                     "regulated", "sales_based"},
    "income_predictability": {"low", "medium", "high"},
}
ARRAYS = ["defensive_skills", "ai_tools_to_learn", "best_suited_for", "poor_fit_for",
          "first_90_days", "year_one_plan", "two_year_plan", "warnings"]


def main():
    records = json.loads(DATA.read_text(encoding="utf-8"))
    errors = []
    seen = set()
    for r in records:
        rid = r.get("id", "<missing id>")
        if rid in seen:
            errors.append(f"{rid}: duplicate id")
        seen.add(rid)
        keys = set(r.keys())
        if keys != FIELDS:
            errors.append(f"{rid}: fields missing={sorted(FIELDS - keys)} extra={sorted(keys - FIELDS)}")
            continue
        inc = r["income_range_cad"]
        if not (isinstance(inc.get("entry"), int) and isinstance(inc.get("mid"), int)
                and isinstance(inc.get("experienced"), int) and inc["entry"] < inc["mid"] < inc["experienced"]):
            errors.append(f"{rid}: bad income_range_cad {inc}")
        tti = r["time_to_income_years"]
        if not (isinstance(tti.get("min"), (int, float)) and isinstance(tti.get("max"), (int, float))
                and tti["min"] <= tti["max"]):
            errors.append(f"{rid}: bad time_to_income_years {tti}")
        for f, allowed in ENUMS.items():
            if r[f] not in allowed:
                errors.append(f"{rid}: {f}={r[f]!r} invalid")
        for f in ARRAYS:
            if not (isinstance(r[f], list) and r[f] and all(isinstance(x, str) and x.strip() for x in r[f])):
                errors.append(f"{rid}: bad array {f}")
    if errors:
        print(f"FAILED — {len(errors)} problem(s):")
        for e in errors[:50]:
            print(" -", e)
        sys.exit(1)
    fams = Counter(r["career_family"] for r in records)
    types = Counter(r["pathway_type"] for r in records)
    print(f"OK: {len(records)} records")
    for f, n in fams.most_common():
        print(f"  {f:18s} {n}")
    print("  pathway types:", dict(types))


if __name__ == "__main__":
    main()
