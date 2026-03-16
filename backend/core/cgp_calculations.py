from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional


CALC_ENGINE = "cgp_calculations"
CALC_VERSION = "v1"

CALC_TAX = "TAX"
CALC_NET = "NET"
CALC_PROJECTION = "PROJECTION"
CALC_ALLOCATION = "ALLOCATION"

SOCIAL_CONTRIB_RATE_FR = 0.172

ALLOCATION_PROFILE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "defensif": {
        "cash": 0.10,
        "oblig": 0.45,
        "immo": 0.30,
        "actions": 0.15,
    },
    "equilibre": {
        "cash": 0.07,
        "oblig": 0.30,
        "immo": 0.33,
        "actions": 0.30,
    },
    "offensif": {
        "cash": 0.05,
        "oblig": 0.15,
        "immo": 0.25,
        "actions": 0.55,
    },
}


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _normalize_ascii(text: Optional[str]) -> str:
    raw = unicodedata.normalize("NFKD", text or "")
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(k in text for k in keywords)


def _safe_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = _clean(str(raw))
    if not text:
        return None

    compact = text.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    normalized = compact.replace(",", ".")
    normalized = re.sub(r"(?<=\d)\.(?=\d{3}(\D|$))", "", normalized)
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _to_rate(raw: Any) -> Optional[float]:
    value = _safe_float(raw)
    if value is None:
        return None
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, float(value)))


def _safe_int(raw: Any) -> Optional[int]:
    value = _safe_float(raw)
    if value is None:
        return None
    try:
        out = int(round(value))
    except Exception:
        return None
    return out if out > 0 else None


def _extract_money_from_text(question: str) -> Optional[float]:
    q = question or ""
    patterns = [
        r"(\d[\d\s.,]*)\s*(k|m)\b",
        r"(\d[\d\s.,]*)\s*(€|eur|euros?)",
        r"(?:montant|capital|investissement|c0|c_0)\s*(?:de|:)?\s*(\d[\d\s.,]*)",
        r"\b(\d{4,})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.IGNORECASE)
        if not m:
            continue
        amount = _safe_float(m.group(1))
        if amount is None:
            continue
        suffix = ""
        if len(m.groups()) >= 2 and m.group(2):
            suffix = str(m.group(2)).lower()
        if suffix == "k":
            amount *= 1_000.0
        elif suffix == "m":
            amount *= 1_000_000.0
        if amount > 0:
            return float(amount)
    return None


def _extract_years_from_text(question: str) -> Optional[int]:
    q = question or ""
    match = re.search(r"(\d{1,2})\s*(ans?|an|years?)", q, flags=re.IGNORECASE)
    if match:
        return _safe_int(match.group(1))

    match2 = re.search(r"horizon\s*(?:de|:)?\s*(\d{1,2})", q, flags=re.IGNORECASE)
    if match2:
        return _safe_int(match2.group(1))
    return None


def _extract_rate_after_keywords(question: str, keywords: List[str]) -> Optional[float]:
    q = question or ""
    for keyword in keywords:
        pattern = rf"(?:{re.escape(keyword)})[^0-9]{{0,25}}(\d+(?:[.,]\d+)?)\s*%?"
        match = re.search(pattern, q, flags=re.IGNORECASE)
        if not match:
            continue
        return _to_rate(match.group(1))
    return None


def _extract_profile_from_text(question: str) -> Optional[str]:
    q = _normalize_ascii(question)
    if "defensif" in q or "prudent" in q or "conservateur" in q:
        return "defensif"
    if "offensif" in q or "dynamique" in q or "agressif" in q:
        return "offensif"
    if "equilibre" in q or "equilibree" in q or "modere" in q:
        return "equilibre"
    return None


def _input_get(inputs: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in inputs and inputs.get(key) not in (None, ""):
            return inputs.get(key)
    return None


def _first_sql_kpi_rate(sources_by_layer: Dict[str, Any], metric_keywords: List[str]) -> Optional[float]:
    items = (sources_by_layer or {}).get("sql_kpi") or []
    if not isinstance(items, list):
        return None

    metric_keywords_norm = [_normalize_ascii(k) for k in metric_keywords]
    for item in items:
        if not isinstance(item, dict):
            continue

        metric_blob = " ".join(
            [
                _clean(str(item.get("metric", ""))),
                _clean(str(item.get("context", ""))),
                _clean(str(item.get("label", ""))),
                _clean(str(item.get("indicator", ""))),
            ]
        )
        metric_norm = _normalize_ascii(metric_blob)
        if not any(k in metric_norm for k in metric_keywords_norm):
            continue

        value = _safe_float(f"{item.get('value', '')} {item.get('unit', '')}")
        if value is None:
            continue

        unit_blob = _normalize_ascii(f"{item.get('unit', '')} {item.get('value', '')}")
        if "%" in unit_blob or "pct" in unit_blob or "pourcent" in unit_blob or value > 1.0:
            rate = _to_rate(value)
        else:
            rate = float(value)
        if rate is not None:
            return max(0.0, min(1.0, rate))
    return None


def _fmt_money(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ")


def _fmt_rate(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def detect_calc_request(question: str) -> Dict[str, Any]:
    q = _normalize_ascii(question)
    calc_types: List[str] = []

    tax_keywords = [
        "fiscalite",
        "impot",
        "ir",
        "is",
        "tmi",
        "prelevement social",
        "prelevements sociaux",
        "tax",
    ]
    net_keywords = [
        "rendement net",
        "net apres fiscal",
        "net apres fiscalite",
        "yield net",
        "net yield",
    ]
    projection_keywords = [
        "projection",
        "projeter",
        "capital final",
        "capitalisation",
        "horizon",
        "compound",
        "(1+r)^n",
    ]
    allocation_keywords = [
        "allocation",
        "repartition",
        "portefeuille",
        "profil",
        "poids",
    ]
    simulation_keywords = [
        "simulation",
        "simule",
        "simuler",
        "simulateur",
    ]

    if _contains_any(q, tax_keywords):
        calc_types.append(CALC_TAX)
    if _contains_any(q, net_keywords):
        calc_types.append(CALC_NET)
    if _contains_any(q, projection_keywords):
        calc_types.append(CALC_PROJECTION)
    if _contains_any(q, allocation_keywords):
        calc_types.append(CALC_ALLOCATION)

    if _contains_any(q, simulation_keywords):
        calc_types.extend([CALC_NET, CALC_PROJECTION, CALC_ALLOCATION])

    calc_types = sorted(set(calc_types))
    return {
        "needs_calc": len(calc_types) > 0,
        "calc_types": calc_types,
    }


def _compute_tax_scpi_ir_fr(
    revenu_brut: Optional[float],
    tmi_rate: Optional[float],
    country: str,
) -> Dict[str, Any]:
    warnings: List[str] = []
    debug: Dict[str, Any] = {
        "formula": "IR = revenu_brut * TMI ; PS = revenu_brut * 17.2% ; Net = revenu_brut - IR - PS",
        "country": country,
    }

    country_norm = _normalize_ascii(country)
    if country_norm not in {"fr", "fra", "france"}:
        warnings.append("tax_country_not_supported_v1")
        return {
            "ok": False,
            "result": None,
            "warnings": warnings,
            "proof": "TAX non calculee: pays non supporte en v1.",
            "debug": debug,
        }

    missing_fields: List[str] = []
    if revenu_brut is None:
        missing_fields.append("revenu_brut")
        warnings.append("tax_missing_revenu")
    if tmi_rate is None:
        missing_fields.append("tmi_rate")
        warnings.append("tax_missing_tmi")

    if missing_fields:
        debug["missing_fields"] = missing_fields
        return {
            "ok": False,
            "result": None,
            "warnings": warnings,
            "proof": "TAX non calculee: inputs manquants.",
            "debug": debug,
        }

    ir_amount = float(revenu_brut) * float(tmi_rate)
    ps_amount = float(revenu_brut) * SOCIAL_CONTRIB_RATE_FR
    net_amount = float(revenu_brut) - ir_amount - ps_amount

    result = {
        "revenu_brut": round(float(revenu_brut), 2),
        "tmi_rate": round(float(tmi_rate), 6),
        "social_contrib_rate": round(SOCIAL_CONTRIB_RATE_FR, 6),
        "ir_amount": round(ir_amount, 2),
        "social_contrib_amount": round(ps_amount, 2),
        "net_amount": round(net_amount, 2),
    }
    proof = (
        "TAX FR: revenu_brut="
        + _fmt_money(result["revenu_brut"])
        + " ; TMI="
        + _fmt_rate(result["tmi_rate"])
        + " ; PS=17.20% ; IR="
        + _fmt_money(result["ir_amount"])
        + " ; PS="
        + _fmt_money(result["social_contrib_amount"])
        + " ; Net="
        + _fmt_money(result["net_amount"])
    )

    debug["computed"] = True
    return {
        "ok": True,
        "result": result,
        "warnings": warnings,
        "proof": proof,
        "debug": debug,
    }


def _compute_net_after_tax(
    gross_yield_rate: Optional[float],
    tmi_rate: Optional[float],
    social_contrib_rate: Optional[float],
    operating_cost_rate: Optional[float],
    prudent_haircut_rate: Optional[float],
) -> Dict[str, Any]:
    warnings: List[str] = []
    missing_fields: List[str] = []

    if gross_yield_rate is None:
        missing_fields.append("gross_yield_rate")
    if tmi_rate is None:
        missing_fields.append("tmi_rate")
    if social_contrib_rate is None:
        missing_fields.append("social_contrib_rate")
    if operating_cost_rate is None:
        missing_fields.append("operating_cost_rate")
    if prudent_haircut_rate is None:
        missing_fields.append("prudent_haircut_rate")

    if missing_fields:
        for field in missing_fields:
            warnings.append(f"net_missing_{field}")
        return {
            "ok": False,
            "result": None,
            "warnings": warnings,
            "proof": "NET non calcule: inputs manquants.",
            "debug": {
                "formula": (
                    "income_after_costs = gross * (1 - operating_cost_rate) ; "
                    "net_after_tax = income_after_costs * (1 - (TMI + PS)) ; "
                    "net_prudent = net_after_tax * (1 - prudent_haircut_rate)"
                ),
                "missing_fields": missing_fields,
            },
        }

    gross = float(gross_yield_rate)
    tmi = float(tmi_rate)
    ps = float(social_contrib_rate)
    op = float(operating_cost_rate)
    hair = float(prudent_haircut_rate)

    income_after_costs = gross * (1.0 - op)
    net_after_tax = income_after_costs * (1.0 - (tmi + ps))
    net_prudent = net_after_tax * (1.0 - hair)

    result = {
        "gross_yield_rate": round(gross, 6),
        "tmi_rate": round(tmi, 6),
        "social_contrib_rate": round(ps, 6),
        "operating_cost_rate": round(op, 6),
        "prudent_haircut_rate": round(hair, 6),
        "income_after_costs_rate": round(income_after_costs, 6),
        "net_after_tax_rate": round(net_after_tax, 6),
        "net_prudent_rate": round(net_prudent, 6),
    }

    proof = (
        "NET: gross="
        + _fmt_rate(result["gross_yield_rate"])
        + " ; costs="
        + _fmt_rate(result["operating_cost_rate"])
        + " ; TMI="
        + _fmt_rate(result["tmi_rate"])
        + " ; PS="
        + _fmt_rate(result["social_contrib_rate"])
        + " ; haircut="
        + _fmt_rate(result["prudent_haircut_rate"])
        + " => net_prudent="
        + _fmt_rate(result["net_prudent_rate"])
    )

    return {
        "ok": True,
        "result": result,
        "warnings": warnings,
        "proof": proof,
        "debug": {
            "formula": (
                "income_after_costs = gross * (1 - operating_cost_rate) ; "
                "net_after_tax = income_after_costs * (1 - (TMI + PS)) ; "
                "net_prudent = net_after_tax * (1 - prudent_haircut_rate)"
            ),
            "computed": True,
        },
    }


def _compute_projection_capital(
    capital_initial: Optional[float],
    taux_net_annuel: Optional[float],
    horizon_years: Optional[int],
) -> Dict[str, Any]:
    warnings: List[str] = []
    missing_fields: List[str] = []

    if capital_initial is None:
        missing_fields.append("capital_initial")
    if taux_net_annuel is None:
        missing_fields.append("taux_net_annuel")
    if horizon_years is None:
        missing_fields.append("horizon_years")

    if missing_fields:
        for field in missing_fields:
            warnings.append(f"projection_missing_{field}")
        return {
            "ok": False,
            "result": None,
            "warnings": warnings,
            "proof": "PROJECTION non calculee: inputs manquants.",
            "debug": {
                "formula": "C_final = C0 * (1 + r)^n ; Gain = C_final - C0",
                "missing_fields": missing_fields,
            },
        }

    c0 = float(capital_initial)
    r = float(taux_net_annuel)
    n = int(horizon_years)

    c_final = c0 * ((1.0 + r) ** n)
    gain = c_final - c0

    result = {
        "capital_initial": round(c0, 2),
        "taux_net_annuel": round(r, 6),
        "horizon_years": n,
        "capital_final": round(c_final, 2),
        "gain": round(gain, 2),
    }

    proof = (
        "PROJECTION: C0="
        + _fmt_money(result["capital_initial"])
        + " ; r="
        + _fmt_rate(result["taux_net_annuel"])
        + " ; n="
        + str(result["horizon_years"])
        + " => C_final="
        + _fmt_money(result["capital_final"])
        + " ; Gain="
        + _fmt_money(result["gain"])
    )

    return {
        "ok": True,
        "result": result,
        "warnings": warnings,
        "proof": proof,
        "debug": {
            "formula": "C_final = C0 * (1 + r)^n ; Gain = C_final - C0",
            "computed": True,
        },
    }


def _compute_allocation(
    profile_scoring: Dict[str, Any],
    question: str,
    user_inputs: Dict[str, Any],
) -> Dict[str, Any]:
    warnings: List[str] = []

    profile = _clean(str(profile_scoring.get("selected_profile") or ""))
    if profile not in ALLOCATION_PROFILE_WEIGHTS:
        profile_from_question = _extract_profile_from_text(question)
        if profile_from_question in ALLOCATION_PROFILE_WEIGHTS:
            profile = profile_from_question
            warnings.append("allocation_profile_from_question")
        else:
            profile = "equilibre"
            warnings.append("allocation_profile_missing_default_equilibre")

    weights = ALLOCATION_PROFILE_WEIGHTS[profile]
    result: Dict[str, Any] = {
        "selected_profile": profile,
        "weights": {k: round(v, 6) for k, v in weights.items()},
    }

    portfolio_amount = _safe_float(
        _input_get(
            user_inputs,
            [
                "capital_total",
                "portfolio_amount",
                "capital_initial",
                "capital",
                "montant",
                "amount",
                "montant_eur",
            ],
        )
    )
    if portfolio_amount is None:
        portfolio_amount = _extract_money_from_text(question)

    if portfolio_amount is not None and portfolio_amount > 0:
        result["portfolio_amount"] = round(portfolio_amount, 2)
        result["allocation_amounts"] = {
            asset: round(portfolio_amount * weight, 2)
            for asset, weight in weights.items()
        }

    proof = (
        "ALLOCATION: profile="
        + profile
        + " ; cash="
        + _fmt_rate(weights["cash"])
        + " ; oblig="
        + _fmt_rate(weights["oblig"])
        + " ; immo="
        + _fmt_rate(weights["immo"])
        + " ; actions="
        + _fmt_rate(weights["actions"])
    )

    if "allocation_amounts" in result:
        proof += (
            " ; montants: cash="
            + _fmt_money(result["allocation_amounts"]["cash"])
            + " ; oblig="
            + _fmt_money(result["allocation_amounts"]["oblig"])
            + " ; immo="
            + _fmt_money(result["allocation_amounts"]["immo"])
            + " ; actions="
            + _fmt_money(result["allocation_amounts"]["actions"])
        )

    return {
        "ok": True,
        "result": result,
        "warnings": warnings,
        "proof": proof,
        "debug": {
            "formula": "allocation weights by deterministic profile",
            "computed": True,
        },
    }


def _effective_calc_types(question: str, user_inputs: Dict[str, Any]) -> List[str]:
    detected = detect_calc_request(question)
    calc_types = list(detected.get("calc_types") or [])

    override_types = user_inputs.get("calc_types") if isinstance(user_inputs.get("calc_types"), list) else []
    for calc_type in override_types:
        value = _clean(str(calc_type)).upper()
        if value in {CALC_TAX, CALC_NET, CALC_PROJECTION, CALC_ALLOCATION}:
            calc_types.append(value)

    # Infer from explicit user inputs if question is short.
    if not calc_types:
        if any(k in user_inputs for k in ["revenu_brut", "tmi", "tmi_rate", "tax_country", "pays", "country"]):
            calc_types.append(CALC_TAX)
        if any(
            k in user_inputs
            for k in [
                "gross_yield_rate",
                "social_contrib_rate",
                "operating_cost_rate",
                "prudent_haircut_rate",
            ]
        ):
            calc_types.append(CALC_NET)
        if any(k in user_inputs for k in ["capital_initial", "horizon", "horizon_years", "taux_net_annuel", "net_annual_rate"]):
            calc_types.append(CALC_PROJECTION)
        if any(k in user_inputs for k in ["profile", "selected_profile", "portfolio_amount", "capital_total"]):
            calc_types.append(CALC_ALLOCATION)

    return sorted(set(calc_types))


def run_cgp_calculations(
    question: str,
    sources_by_layer: dict,
    profile_scoring: dict,
    user_inputs: dict | None = None,
) -> Dict[str, Any]:
    inputs: Dict[str, Any] = user_inputs if isinstance(user_inputs, dict) else {}
    detected = detect_calc_request(question)
    calc_types = _effective_calc_types(question, inputs)

    calc_results: Dict[str, Any] = {}
    calc_warnings: List[str] = []
    proof_lines: List[str] = []

    calc_debug: Dict[str, Any] = {
        "engine": CALC_ENGINE,
        "version": CALC_VERSION,
        "detected": detected,
        "calc_types": calc_types,
        "inputs_used": {},
        "steps": {},
        "sources_used": {
            "sql_kpi_count": len((sources_by_layer or {}).get("sql_kpi") or []),
            "rag_market_count": len((sources_by_layer or {}).get("rag_market") or []),
            "rag_darwin_count": len((sources_by_layer or {}).get("rag_darwin") or []),
        },
    }

    if CALC_TAX in calc_types:
        revenu_brut = _safe_float(
            _input_get(
                inputs,
                [
                    "revenu_brut",
                    "revenu",
                    "gross_income",
                    "income",
                    "revenu_brut_annuel",
                ],
            )
        )
        if revenu_brut is None:
            revenu_brut = _extract_money_from_text(question)

        tmi_rate = _to_rate(_input_get(inputs, ["tmi_rate", "tmi", "tmi_percent"]))
        if tmi_rate is None:
            tmi_rate = _extract_rate_after_keywords(question, ["tmi", "tranche marginale", "ir"])

        country = _clean(
            str(
                _input_get(inputs, ["country", "pays", "tax_country"]) or "FR"
            )
        )

        calc_debug["inputs_used"][CALC_TAX] = {
            "revenu_brut": revenu_brut,
            "tmi_rate": tmi_rate,
            "country": country,
        }

        tax_step = _compute_tax_scpi_ir_fr(revenu_brut=revenu_brut, tmi_rate=tmi_rate, country=country)
        calc_debug["steps"][CALC_TAX] = tax_step.get("debug", {})
        calc_warnings.extend(tax_step.get("warnings") or [])
        if tax_step.get("ok"):
            calc_results[CALC_TAX] = tax_step.get("result")
            proof_lines.append(str(tax_step.get("proof") or ""))

    net_result_for_projection_rate: Optional[float] = None
    if CALC_NET in calc_types:
        gross_yield_rate = _to_rate(_input_get(inputs, ["gross_yield_rate", "gross_rate", "yield_rate", "gross_yield"]))
        if gross_yield_rate is None:
            gross_yield_rate = _extract_rate_after_keywords(question, ["gross", "brut", "rendement brut", "gross_yield"])
        if gross_yield_rate is None:
            gross_yield_rate = _first_sql_kpi_rate(sources_by_layer, ["taux de distribution", "rendement", "td"])

        tmi_rate = _to_rate(_input_get(inputs, ["tmi_rate", "tmi", "tmi_percent"]))
        if tmi_rate is None:
            tmi_rate = _extract_rate_after_keywords(question, ["tmi", "tranche marginale", "ir"])

        social_contrib_rate = _to_rate(_input_get(inputs, ["social_contrib_rate", "ps_rate", "prelevements_sociaux_rate"]))
        if social_contrib_rate is None:
            social_contrib_rate = _extract_rate_after_keywords(question, ["ps", "prelevements sociaux", "social"])

        operating_cost_rate = _to_rate(_input_get(inputs, ["operating_cost_rate", "cost_rate", "frais_exploitation_rate", "costs_rate"]))
        if operating_cost_rate is None:
            operating_cost_rate = _extract_rate_after_keywords(question, ["operating", "cost", "frais", "cout", "cout"])

        prudent_haircut_rate = _to_rate(_input_get(inputs, ["prudent_haircut_rate", "haircut_rate", "decote_prudente_rate", "haircut"]))
        if prudent_haircut_rate is None:
            prudent_haircut_rate = _extract_rate_after_keywords(question, ["haircut", "decote", "prudente", "prudent"])

        calc_debug["inputs_used"][CALC_NET] = {
            "gross_yield_rate": gross_yield_rate,
            "tmi_rate": tmi_rate,
            "social_contrib_rate": social_contrib_rate,
            "operating_cost_rate": operating_cost_rate,
            "prudent_haircut_rate": prudent_haircut_rate,
        }

        net_step = _compute_net_after_tax(
            gross_yield_rate=gross_yield_rate,
            tmi_rate=tmi_rate,
            social_contrib_rate=social_contrib_rate,
            operating_cost_rate=operating_cost_rate,
            prudent_haircut_rate=prudent_haircut_rate,
        )
        calc_debug["steps"][CALC_NET] = net_step.get("debug", {})
        calc_warnings.extend(net_step.get("warnings") or [])
        if net_step.get("ok"):
            calc_results[CALC_NET] = net_step.get("result")
            proof_lines.append(str(net_step.get("proof") or ""))
            net_result_for_projection_rate = (net_step.get("result") or {}).get("net_prudent_rate")

    if CALC_PROJECTION in calc_types:
        capital_initial = _safe_float(
            _input_get(
                inputs,
                [
                    "capital_initial",
                    "capital",
                    "montant",
                    "amount",
                    "c0",
                    "montant_eur",
                ],
            )
        )
        if capital_initial is None:
            capital_initial = _extract_money_from_text(question)

        taux_net_annuel = _to_rate(_input_get(inputs, ["taux_net_annuel", "net_annual_rate", "net_rate", "r"]))
        if taux_net_annuel is None:
            taux_net_annuel = _extract_rate_after_keywords(question, ["taux net", "rendement net", "net rate", "r"])
        if taux_net_annuel is None and net_result_for_projection_rate is not None:
            taux_net_annuel = float(net_result_for_projection_rate)
            calc_warnings.append("projection_rate_from_net_calc")

        horizon_years = _safe_int(_input_get(inputs, ["horizon_years", "horizon", "annees", "years", "n"]))
        if horizon_years is None:
            horizon_years = _extract_years_from_text(question)

        calc_debug["inputs_used"][CALC_PROJECTION] = {
            "capital_initial": capital_initial,
            "taux_net_annuel": taux_net_annuel,
            "horizon_years": horizon_years,
        }

        projection_step = _compute_projection_capital(
            capital_initial=capital_initial,
            taux_net_annuel=taux_net_annuel,
            horizon_years=horizon_years,
        )
        calc_debug["steps"][CALC_PROJECTION] = projection_step.get("debug", {})
        calc_warnings.extend(projection_step.get("warnings") or [])
        if projection_step.get("ok"):
            calc_results[CALC_PROJECTION] = projection_step.get("result")
            proof_lines.append(str(projection_step.get("proof") or ""))

    if CALC_ALLOCATION in calc_types:
        allocation_step = _compute_allocation(
            profile_scoring=profile_scoring if isinstance(profile_scoring, dict) else {},
            question=question,
            user_inputs=inputs,
        )
        calc_debug["inputs_used"][CALC_ALLOCATION] = {
            "selected_profile_from_scoring": (profile_scoring or {}).get("selected_profile") if isinstance(profile_scoring, dict) else None,
            "portfolio_amount": _input_get(inputs, ["capital_total", "portfolio_amount", "capital", "amount", "montant"]),
        }
        calc_debug["steps"][CALC_ALLOCATION] = allocation_step.get("debug", {})
        calc_warnings.extend(allocation_step.get("warnings") or [])
        if allocation_step.get("ok"):
            calc_results[CALC_ALLOCATION] = allocation_step.get("result")
            proof_lines.append(str(allocation_step.get("proof") or ""))

    if not calc_results:
        if not calc_types:
            calc_warnings.append("no_calc_requested")
        else:
            calc_warnings.append("calc_not_executed_missing_inputs")

    calc_warnings = sorted(set([_clean(str(w)) for w in calc_warnings if _clean(str(w))]))
    calc_proof_text = "\n".join([line for line in proof_lines if _clean(line)]).strip()
    if not calc_proof_text:
        calc_proof_text = "No deterministic calculation executed."

    calc_debug["computed_types"] = sorted(calc_results.keys())
    calc_debug["warning_count"] = len(calc_warnings)
    calc_debug["proof_lines_count"] = len([line for line in proof_lines if _clean(line)])

    return {
        "calc_results": calc_results,
        "calc_warnings": calc_warnings,
        "calc_proof_text": calc_proof_text,
        "calc_debug": calc_debug,
    }


__all__ = [
    "detect_calc_request",
    "run_cgp_calculations",
    "CALC_ENGINE",
    "CALC_VERSION",
    "CALC_TAX",
    "CALC_NET",
    "CALC_PROJECTION",
    "CALC_ALLOCATION",
]
