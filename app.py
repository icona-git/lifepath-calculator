"""
LifePath Calculator — prototype.

Turns a desired future lifestyle into a cost estimate, a gross income target,
and three realistic career/pathway plans (Safe / Balanced / Bold), with AI risk
and AI leverage shown for every recommended path.

All assumptions live in data/*.json so they can be edited freely now and
swapped for database-backed admin records later. Pure engine functions sit at
the top of this file and never touch Streamlit widgets, so they can be imported
and tested without a running app (`import app; app.compute_results(...)`).

Run locally:  streamlit run app.py
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"

DISCLAIMER = (
    "This is a planning prototype, not financial, tax, career, or legal advice. "
    "Costs, wages, taxes, and job markets change. Use this as a decision-support "
    "tool, then verify important decisions with current sources and qualified "
    "advisors where needed."
)

AI_DISCLAIMER = (
    "AI risk ratings are directional, not predictions. They estimate whether parts "
    "of a career path may be affected by AI tools over the next 3 to 5 years. A high "
    "AI exposure rating may also mean high opportunity for people who learn to use AI well."
)

STABLE_CATEGORIES = {"Public Sector & Stability", "Healthcare & Care Work", "Trades & Technical"}
SELF_EMPLOYED_CATEGORIES = {"Freelance & Self-Employment", "AI-Era Entrepreneurship"}

LEVEL_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Very High": 3}


# --------------------------------------------------------------------------
# Data access — single seam to swap JSON files for a database/admin later
# --------------------------------------------------------------------------

@st.cache_data
def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def costs():
    return _load("cost_assumptions.json")


def tax_model():
    return _load("tax_assumptions.json")


def options():
    return _load("questionnaire_options.json")


def career_paths():
    return _load("career_paths.json")


def path_by_id(pid):
    for rec in career_paths():
        if rec["id"] == pid:
            return rec
    return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def round_to(n, step):
    return int(round(n / step) * step)


def money(n):
    return f"${n:,.0f}"


def esc(text):
    """Escape $ for Streamlit markdown — paired dollar signs otherwise render as LaTeX."""
    return text.replace("$", "\\$")


# --------------------------------------------------------------------------
# Engine: budget
# --------------------------------------------------------------------------

def compute_budget(p):
    """Monthly budget line items + totals (with a deliberate low/high range)."""
    C = costs()
    O = options()
    level = O["lifestyle_levels"][p["lifestyle_level"]]
    housing_tier = p["housing_tier"]

    housing = C["housing"][housing_tier]["monthly"]
    vehicle = C["vehicle"][p["vehicle_tier"]]["monthly"]
    food = C["food"][level["food"]]

    if housing_tier == "family_support":
        utilities = 0
        utilities_note = "Assumed included in family contribution"
    elif housing_tier == "shared":
        utilities = C["utilities"]["shared_or_basic"]
        utilities_note = "Split with roommates"
    else:
        utilities = C["utilities"][level["utilities"]]
        utilities_note = ""

    phone = C["phone"][level["phone"]]
    internet = C["internet"]["shared"] if housing_tier in ("family_support", "shared") else C["internet"]["standard"]
    tenant_ins = 0 if housing_tier == "family_support" else C["tenant_insurance"]["renting"]
    health = C["health_personal"][level["health"]]
    lifestyle = C["lifestyle"][level["lifestyle"]]
    travel = C["travel"][p["travel_tier"]]
    savings = C["savings"][p["savings_tier"]]
    education = C["education"][p["education_tier"]]
    debt = int(p.get("debt_payment") or 0)

    lines = [
        ("Housing", housing, C["housing"][housing_tier]["label"]),
        ("Utilities", utilities, utilities_note),
        ("Tenant/home insurance", tenant_ins, ""),
        ("Groceries", food, ""),
        ("Phone", phone, ""),
        ("Internet", internet, ""),
        ("Transportation", vehicle, C["vehicle"][p["vehicle_tier"]]["label"]),
        ("Lifestyle & everyday", lifestyle, "Clothing, subscriptions, entertainment, fitness/hobbies"),
        ("Health, dental & personal care", health, ""),
        ("Travel fund", travel, ""),
        ("Savings & investing", savings, ""),
        ("Education", education, ""),
        ("Debt repayment", debt, "From your current monthly debt payment"),
    ]
    subtotal = sum(v for _, v, _ in lines)
    buffer = int(round(subtotal * C["misc_buffer_percent"]))
    lines.append(("Miscellaneous buffer", buffer, f"{int(C['misc_buffer_percent'] * 100)}% of subtotal — life happens"))
    total = subtotal + buffer

    totals = {
        "monthly_mid": total,
        "monthly_low": round_to(total * 0.93, 50),
        "monthly_high": round_to(total * 1.07, 50),
    }
    return lines, totals


# --------------------------------------------------------------------------
# Engine: gross income estimate (rough, tiered — not CRA-grade)
# --------------------------------------------------------------------------

def estimate_gross_annual(after_tax_needed):
    for tier in tax_model()["tiers"]:
        cap = tier["after_tax_up_to"]
        if cap is None or after_tax_needed <= cap:
            return after_tax_needed / tier["net_ratio"]
    return after_tax_needed / 0.60  # unreachable; defensive


def compute_targets(p):
    lines, totals = compute_budget(p)
    annual_mid = totals["monthly_mid"] * 12
    annual_low = totals["monthly_low"] * 12
    annual_high = totals["monthly_high"] * 12
    unc = tax_model()["uncertainty_percent"]

    gross_mid = round_to(estimate_gross_annual(annual_mid), 1000)
    gross_low = round_to(estimate_gross_annual(annual_low) * (1 - unc / 2), 1000)
    gross_high = round_to(estimate_gross_annual(annual_high) * (1 + unc / 2), 1000)

    current = int(p.get("current_income") or 0)
    return {
        "budget_lines": lines,
        "monthly_low": totals["monthly_low"],
        "monthly_mid": totals["monthly_mid"],
        "monthly_high": totals["monthly_high"],
        "annual_mid": round_to(annual_mid, 500),
        "annual_low": round_to(annual_low, 500),
        "annual_high": round_to(annual_high, 500),
        "gross_low": gross_low,
        "gross_mid": gross_mid,
        "gross_high": gross_high,
        "current_income": current,
        "gap": gross_mid - current,
    }


# --------------------------------------------------------------------------
# Engine: pathway scoring (weights, not rigid branching)
# --------------------------------------------------------------------------

def aligned_categories(p):
    O = options()
    cats = set(O["current_fields"].get(p.get("current_field", ""), []))
    for skill in p.get("skills", []):
        cats.update(O["skills"].get(skill, []))
    return cats


def has_ent_signal(p):
    return p.get("business_interest") == "Yes - strongly interested" or p.get("ent_interest") == "High"


def has_dream_signal(p):
    return p.get("creative_interest", "None / not a focus") != "None / not a focus" or p.get("creative_interest_ws") == "High"


def score_path(path, ctx):
    p = ctx["p"]
    req = ctx["req_gross"]
    yrs = ctx["target_years"]
    inc = path["income_range_cad"]
    score = 0
    reasons = []

    # Income fit
    if inc["mid"] >= req:
        score += 20
        reasons.append("Mid-career income meets your target")
    elif inc["experienced"] >= req:
        score += 10
        reasons.append("Can meet your target with experience")
    else:
        score -= 15
        reasons.append("Even experienced income likely falls short of your target")

    # Timeframe fit
    tti = path["time_to_income_years"]
    if tti["max"] <= yrs:
        score += 8
        reasons.append(f"Fits your {yrs}-year timeframe")
    elif tti["min"] > yrs:
        score -= 10
        reasons.append("Likely takes longer than your timeframe")

    # Risk tolerance
    risk = path["risk_level"]
    tol = p.get("risk_tolerance", "Medium")
    if risk == tol:
        score += 10
        reasons.append("Risk level matches your tolerance")
    elif tol == "Low" and risk == "High":
        score -= 12
        reasons.append("Riskier than you want")
    elif tol == "Medium" and risk == "High":
        score -= 4
    if p.get("uncertainty_comfort") == "Low" and risk == "High":
        score -= 8

    # Education willingness vs training load
    tcl = path["training_cost_level"]
    study = p.get("study_willingness", "Medium")
    if study == "Low" and tcl == "High":
        score -= 12
        reasons.append("Needs more schooling than you want to take on")
    elif study == "High" and tcl in ("Medium", "High"):
        score += 5
    if p.get("education_tier") == "none" and tcl == "High" and tti["min"] >= 2:
        score -= 6

    # Structure vs autonomy
    cat = path["category"]
    if p.get("structure_pref") == "High":
        if cat in STABLE_CATEGORIES:
            score += 6
        if cat in SELF_EMPLOYED_CATEGORIES:
            score -= 6
    if p.get("autonomy_pref") == "High" and LEVEL_ORDER[path["freelance_potential"]] >= 2:
        score += 8
        reasons.append("High autonomy / freelance potential")

    # Sales comfort
    sr = path["sales_requirement"]
    sales = p.get("sales_comfort", "Medium")
    if sales == "Low" and sr == "High":
        score -= 12
        reasons.append("Demands more selling than you're comfortable with")
    elif sales == "High" and sr == "High":
        score += 8
        reasons.append("Uses your comfort with selling")

    # Entrepreneurship interest
    if has_ent_signal(p) and LEVEL_ORDER[path["business_potential"]] >= 2:
        score += 15
        reasons.append("Strong business-building potential")
    if p.get("ent_interest") == "Low" and cat == "AI-Era Entrepreneurship":
        score -= 10

    # Creative interest
    creative_signal = has_dream_signal(p) or p.get("creative_interest_ws") in ("Medium", "High")
    if creative_signal and cat == "Creative & Passion":
        score += 12
        reasons.append("Aligns with your creative interest")
    if p.get("creative_interest_ws") == "Low" and cat == "Creative & Passion":
        score -= 6

    # AI interest and disruption tolerance
    if p.get("ai_interest") == "High" and LEVEL_ORDER[path["ai_leverage_potential"]] >= 2:
        score += 10
        reasons.append("High AI leverage if you learn the tools")
    if p.get("ai_interest") == "Low" and path["ai_exposure_level"] == "Very High":
        score -= 6
    needs_income_soon = str(p.get("income_speed", "")).startswith("Yes")
    if needs_income_soon and path["ai_disruption_risk"] == "High":
        score -= 10
        reasons.append("AI-pressured entry market is risky when you need income soon")
    if needs_income_soon:
        if tti["min"] > 1:
            score -= 8
        elif tti["max"] <= 1:
            score += 6
            reasons.append("Fast first income")

    # Labour market signal
    market_pts = {"Hot": 8, "Warm": 4, "Balanced": 0, "Cool": -4, "Cold": -8}
    score += market_pts[path["job_market_signal"]]
    if path["job_market_signal"] == "Hot":
        reasons.append("Strong Alberta demand signal")
    elif path["job_market_signal"] == "Cold":
        reasons.append("Weak Alberta demand signal")

    # Existing skills / field alignment
    if cat in ctx["aligned_cats"]:
        score += 12
        reasons.append("Builds on your existing skills or training")

    # Direct interest dials
    if p.get("stable_interest") == "High" and risk == "Low":
        score += 8
        reasons.append("The stable employment you said you want")
    if p.get("freelance_interest") == "High" and (cat == "Freelance & Self-Employment" or path["freelance_potential"] == "Very High"):
        score += 10
    if p.get("trades_interest") == "High" and cat == "Trades & Technical":
        score += 12
        reasons.append("Matches your interest in trades")
    if p.get("trades_interest") == "Low" and cat == "Trades & Technical":
        score -= 6

    return score, reasons


def rank_paths(ctx):
    scored = []
    for path in career_paths():
        s, r = score_path(path, ctx)
        scored.append({"path": path, "score": s, "reasons": r})
    scored.sort(key=lambda x: (-x["score"], -x["path"]["income_range_cad"]["mid"]))
    return scored


# --------------------------------------------------------------------------
# Engine: three plans
# --------------------------------------------------------------------------

def build_plans(ranked, ctx):
    p = ctx["p"]
    used = set()

    def first(pool):
        for item in pool:
            if item["path"]["id"] not in used:
                return item
        return None

    # Safe: stable, lower-risk income reliability
    safe_pool = [x for x in ranked if x["path"]["risk_level"] == "Low"]
    if not safe_pool:
        safe_pool = [x for x in ranked
                     if x["path"]["risk_level"] == "Medium"
                     and x["path"]["category"] not in SELF_EMPLOYED_CATEGORIES
                     and x["path"]["sales_requirement"] != "High"]
    safe = first(safe_pool) or first(ranked)
    used.add(safe["path"]["id"])

    # Bold: entrepreneurship-first or dream-first
    bold_pool = [x for x in ranked if x["path"]["category"] == "AI-Era Entrepreneurship"
                 or x["path"]["business_potential"] == "Very High"]
    if has_dream_signal(p):
        bold_pool = [x for x in ranked
                     if x["path"]["category"] in ("AI-Era Entrepreneurship", "Creative & Passion", "Freelance & Self-Employment")
                     or x["path"]["business_potential"] == "Very High"]
    bold = first(bold_pool) or first(ranked)
    used.add(bold["path"]["id"])

    # Balanced: best remaining practical income engine
    bal_pool = [x for x in ranked
                if x["path"]["risk_level"] != "High"
                and x["path"]["income_range_cad"]["mid"] >= ctx["req_gross"] * 0.8]
    balanced = first(bal_pool) or first(ranked)
    used.add(balanced["path"]["id"])

    # Balanced side-track: protect the passion / develop the business muscle
    side = None
    if has_dream_signal(p):
        # Prefer the user's actual dream domain, then any creative path
        pref = DREAM_PATH_MAP.get(p.get("creative_interest", ""), ())
        side_pool = [x for x in ranked if x["path"]["id"] in pref]
        side_pool += [x for x in ranked if x["path"]["category"] == "Creative & Passion"]
    elif has_ent_signal(p):
        side_pool = [x for x in ranked if x["path"]["category"] in SELF_EMPLOYED_CATEGORIES]
    elif p.get("freelance_interest") == "High":
        side_pool = [x for x in ranked if x["path"]["category"] == "Freelance & Self-Employment"]
    else:
        side_pool = []
    side_item = first(side_pool)
    if side_item:
        side = side_item
        used.add(side["path"]["id"])

    return {"safe": safe, "balanced": balanced, "bold": bold, "side": side}


# --------------------------------------------------------------------------
# Engine: entrepreneurship readiness
# --------------------------------------------------------------------------

FIRST_OFFER_BY_SKILL = {
    "Coding / software": "a simple website or automation package for one local business niche",
    "AI tools / automation": "an AI workflow setup for one repetitive office process",
    "Music / audio": "paid lessons, or session/editing services",
    "Design / visual": "a fixed-price brand starter package",
    "Writing": "a monthly content package for one industry",
    "Video / photography": "event or small-business shooting packages",
    "Sales / persuasion": "commission-based selling for a local service company",
    "Teaching / coaching": "tutoring blocks in a subject you already know",
    "Hands-on / mechanical": "a mobile small-repair or install service",
}


def ent_readiness(p, monthly_mid):
    answers = p.get("ent_answers")
    if not p.get("ent_shown") or not answers:
        return {"assessed": False}
    O = options()
    yes = sum(1 for v in answers.values() if v)
    if yes <= 2:
        idx = 0
    elif yes <= 4:
        idx = 1
    elif yes <= 6:
        idx = 2
    elif yes <= 8:
        idx = 3
    else:
        idx = 4

    savings = int(p.get("savings") or 0)
    runway_months = (savings / monthly_mid) if monthly_mid else 0

    first_offer = "a small, concrete service one specific customer type already pays for"
    for skill in p.get("skills", []):
        if skill in FIRST_OFFER_BY_SKILL:
            first_offer = FIRST_OFFER_BY_SKILL[skill]
            break

    return {
        "assessed": True,
        "level_idx": idx,
        "level": O["readiness_levels"][idx],
        "yes_count": yes,
        "total": len(answers),
        "runway_months": runway_months,
        "first_offer": first_offer,
        "missing": [q["label"] for q in O["ent_questions"] if not answers.get(q["key"])],
    }


# --------------------------------------------------------------------------
# Engine: dream path analysis
# --------------------------------------------------------------------------

DREAM_PATH_MAP = {
    "Music / performance": ("musician_performer", "creative_business_owner"),
    "Visual art / illustration": ("artist_illustrator", "creative_business_owner"),
    "Content creation / video": ("content_creator", "ai_content_studio"),
    "Writing": ("freelance_copywriter", "niche_media_business"),
    "Photography / film": ("photographer_videographer", "creative_business_owner"),
    "Design": ("graphic_designer", "freelance_designer"),
    "Other creative pursuit": ("creative_agency_freelancer", "creative_business_owner"),
}


def dream_analysis(p, plans, targets):
    interest = p.get("creative_interest", "None / not a focus")
    if interest == "None / not a focus":
        return None
    direct_id, biz_id = DREAM_PATH_MAP.get(interest, DREAM_PATH_MAP["Other creative pursuit"])
    direct = path_by_id(direct_id)
    biz = path_by_id(biz_id)
    income_engine = plans["balanced"]["path"]
    if direct is None or biz is None:
        return None
    return {
        "interest": interest,
        "direct": direct,
        "income_engine": income_engine,
        "business": biz,
        "direct_covers_target": direct["income_range_cad"]["mid"] >= targets["gross_mid"],
    }


# --------------------------------------------------------------------------
# Engine: hard truths + next moves + roadmap
# --------------------------------------------------------------------------

def hard_truths(p, t, plans, dream):
    truths = []
    current = t["current_income"]

    if current == 0:
        truths.append(
            f"You have no current income, so this entire plan rests on the pathway you choose. "
            f"Your target life needs roughly {money(t['gross_low'])}–{money(t['gross_high'])} gross per year — "
            f"treat the first income milestone as non-negotiable."
        )
    elif t["gross_mid"] > current * 1.25:
        truths.append(
            f"Your target lifestyle is not supported by your current income "
            f"({money(current)} now vs roughly {money(t['gross_mid'])} needed). You need either a "
            f"lower-cost version of the lifestyle or a stronger income path — there is no third option."
        )

    vehicle_line = next((v for label, v, _ in t["budget_lines"] if label == "Transportation"), 0)
    if p.get("vehicle_tier") not in (None, "none") and t["monthly_mid"] and vehicle_line / t["monthly_mid"] >= 0.15:
        truths.append(
            f"The vehicle is absorbing about {int(round(100 * vehicle_line / t['monthly_mid']))}% of your monthly budget "
            f"({money(vehicle_line)}/mo). Delaying or downgrading the car by a year would make the rest of the plan noticeably more realistic."
        )

    if dream and not dream["direct_covers_target"]:
        truths.append(
            f"The {dream['interest'].lower()} path is possible, but relying on it immediately for full income is high-risk — "
            f"typical mid-level earnings ({money(dream['direct']['income_range_cad']['mid'])}) sit below your target. "
            f"A passion-plus-income strategy is safer."
        )

    aligned = aligned_categories(p)
    plan_cats = {plans[k]["path"]["category"] for k in ("safe", "balanced", "bold")}
    educated = p.get("education_level") in ("Certificate or diploma", "Trade ticket / apprenticeship", "Bachelor's degree", "Graduate degree")
    if educated and aligned and not (aligned & plan_cats):
        truths.append(
            "Your existing education and skills are an economic asset. Walking away from them completely would be "
            "wasteful unless you are replacing them with a clearly stronger plan — consider pathways that reuse what you already paid for."
        )

    if has_ent_signal(p):
        truths.append(
            "Entrepreneurship fits your independence goals, but it is not a shortcut: you need sales, pricing, delivery, "
            "and basic financial skills before depending on it for rent."
        )

    primary = plans["balanced"]["path"]
    if LEVEL_ORDER[primary["ai_exposure_level"]] >= 2:
        truths.append(
            f"{primary['name']} has {primary['ai_exposure_level'].lower()} AI exposure. You need to become the person who "
            f"uses AI to deliver more, not the person whose basic tasks are replaced by it."
        )

    debt = int(p.get("debt_payment") or 0)
    if t["monthly_mid"] and debt / t["monthly_mid"] > 0.12:
        truths.append(
            f"Debt repayment is taking {money(debt)} of every month. Clearing the highest-interest balance first is "
            f"probably worth more than any optimization elsewhere in this plan."
        )

    return truths[:6]


def next_moves(p, t, plans, readiness):
    moves = []
    primary = plans["balanced"]["path"]
    moves.append((f"Start the income engine: {primary['name']}", primary["first_90_days"][0]))

    if p.get("education_tier", "none") != "none":
        label = options()["education_labels"][p["education_tier"]]
        moves.append((
            "Lock in the education step",
            f"You budgeted for '{label}'. Confirm programs, intake dates, and real costs this month so the plan has a start date.",
        ))
    else:
        amt = next((v for label, v, _ in t["budget_lines"] if label == "Savings & investing"), 0) or 100
        moves.append((
            "Automate the money",
            f"Open a separate account and automate {money(amt)}/month toward your buffer — the plan's savings line, made real.",
        ))

    if readiness.get("assessed") and readiness["level_idx"] >= 2:
        moves.append((
            "Test a first offer",
            f"Offer {readiness['first_offer']} to 10 real prospects and try to land 1–3 paying customers before spending anything on branding.",
        ))
    elif plans["side"]:
        side = plans["side"]["path"]
        moves.append((f"Protect the passion: {side['name']}", side["first_90_days"][0]))
    else:
        moves.append(("Build proof", primary["first_90_days"][1] if len(primary["first_90_days"]) > 1 else "Document your progress publicly."))

    return moves[:3]


def build_roadmap(plans):
    primary = plans["balanced"]["path"]
    side = plans["side"]["path"] if plans["side"] else None
    r90 = list(primary["first_90_days"])
    if side:
        r90.append(f"Side track — {side['first_90_days'][0]}")
    y1 = list(primary["year_one_plan"])
    if side:
        y1.append(f"Side track — {side['year_one_plan'][0]}")
    y2 = list(primary["two_year_plan"]) + ["Reassess this plan against reality and adjust the targets"]
    return {"r90": r90, "y1": y1, "y2": y2}


# --------------------------------------------------------------------------
# Engine: one entry point used by the results page (and smoke tests)
# --------------------------------------------------------------------------

def compute_results(p):
    t = compute_targets(p)
    O = options()
    yrs = p.get("timeframe_years") or O["timeframes"].get(p.get("timeframe_label", "5 years")) or 5
    ctx = {
        "p": p,
        "req_gross": t["gross_mid"],
        "target_years": yrs,
        "aligned_cats": aligned_categories(p),
    }
    ranked = rank_paths(ctx)
    plans = build_plans(ranked, ctx)
    readiness = ent_readiness(p, t["monthly_mid"])
    dream = dream_analysis(p, plans, t)
    return {
        "targets": t,
        "years": yrs,
        "ranked": ranked,
        "plans": plans,
        "readiness": readiness,
        "dream": dream,
        "truths": hard_truths(p, t, plans, dream),
        "moves": next_moves(p, t, plans, readiness),
        "roadmap": build_roadmap(plans),
    }


# --------------------------------------------------------------------------
# Sample profile (drives the demo button and engine smoke tests)
# --------------------------------------------------------------------------

SAMPLE_PROFILE = {
    "age_range": "20-24",
    "city": "Calgary, Alberta",
    "housing_now": "Renting - shared with roommates",
    "education_level": "Certificate or diploma",
    "work_status": "Working part-time",
    "current_income": 32000,
    "take_home_monthly": 2100,
    "savings": 6000,
    "debt_total": 4000,
    "debt_payment": 150,
    "skills": ["Music / audio", "Coding / software", "Customer service"],
    "current_field": "Creative / arts / music / media",
    "support_level": "Some support (partner or family)",
    "timeframe_label": "5 years",
    "timeframe_years": 5,
    "target_city": "Calgary, Alberta",
    "business_interest": "Curious",
    "creative_interest": "Music / performance",
    "lifestyle_level": "Stable and realistic",
    "balance": "Balanced",
    "housing_tier": "stable_one_bedroom",
    "vehicle_tier": "reliable_used",
    "travel_tier": "one_modest_trip",
    "savings_tier": "three_month_buffer",
    "education_tier": "part_time_courses",
    "risk_tolerance": "Medium",
    "income_speed": "Somewhat - within a year or two",
    "study_willingness": "High",
    "evenings_weekends": "High",
    "structure_pref": "Medium",
    "autonomy_pref": "High",
    "sales_comfort": "Medium",
    "uncertainty_comfort": "Medium",
    "ai_interest": "High",
    "ent_interest": "High",
    "stable_interest": "Medium",
    "freelance_interest": "High",
    "trades_interest": "Low",
    "creative_interest_ws": "High",
    "ent_shown": True,
    "ent_answers": {
        "sales": True, "accounting": True, "marketing": True, "customers": True,
        "rejection": True, "paid_skill": True, "runway": False, "start_small": True, "training": True,
    },
}


# ==========================================================================
# UI — everything below renders Streamlit and never runs on plain import
# ==========================================================================

RISK_COLORS = {"Low": "#2e7d32", "Medium": "#a67c00", "High": "#d35400", "Very High": "#c0392b"}
GOOD_COLORS = {"Low": "#8a8a8a", "Medium": "#7d9a44", "High": "#2e7d32", "Very High": "#1b5e20"}
MARKET_COLORS = {"Hot": "#2e7d32", "Warm": "#5e9c52", "Balanced": "#8a8a8a", "Cool": "#c47f17", "Cold": "#b3402f"}


def pill(text, color):
    return (
        f'<span style="background:{color};color:#fff;padding:2px 10px;border-radius:999px;'
        f'font-size:0.78rem;font-weight:600;white-space:nowrap;display:inline-block;margin:2px 4px 2px 0;">{text}</span>'
    )


def ai_pills(path):
    return "".join([
        pill(f"AI exposure: {path['ai_exposure_level']}", RISK_COLORS[path["ai_exposure_level"]]),
        pill(f"Disruption risk: {path['ai_disruption_risk']}", RISK_COLORS[path["ai_disruption_risk"]]),
        pill(f"AI leverage: {path['ai_leverage_potential']}", GOOD_COLORS[path["ai_leverage_potential"]]),
    ])


def p_state():
    if "p" not in st.session_state:
        st.session_state.p = {}
    return st.session_state.p


def go(step):
    st.session_state.step = step
    st.rerun()


def nav_buttons(back_step, label="Continue →"):
    c1, c2 = st.columns([1, 2])
    back = c1.form_submit_button("← Back")
    nxt = c2.form_submit_button(label, type="primary")
    return back, nxt


# ---------------------------- Step 0: welcome -----------------------------

def step_welcome():
    C = costs()
    st.title("🧭 LifePath Calculator")
    st.subheader("Describe the life you want. See what it costs. Compare the paths that can get you there.")
    st.write(
        "This tool helps you turn a future lifestyle goal into a realistic income and pathway plan. "
        "It asks what life you want in your chosen timeframe and where you are now, estimates what that "
        "life costs, calculates the gross income that supports it, then compares realistic career, education, "
        "freelance, trades, employment, and entrepreneurship pathways — including where AI may bite and where "
        "it can be your leverage."
    )
    st.info(DISCLAIMER, icon="ℹ️")
    st.caption(
        f"Assumptions: {C['market']}, {C['currency']}, updated {C['last_updated']}. "
        "Takes about 8–10 minutes. Nothing is saved or sent anywhere."
    )
    c1, c2 = st.columns([1, 1])
    if c1.button("Start →", type="primary"):
        go(1)
    if c2.button("Try a sample profile (demo)"):
        st.session_state.p = dict(SAMPLE_PROFILE)
        go(6)


# ---------------------- Step 1: current situation -------------------------

def step_current():
    p = p_state()
    O = options()
    st.header("Step 1 — Where you are now")
    st.caption("Honest inputs make honest plans. Rough numbers are fine.")

    with st.form("current"):
        c1, c2 = st.columns(2)
        with c1:
            age = st.selectbox("Age range", O["age_ranges"], index=O["age_ranges"].index(p.get("age_range", "20-24")))
            city = st.text_input("Current city/province", value=p.get("city", "Calgary, Alberta"))
            housing_now = st.selectbox("Current housing situation", O["housing_now"],
                                       index=O["housing_now"].index(p.get("housing_now", O["housing_now"][0])))
            education = st.selectbox("Current education level", O["education_levels"],
                                     index=O["education_levels"].index(p.get("education_level", O["education_levels"][0])))
            work = st.selectbox("Current work status", O["work_status"],
                                index=O["work_status"].index(p.get("work_status", O["work_status"][0])))
            support = st.selectbox("Current support level", O["support_levels"],
                                   index=O["support_levels"].index(p.get("support_level", O["support_levels"][2])))
        with c2:
            income = st.number_input("Current gross annual income (CAD)", min_value=0, max_value=1_000_000,
                                     value=int(p.get("current_income", 0)), step=1000)
            take_home = st.number_input("Current monthly take-home (optional)", min_value=0, max_value=50_000,
                                        value=int(p.get("take_home_monthly", 0)), step=100)
            savings = st.number_input("Current savings (CAD)", min_value=0, max_value=10_000_000,
                                      value=int(p.get("savings", 0)), step=500)
            debt_total = st.number_input("Current debt total (CAD)", min_value=0, max_value=10_000_000,
                                         value=int(p.get("debt_total", 0)), step=500)
            debt_payment = st.number_input("Current monthly debt payment (CAD)", min_value=0, max_value=50_000,
                                           value=int(p.get("debt_payment", 0)), step=25)
        skills = st.multiselect("Your main skills today", list(O["skills"].keys()), default=p.get("skills", []))
        field = st.selectbox("Current field or recent training", list(O["current_fields"].keys()),
                             index=list(O["current_fields"].keys()).index(p.get("current_field", "Other / no field yet")))

        back, nxt = nav_buttons(0)
    if back or nxt:
        p.update({
            "age_range": age, "city": city, "housing_now": housing_now, "education_level": education,
            "work_status": work, "support_level": support, "current_income": income,
            "take_home_monthly": take_home, "savings": savings, "debt_total": debt_total,
            "debt_payment": debt_payment, "skills": skills, "current_field": field,
        })
        go(0 if back else 2)


# ------------------------- Step 2: future target --------------------------

def step_future():
    p = p_state()
    O = options()
    st.header("Step 2 — The life you're aiming for")

    with st.form("future"):
        c1, c2 = st.columns(2)
        with c1:
            tf_labels = list(O["timeframes"].keys())
            tf = st.selectbox("Target timeframe", tf_labels, index=tf_labels.index(p.get("timeframe_label", "5 years")))
            custom_years = st.number_input("If Custom: how many years?", min_value=1, max_value=20,
                                           value=int(p.get("timeframe_years") or 5))
            target_city = st.text_input("Desired location", value=p.get("target_city", "Calgary, Alberta"),
                                        help="Costs stay Calgary-based in this prototype — treat other cities as directional.")
            lifestyle = st.selectbox("Desired lifestyle level", list(O["lifestyle_levels"].keys()),
                                     index=list(O["lifestyle_levels"].keys()).index(p.get("lifestyle_level", "Stable and realistic")))
        with c2:
            business = st.radio("Interest in business / entrepreneurship", O["business_interest"],
                                index=O["business_interest"].index(p.get("business_interest", O["business_interest"][0])))
            creative = st.selectbox("Creative / dream interest", O["creative_interests"],
                                    index=O["creative_interests"].index(p.get("creative_interest", O["creative_interests"][0])))
            balance = st.radio("Desired work-life balance", O["balance"],
                               index=O["balance"].index(p.get("balance", "Balanced")))

        back, nxt = nav_buttons(1)
    if back or nxt:
        years = O["timeframes"][tf] if O["timeframes"][tf] else int(custom_years)
        p.update({
            "timeframe_label": tf, "timeframe_years": years, "target_city": target_city,
            "lifestyle_level": lifestyle, "business_interest": business,
            "creative_interest": creative, "balance": balance,
        })
        go(1 if back else 3)


# ------------------------ Step 3: lifestyle tiers --------------------------

def step_tiers():
    p = p_state()
    C = costs()
    O = options()
    st.header("Step 3 — Pick your tiers")
    st.caption("These drive the budget. Every dollar figure is an editable Calgary assumption, not a quote.")

    with st.form("tiers"):
        housing_keys = list(C["housing"].keys())
        housing = st.selectbox(
            "Housing goal", housing_keys,
            index=housing_keys.index(p.get("housing_tier", "stable_one_bedroom")),
            format_func=lambda k: f"{C['housing'][k]['label']} — ~{money(C['housing'][k]['monthly'])}/mo",
        )
        vehicle_keys = list(C["vehicle"].keys())
        vehicle = st.selectbox(
            "Vehicle goal", vehicle_keys,
            index=vehicle_keys.index(p.get("vehicle_tier", "older_used")),
            format_func=lambda k: f"{C['vehicle'][k]['label']} — ~{money(C['vehicle'][k]['monthly'])}/mo",
        )
        travel_keys = list(C["travel"].keys())
        travel = st.selectbox(
            "Travel goal", travel_keys,
            index=travel_keys.index(p.get("travel_tier", "one_modest_trip")),
            format_func=lambda k: f"{O['travel_labels'][k]} — ~{money(C['travel'][k])}/mo set aside",
        )
        savings_keys = list(C["savings"].keys())
        savings = st.selectbox(
            "Savings goal", savings_keys,
            index=savings_keys.index(p.get("savings_tier", "starter")),
            format_func=lambda k: f"{O['savings_labels'][k]} — ~{money(C['savings'][k])}/mo",
        )
        edu_keys = list(C["education"].keys())
        edu = st.selectbox(
            "Education goal", edu_keys,
            index=edu_keys.index(p.get("education_tier", "none")),
            format_func=lambda k: f"{O['education_labels'][k]} — ~{money(C['education'][k])}/mo",
        )

        back, nxt = nav_buttons(2)
    if back or nxt:
        p.update({
            "housing_tier": housing, "vehicle_tier": vehicle, "travel_tier": travel,
            "savings_tier": savings, "education_tier": edu,
        })
        go(2 if back else 4)


# --------------------- Step 4: work style & risk ---------------------------

def step_workstyle():
    p = p_state()
    O = options()
    st.header("Step 4 — How you like to work")
    st.caption("This shapes which pathways score well for you. There are no wrong answers.")

    with st.form("workstyle"):
        speed = st.radio("Do you need income quickly?", O["income_speed"],
                         index=O["income_speed"].index(p.get("income_speed", O["income_speed"][1])))
        st.divider()
        cols = st.columns(2)
        values = {}
        for i, q in enumerate(O["workstyle_questions"]):
            with cols[i % 2]:
                values[q["key"]] = st.select_slider(
                    q["label"], options=O["levels3"],
                    value=p.get(q["key"], "Medium"),
                    help=q["help"] or None,
                    key=f"ws_{q['key']}",
                )
        back, nxt = nav_buttons(3)
    if back or nxt:
        p.update(values)
        p["income_speed"] = speed
        if back:
            go(3)
        ent_relevant = p.get("business_interest") != "No / not now" or values.get("ent_interest") in ("Medium", "High")
        p["ent_shown"] = bool(ent_relevant)
        go(5 if ent_relevant else 6)


# ------------------- Step 5: entrepreneurship check ------------------------

def step_ent():
    p = p_state()
    O = options()
    st.header("Step 5 — Entrepreneurship readiness")
    st.write(
        "Entrepreneurship can create independence, but it is not a shortcut. It requires sales, delivery, "
        "pricing, customer service, financial discipline, and resilience. Answer honestly — this only "
        "changes the advice, not your worth."
    )
    with st.form("ent"):
        answers = {}
        for q in O["ent_questions"]:
            prev = p.get("ent_answers", {}).get(q["key"], False)
            answers[q["key"]] = st.toggle(q["label"], value=prev, key=f"ent_{q['key']}")
        back, nxt = nav_buttons(4, label="See my plan →")
    if back or nxt:
        p["ent_answers"] = answers
        go(4 if back else 6)


# ---------------------------- Results page --------------------------------

def render_path_card(item, plan_note):
    path = item["path"]
    inc = path["income_range_cad"]
    with st.container(border=True):
        st.markdown(f"#### {path['name']}")
        st.caption(f"{path['category']} · {plan_note}")
        st.markdown(
            pill(f"Risk: {path['risk_level']}", RISK_COLORS[path["risk_level"]])
            + pill(f"Market: {path['job_market_signal']}", MARKET_COLORS[path["job_market_signal"]])
            + pill(f"Training cost: {path['training_cost_level']}", RISK_COLORS[path["training_cost_level"]])
            + ai_pills(path),
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Entry", money(inc["entry"]))
        c2.metric("Mid", money(inc["mid"]))
        c3.metric("Experienced", money(inc["experienced"]))
        st.markdown(esc(f"**Time to income:** {path['time_to_income']}"))
        st.markdown(esc(f"**Training:** {path['training_required']}"))
        if item["reasons"]:
            st.markdown(esc("**Why it scored well for you:** " + " · ".join(item["reasons"][:4])))
        with st.expander("First 90 days"):
            for step_item in path["first_90_days"]:
                st.markdown(esc(f"- {step_item}"))


def render_results():
    p = p_state()
    if "housing_tier" not in p:
        st.warning("No answers yet — start the questionnaire first.")
        if st.button("← To the start"):
            go(0)
        return

    O = options()
    r = compute_results(p)
    t = r["targets"]
    plans = r["plans"]

    st.title("Your LifePath plan")

    # 1. Target life summary
    st.header("1 · Your target life")
    C = costs()
    summary = (
        f"In **{r['years']} years**, in **{p.get('target_city', 'Calgary')}**: "
        f"{C['housing'][p['housing_tier']]['label'].lower()}, "
        f"{C['vehicle'][p['vehicle_tier']]['label'].lower()}, "
        f"{O['travel_labels'][p['travel_tier']].lower()}, "
        f"{O['savings_labels'][p['savings_tier']].lower()}, "
        f"education: {O['education_labels'][p['education_tier']].lower()} — "
        f"**{p['lifestyle_level'].lower()}** lifestyle, {p['balance'].lower()} pace."
    )
    st.markdown(summary)
    if p.get("creative_interest", "None / not a focus") != "None / not a focus":
        st.markdown(f"Creative focus to protect: **{p['creative_interest']}**.")

    # 2-4. Cost, income, gap
    st.header("2 · What it costs, and the income that supports it")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Monthly cost", f"{money(t['monthly_low'])}–{money(t['monthly_high'])}", help="After-tax spending, including buffer")
    m2.metric("Annual after-tax need", f"≈ {money(t['annual_mid'])}")
    m3.metric("Gross income target", f"{money(t['gross_low'])}–{money(t['gross_high'])}", help="Rough gross salary equivalent — see tax caveat below")
    gap_label = money(t["gap"]) if t["gap"] > 0 else "On track"
    m4.metric("Gap vs current income", gap_label,
              delta=f"earning {money(t['current_income'])} today" if t["current_income"] else "no current income",
              delta_color="off")
    st.markdown(esc(
        f"Based on the lifestyle you described, you likely need approximately **{money(t['monthly_low'])} to "
        f"{money(t['monthly_high'])} per month after tax**. That implies a rough gross salary target of about "
        f"**{money(t['gross_low'])} to {money(t['gross_high'])} per year**, depending on deductions, benefits, and tax details."
    ))
    st.caption(tax_model()["note"])

    with st.expander("Monthly budget breakdown", expanded=False):
        df = pd.DataFrame(
            [(label, money(v), note) for label, v, note in t["budget_lines"]],
            columns=["Category", "Monthly", "Notes"],
        )
        st.dataframe(df, hide_index=True)
        st.markdown(esc(f"**Total: {money(t['monthly_mid'])}/month** (planning range {money(t['monthly_low'])}–{money(t['monthly_high'])})"))

    # 5. Three plans
    st.header("3 · Three possible plans")
    tab_safe, tab_bal, tab_bold = st.tabs(["🛡️ Safe Plan", "⚖️ Balanced Plan", "🚀 Bold Plan"])
    with tab_safe:
        st.markdown("**Stable, lower-risk employment/training path. Prioritizes income reliability.**")
        render_path_card(plans["safe"], "Safe plan — income reliability first")
    with tab_bal:
        st.markdown("**Practical income engine plus passion or entrepreneurship development. Often the best default.**")
        render_path_card(plans["balanced"], "Balanced plan — primary income engine")
        if plans["side"]:
            st.markdown("**…paired with a development track you actually care about:**")
            render_path_card(plans["side"], "Balanced plan — side development track")
    with tab_bold:
        st.markdown("**Entrepreneurship-first or dream-first route. Higher ceiling, weaker floor.**")
        render_path_card(plans["bold"], "Bold plan — higher risk, higher ownership")
        savings_now = int(p.get("savings") or 0)
        runway = savings_now / t["monthly_mid"] if t["monthly_mid"] else 0
        need = t["monthly_mid"] * 6
        st.warning(esc(
            f"**Runway check:** you have about **{runway:.1f} months** of costs saved ({money(savings_now)}). "
            f"A bold path wants ~6 months ({money(need)}). "
            f"**Fallback trigger:** if this path isn't covering at least half your monthly costs by month 9, "
            f"shift to the Balanced plan without treating it as failure."),
            icon="⚠️",
        )
        if p.get("balance") == "Protect time and wellbeing":
            st.caption("You said you want to protect time and wellbeing — note that bold paths usually front-load evenings and weekends.")

    # Comparison table
    with st.expander("Compare the recommended pathways side by side"):
        rows = []
        for key, label in (("safe", "Safe"), ("balanced", "Balanced"), ("side", "Balanced — side"), ("bold", "Bold")):
            item = plans.get(key)
            if not item:
                continue
            path = item["path"]
            rows.append({
                "Plan": label,
                "Pathway": path["name"],
                "Category": path["category"],
                "Entry": money(path["income_range_cad"]["entry"]),
                "Mid": money(path["income_range_cad"]["mid"]),
                "Experienced": money(path["income_range_cad"]["experienced"]),
                "Time to income": path["time_to_income"],
                "Risk": path["risk_level"],
                "Market": path["job_market_signal"],
                "AI exposure": path["ai_exposure_level"],
                "AI leverage": path["ai_leverage_potential"],
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True)

    # 6. AI risk snapshot
    st.header("4 · AI risk snapshot")
    st.caption(AI_DISCLAIMER)
    snapshot = [plans["safe"], plans["balanced"]] + ([plans["side"]] if plans["side"] else []) + [plans["bold"]]
    seen = set()
    for item in snapshot:
        path = item["path"]
        if path["id"] in seen:
            continue
        seen.add(path["id"])
        with st.container(border=True):
            st.markdown(f"**{path['name']}**")
            st.markdown(ai_pills(path), unsafe_allow_html=True)
            st.markdown(esc(f"**Human moat:** {path['human_moat']}"))
            st.markdown(esc(f"**3–5 year note:** {path['three_to_five_year_ai_note']}"))
            st.markdown(esc(f"**Defensive skills:** {', '.join(path['defensive_skills'])}  \n"
                            f"**AI tools to learn:** {', '.join(path['ai_tools_to_learn'])}"))

    # 7. Dream path analysis
    if r["dream"]:
        d = r["dream"]
        st.header("5 · Dream path analysis")
        st.caption(f"Your creative focus: {d['interest']}. The dream stays in the plan — it just gets economically tested.")
        t1, t2, t3 = st.tabs(["Direct dream path", "Passion + income path", "Entrepreneurial dream path"])
        with t1:
            dp = d["direct"]
            st.markdown(esc(
                f"Pursue **{dp['name']}** as the primary path. Typical incomes: {money(dp['income_range_cad']['entry'])} entry, "
                f"{money(dp['income_range_cad']['mid'])} mid. {dp['time_to_income']}"
            ))
            st.markdown(esc(f"**Training:** {dp['training_required']}"))
            st.markdown(f"**Self-promotion burden:** {dp['sales_requirement']} · **Income volatility risk:** {dp['risk_level']}")
            st.markdown(esc(f"**Honest note:** {dp['notes']}"))
            if not d["direct_covers_target"]:
                st.warning("On typical mid-level earnings this path does not fund your target lifestyle by itself. "
                           "Backup trigger: if it isn't covering half your costs after 12–18 months, move it to the side track.", icon="⚠️")
        with t2:
            ie = d["income_engine"]
            st.markdown(esc(
                f"Protect the dream by pairing it with a practical engine: **{ie['name']}** pays the bills "
                f"({money(ie['income_range_cad']['mid'])} mid-career) while **{d['interest'].lower()}** develops on protected "
                f"evenings/weekend blocks. This is usually the most durable version — the dream gets years to compound "
                f"without rent depending on it."
            ))
        with t3:
            bz = d["business"]
            st.markdown(esc(
                f"Build a business around the dream: **{bz['name']}** — lessons, services, production, events, content, "
                f"or digital products. Owner take-home ranges roughly {money(bz['income_range_cad']['entry'])} early to "
                f"{money(bz['income_range_cad']['mid'])} established."
            ))
            st.markdown(esc(f"**What it demands:** {bz['notes']}"))

    # 8. Entrepreneurship readiness
    if r["readiness"].get("assessed"):
        rd = r["readiness"]
        st.header("6 · Entrepreneurship readiness")
        st.markdown(f"**{rd['level']}** — you answered yes to {rd['yes_count']} of {rd['total']} readiness questions.")
        st.progress(rd["yes_count"] / rd["total"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(esc(
                f"**First offer to test:** {rd['first_offer']}.  \n"
                f"**First customers:** 10 direct conversations in your own network and one local channel "
                f"(community groups, industry meetups) — before any paid ads.  \n"
                f"**Recommended training:** structured business education (SAIT/Mount Royal continuing ed or equivalent) "
                f"covering sales, pricing, and bookkeeping basics."
            ))
        with c2:
            st.markdown(
                f"**Runway:** {rd['runway_months']:.1f} months of costs saved. Build toward 6 before going full-time.  \n"
                f"**Startup budget warning:** keep first-offer testing under a few hundred dollars — sell before you build.  \n"
                f"**Fallback plan:** the Safe plan above stays open; revisit at fixed checkpoints, not in a panic."
            )
        if rd["missing"]:
            st.caption("Gaps you flagged: " + " · ".join(rd["missing"]))
        st.markdown(
            "> Entrepreneurship can create independence, but it is not a shortcut. It requires sales, delivery, pricing, "
            "customer service, financial discipline, and resilience. Business education may reduce avoidable mistakes, "
            "but execution still matters."
        )

    # 9. Roadmap
    st.header("7 · Your two-year roadmap")
    st.caption("Built from your Balanced plan — the default recommendation.")
    rc1, rc2, rc3 = st.columns(3)
    for col, title, items in ((rc1, "Next 90 days", r["roadmap"]["r90"]),
                              (rc2, "Months 4–12", r["roadmap"]["y1"]),
                              (rc3, "Year 2", r["roadmap"]["y2"])):
        with col:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                for it in items:
                    st.markdown(esc(f"- {it}"))

    # 10. Hard truths
    if r["truths"]:
        st.header("8 · Hard truths")
        st.caption("Blunt on purpose. Constructive on purpose.")
        for truth in r["truths"]:
            st.markdown(esc(f"> {truth}"))

    # 11. Next 3 moves
    st.header("9 · Your next 3 moves")
    mv_cols = st.columns(3)
    for col, (title, body) in zip(mv_cols, r["moves"]):
        with col:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.markdown(esc(body))

    st.divider()
    st.info(DISCLAIMER, icon="ℹ️")
    c1, c2 = st.columns(2)
    if c1.button("← Adjust my answers"):
        go(5 if p.get("ent_shown") else 4)
    if c2.button("Start over"):
        st.session_state.clear()
        st.rerun()


# ------------------------------- Router -----------------------------------

STEP_NAMES = ["Welcome", "Your situation", "Future target", "Lifestyle tiers", "Work style", "Entrepreneurship", "Your plan"]


def main():
    st.set_page_config(page_title="LifePath Calculator", page_icon="🧭", layout="wide")
    step = st.session_state.setdefault("step", 0)
    p = p_state()

    with st.sidebar:
        st.markdown("### 🧭 LifePath Calculator")
        shown_step = min(step, 6)
        st.progress(min(shown_step / 6, 1.0), text=f"{STEP_NAMES[shown_step]}")
        C = costs()
        st.caption(
            f"Prototype · assumptions for {C['market']} updated {C['last_updated']}. "
            "All numbers editable in `data/*.json`."
        )
        if st.button("Start over", key="sidebar_restart"):
            st.session_state.clear()
            st.rerun()
        st.caption(DISCLAIMER)

    steps = {
        0: step_welcome,
        1: step_current,
        2: step_future,
        3: step_tiers,
        4: step_workstyle,
        5: step_ent,
        6: render_results,
    }
    steps.get(step, step_welcome)()


if __name__ == "__main__":
    main()
