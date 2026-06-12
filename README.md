# LifePath Calculator — prototype

An interview-style coaching tool: describe the life you want in 2/5/10 years, see what it probably
costs (Alberta, 2026-style assumptions, with per-city cost packs for Calgary, Edmonton, Red Deer,
Lethbridge, Medicine Hat, Fort McMurray, Grande Prairie, and rural Alberta), see the gross income
that supports it, then compare Safe / Balanced / Bold pathway plans — with AI exposure, disruption
risk, and AI leverage shown for every recommended career path.

The welcome page offers three deliberately different demo personas (hospitality worker in
Lethbridge, tech grad in Edmonton, working musician in Calgary) so no single story reads as the
app's default user.

**This is a planning prototype, not financial, tax, career, or legal advice.** All numbers are rough,
editable assumptions.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501. The welcome page has a **“Try a sample profile (demo)”** button that jumps
straight to a fully-populated results page.

## Files

| File | What it controls |
|---|---|
| `app.py` | Interview flow, budget engine, gross-up, pathway scoring, plan builder, UI |
| `data/cost_assumptions.json` | Every monthly cost tier + per-city housing tables and everyday-cost factors + misc buffer % |
| `data/tax_assumptions.json` | Tiered net-ratio gross-up model + uncertainty band |
| `data/career_paths.json` | 88 career/pathway cards across 8 categories (the heart of the tool) |
| `data/questionnaire_options.json` | Dropdown options, skill→category mappings, lifestyle-level derivations, readiness levels |

No database, no login, no admin panel, no external API calls — by design for v1. All data access in
`app.py` goes through four small loader functions (`costs()`, `tax_model()`, `options()`,
`career_paths()`), so swapping JSON for database-backed admin records later is a one-seam change.

## Editing the career data

`data/career_paths.json` is an array of records. Enumerated fields and their allowed values:

- `training_cost_level`, `risk_level`, `sales_requirement`, `ai_disruption_risk`: `Low | Medium | High`
- `business_potential`, `freelance_potential`, `ai_exposure_level`, `ai_leverage_potential`: `Low | Medium | High | Very High`
- `job_market_signal`: `Hot | Warm | Balanced | Cool | Cold` (Alberta ALIS-style)

Two fields were added beyond the original spec to make scoring possible:
`time_to_income_years {min,max}` (machine-readable timeframe fit) and `sales_requirement`
(sales-comfort fit). `tenant_insurance` and `health_personal` were similarly added to the cost file
so the budget covers the full category list.

## How the engine works (short version)

1. **Budget** — tier choices map to monthly line items; housing comes from the target city's table
   and food/utilities/lifestyle/health scale by the city's `everyday_factor`; +10% misc buffer;
   presented as a ±7% range to avoid false precision.
2. **Gross-up** — annual after-tax need → tiered net-ratio estimate (`tax_assumptions.json`) ± 8%.
3. **Scoring** — every path scored on income fit, timeframe, risk tolerance, study willingness,
   structure/autonomy, sales comfort, entrepreneurship/creative/AI interest, income urgency vs AI
   disruption, Alberta market signal, and skill/field alignment. Weights live in `score_path()`.
4. **Plans** — Safe = top low-risk stable path; Balanced = best practical income engine (+ a passion/
   business side track); Bold = top entrepreneurship/dream path with runway math and a fallback trigger.

## Known limitations

- Tax model is deliberately rough (no CPP/EI/credit detail, no self-employment distinction).
- City housing tables and everyday factors are directional mid-2026 estimates; "Outside Alberta"
  falls back to Calgary-baseline costs.
- Career incomes are Alberta-wide and the ALIS-style market signals are provincial — neither is
  city-specific, and both are editorial judgment for the prototype, not live data.
- AI ratings are directional, not predictions.
- Scoring weights are hand-tuned, not validated against outcomes.
- Session state only — refresh/restart loses answers; nothing is persisted or transmitted.

## Recommended next patch

1. Data refresh pass against live ALIS / Job Bank wage tables (scriptable; sources in the spec).
2. SQLite + minimal admin page (behind auth) replacing the JSON files — the loader seam is ready.
3. PDF/email export of the roadmap.
4. Other provinces: per-province tax tiers + cost packs are easy; the real work is per-province
   income ranges and market signals across the 88 career records (generatable the same way the
   Alberta set was, with the same disclaimers).
5. Copy polish from a handful of real user walkthroughs.

## Deploying (later)

Streamlit is a long-running process that needs websocket pass-through, so production hosting wants a
reverse proxy (nginx with `Upgrade`/`Connection` headers) in front of `streamlit run` under systemd,
or a container. Stage on a subdomain first. Note v1 has no authentication by design — it's meant to be
public — but put basic rate limiting at the proxy if the domain gets traffic.
