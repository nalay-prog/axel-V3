import os
import re
from typing import Any, Dict, List, Optional


PORTFOLIO_SIM_ENGINE = "portfolio_simulation"
PORTFOLIO_SIM_VERSION = os.getenv("PORTFOLIO_SIM_VERSION", "v1")
PORTFOLIO_SIM_DEFAULT_GROSS_YIELD = float(os.getenv("PORTFOLIO_SIM_DEFAULT_GROSS_YIELD", "0.06"))
PORTFOLIO_SIM_DEFAULT_OCCUPANCY_RATE = float(os.getenv("PORTFOLIO_SIM_DEFAULT_OCCUPANCY_RATE", "0.92"))
PORTFOLIO_SIM_DEFAULT_OPERATING_COST_RATE = float(os.getenv("PORTFOLIO_SIM_DEFAULT_OPERATING_COST_RATE", "0.01"))
PORTFOLIO_SIM_PRUDENT_HAIRCUT_RATE = float(os.getenv("PORTFOLIO_SIM_PRUDENT_HAIRCUT_RATE", "0.15"))
PORTFOLIO_SIM_TAX_RATE_IR = float(os.getenv("PORTFOLIO_SIM_TAX_RATE_IR", "0.30"))
PORTFOLIO_SIM_TAX_RATE_IS = float(os.getenv("PORTFOLIO_SIM_TAX_RATE_IS", "0.25"))


def _clean(text: Optional[str]) -> str:
    return (text or "").strip()


def _normalize_ascii(text: Optional[str]) -> str:
    normalized = (text or "").lower().strip()
    normalized = normalized.replace("é", "e").replace("è", "e").replace("ê", "e")
    normalized = normalized.replace("à", "a").replace("â", "a").replace("î", "i")
    normalized = normalized.replace("ô", "o").replace("û", "u").replace("ù", "u")
    normalized = normalized.replace("ç", "c")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


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
    m = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _safe_int(raw: Any) -> Optional[int]:
    value = _safe_float(raw)
    if value is None:
        return None
    try:
        return int(round(value))
    except Exception:
        return None


def _rate_clamped(rate: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(rate)))


def _amount_from_text(question: str) -> Optional[float]:
    q = question or ""
    patterns = [
        r"(\d[\d\s.,]*)\s*(k|m)\b",
        r"(\d[\d\s.,]*)\s*(€|eur|euros?)",
        r"(?:montant|investissement|capital)\s*(?:de|:)?\s*(\d[\d\s.,]*)",
    ]
    for pattern in patterns:
        m = re.search(pattern, q, flags=re.IGNORECASE)
        if not m:
            continue
        raw = _safe_float(m.group(1))
        if raw is None:
            continue
        suffix = (m.group(2) if len(m.groups()) > 1 else "").lower()
        if suffix == "k":
            raw *= 1_000.0
        elif suffix == "m":
            raw *= 1_000_000.0
        if raw > 0:
            return raw
    return None


def _horizon_from_text(question: str) -> Optional[int]:
    q = question or ""
    m = re.search(r"(\d{1,2})\s*(ans|an|years?)", q, flags=re.IGNORECASE)
    if m:
        years = _safe_int(m.group(1))
        if years and years > 0:
            return years

    m2 = re.search(r"horizon\s*(?:de|:)?\s*(\d{1,2})", q, flags=re.IGNORECASE)
    if m2:
        years = _safe_int(m2.group(1))
        if years and years > 0:
            return years
    return None


def _tax_regime_from_text(question: str) -> Optional[str]:
    q = _normalize_ascii(question)
    if re.search(r"\bis\b", q) or "impot sur les societes" in q:
        return "IS"
    if re.search(r"\bir\b", q) or "impot sur le revenu" in q:
        return "IR"
    return None


def _is_simulation_requested(question: str, raw_input: Optional[Dict[str, Any]]) -> bool:
    if isinstance(raw_input, dict) and any(v not in (None, "") for v in raw_input.values()):
        return True
    q = _normalize_ascii(question)
    keywords = [
        "simulation",
        "simule",
        "simuler",
        "simulateur",
        "portefeuille",
        "cash flow",
        "cash-flow",
        "rendement net",
        "projection prudente",
    ]
    return any(k in q for k in keywords)


def normalize_simulation_input(
    question: str,
    raw_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = raw_input if isinstance(raw_input, dict) else {}
    requested = _is_simulation_requested(question, raw)

    amount_keys = ["amount", "montant", "amount_eur", "montant_eur", "capital", "invested_amount"]
    tax_keys = ["fiscality", "fiscalite", "tax_regime", "tax", "imposition"]
    horizon_keys = ["horizon", "horizon_years", "annees", "ans", "duree", "duration_years"]

    amount = None
    for key in amount_keys:
        if key in raw:
            amount = _safe_float(raw.get(key))
            if amount is not None and amount > 0:
                break
    if amount is None:
        amount = _amount_from_text(question)

    tax_regime = None
    for key in tax_keys:
        if key in raw:
            value = _normalize_ascii(str(raw.get(key)))
            if value in {"ir", "impot sur le revenu"}:
                tax_regime = "IR"
                break
            if value in {"is", "impot sur les societes"}:
                tax_regime = "IS"
                break
    if tax_regime is None:
        tax_regime = _tax_regime_from_text(question)

    horizon_years = None
    for key in horizon_keys:
        if key in raw:
            horizon_years = _safe_int(raw.get(key))
            if horizon_years is not None and horizon_years > 0:
                break
    if horizon_years is None:
        horizon_years = _horizon_from_text(question)

    missing_fields: List[str] = []
    if amount is None or amount <= 0:
        missing_fields.append("montant")
    if tax_regime not in {"IR", "IS"}:
        missing_fields.append("fiscalite")
    if horizon_years is None or horizon_years <= 0:
        missing_fields.append("horizon")

    return {
        "requested": requested,
        "is_complete": requested and not missing_fields,
        "missing_fields": missing_fields,
        "input": {
            "montant_eur": round(float(amount), 2) if amount is not None and amount > 0 else None,
            "fiscalite": tax_regime if tax_regime in {"IR", "IS"} else None,
            "horizon_years": int(horizon_years) if horizon_years is not None and horizon_years > 0 else None,
        },
    }


def _is_percent_like(value: Any, unit: Any) -> bool:
    blob = _normalize_ascii(f"{value or ''} {unit or ''}")
    return "%" in blob or "pct" in blob or "pourcent" in blob


def _to_rate(value: Optional[float], percent_like: bool = False) -> Optional[float]:
    if value is None:
        return None
    if percent_like:
        return value / 100.0
    if value > 1.5:
        return value / 100.0
    return value


def _infer_assumptions_from_kpi(kpi_sources: Optional[List[Any]] = None) -> Dict[str, Any]:
    gross_yield = _rate_clamped(PORTFOLIO_SIM_DEFAULT_GROSS_YIELD, 0.01, 0.2)
    occupancy = _rate_clamped(PORTFOLIO_SIM_DEFAULT_OCCUPANCY_RATE, 0.5, 1.0)
    source_hints = {
        "gross_yield_source": "default_model",
        "occupancy_source": "default_model",
    }

    for item in kpi_sources or []:
        if not isinstance(item, dict):
            continue
        metric = _normalize_ascii(
            " ".join(
                [
                    _clean(str(item.get("metric", ""))),
                    _clean(str(item.get("context", ""))),
                ]
            )
        )
        raw_value = _safe_float(f"{item.get('value', '')} {item.get('unit', '')}")
        if raw_value is None:
            continue
        percent_like = _is_percent_like(item.get("value"), item.get("unit"))
        source = _clean(str(item.get("source") or "non_renseigne"))
        date = _clean(str(item.get("date") or "non_renseigne"))
        source_label = f"{source} ({date})"

        if any(k in metric for k in ["taux distribution", "rendement", "td", "distribution"]):
            inferred = _to_rate(raw_value, percent_like=percent_like)
            if inferred is not None and 0.01 <= inferred <= 0.2:
                gross_yield = inferred
                source_hints["gross_yield_source"] = source_label
                continue

        if any(k in metric for k in ["tof", "occupation", "taux occupation"]):
            inferred = _to_rate(raw_value, percent_like=percent_like)
            if inferred is not None and 0.5 <= inferred <= 1.0:
                occupancy = inferred
                source_hints["occupancy_source"] = source_label

    return {
        "gross_yield_rate": round(gross_yield, 6),
        "occupancy_rate": round(occupancy, 6),
        "operating_cost_rate": round(_rate_clamped(PORTFOLIO_SIM_DEFAULT_OPERATING_COST_RATE, 0.0, 0.1), 6),
        "prudent_haircut_rate": round(_rate_clamped(PORTFOLIO_SIM_PRUDENT_HAIRCUT_RATE, 0.0, 0.5), 6),
        "tax_rates": {
            "IR": round(_rate_clamped(PORTFOLIO_SIM_TAX_RATE_IR, 0.0, 0.8), 6),
            "IS": round(_rate_clamped(PORTFOLIO_SIM_TAX_RATE_IS, 0.0, 0.8), 6),
        },
        **source_hints,
    }


def _compute_simulation(
    amount_eur: float,
    fiscalite: str,
    horizon_years: int,
    assumptions: Dict[str, Any],
) -> Dict[str, Any]:
    gross_yield_rate = float(assumptions.get("gross_yield_rate", PORTFOLIO_SIM_DEFAULT_GROSS_YIELD))
    occupancy_rate = float(assumptions.get("occupancy_rate", PORTFOLIO_SIM_DEFAULT_OCCUPANCY_RATE))
    operating_cost_rate = float(
        assumptions.get("operating_cost_rate", PORTFOLIO_SIM_DEFAULT_OPERATING_COST_RATE)
    )
    prudent_haircut_rate = float(
        assumptions.get("prudent_haircut_rate", PORTFOLIO_SIM_PRUDENT_HAIRCUT_RATE)
    )
    tax_rates = assumptions.get("tax_rates", {}) or {}
    tax_rate = float(tax_rates.get(fiscalite, PORTFOLIO_SIM_TAX_RATE_IR))

    gross_income = amount_eur * gross_yield_rate
    occupied_income = gross_income * occupancy_rate
    operating_cost = amount_eur * operating_cost_rate
    taxable_income = max(0.0, occupied_income - operating_cost)
    tax_amount = taxable_income * tax_rate
    net_cash_flow_annual = taxable_income - tax_amount
    net_cash_flow_monthly = net_cash_flow_annual / 12.0
    net_yield_after_tax_rate = (net_cash_flow_annual / amount_eur) if amount_eur > 0 else 0.0

    prudent_net_yield_rate = max(0.0, net_yield_after_tax_rate * (1.0 - prudent_haircut_rate))
    projected_capital_prudent = amount_eur * ((1.0 + prudent_net_yield_rate) ** max(1, horizon_years))
    projected_gain_prudent = projected_capital_prudent - amount_eur

    return {
        "cash_flow_estime": {
            "annuel_eur": round(net_cash_flow_annual, 2),
            "mensuel_eur": round(net_cash_flow_monthly, 2),
        },
        "rendement_net_apres_fiscalite": {
            "rate": round(net_yield_after_tax_rate, 6),
            "pct": round(net_yield_after_tax_rate * 100.0, 2),
        },
        "projection_prudente": {
            "horizon_years": int(horizon_years),
            "net_yield_rate": round(prudent_net_yield_rate, 6),
            "net_yield_pct": round(prudent_net_yield_rate * 100.0, 2),
            "capital_projete_eur": round(projected_capital_prudent, 2),
            "gain_projete_eur": round(projected_gain_prudent, 2),
        },
    }


def run_portfolio_simulation(
    question: str,
    raw_input: Optional[Dict[str, Any]] = None,
    kpi_sources: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_simulation_input(question=question, raw_input=raw_input)
    if not normalized.get("requested"):
        return {"requested": False}

    simulation: Dict[str, Any] = {
        "engine": PORTFOLIO_SIM_ENGINE,
        "version": PORTFOLIO_SIM_VERSION,
        "requested": True,
        "is_complete": bool(normalized.get("is_complete")),
        "input": normalized.get("input") or {},
        "missing_fields": normalized.get("missing_fields") or [],
    }

    if not simulation["is_complete"]:
        simulation["message"] = (
            "Inputs requis pour simulation portefeuille: montant, fiscalite (IR/IS), horizon (ans)."
        )
        return simulation

    input_payload = simulation["input"]
    amount = float(input_payload.get("montant_eur") or 0.0)
    fiscalite = str(input_payload.get("fiscalite") or "IR")
    horizon_years = int(input_payload.get("horizon_years") or 0)

    assumptions = _infer_assumptions_from_kpi(kpi_sources=kpi_sources)
    results = _compute_simulation(
        amount_eur=amount,
        fiscalite=fiscalite,
        horizon_years=horizon_years,
        assumptions=assumptions,
    )

    simulation["assumptions"] = assumptions
    simulation["results"] = results
    simulation["warning"] = (
        "Estimation non contractuelle. Les flux reels peuvent varier selon vacance, frais, fiscalite et marche."
    )
    return simulation
