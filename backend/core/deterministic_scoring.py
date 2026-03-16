import copy
import re
from typing import Any, Dict, List, Optional, Tuple


PROFILE_DEFENSIF = "defensif"
PROFILE_EQUILIBRE = "equilibre"
PROFILE_OFFENSIF = "offensif"

DEFAULT_SCORING_VERSION = "1.2"

# Alias rétrocompatibles: les anciens identifiants continuent de fonctionner.
SCORING_VERSION_ALIASES: Dict[str, str] = {
    "v1": "1.0",
    "v1.0": "1.0",
    "1": "1.0",
    "1.0": "1.0",
    "v1.1": "1.1",
    "1.1": "1.1",
    "v1.2": "1.2",
    "1.2": "1.2",
    "v2": "1.2",
    "v2.0": "1.2",
    "2": "1.2",
    "2.0": "1.2",
    "latest": "1.2",
}


BASE_FEATURE_RULES: Dict[str, Dict[str, Any]] = {
    "capital_preservation": {
        "keywords": [
            "securiser",
            "securite",
            "proteger le capital",
            "preserver",
            "sans risque",
            "prudent",
        ],
        "per_hit": 0.35,
        "max": 1.0,
    },
    "liquidity_need": {
        "keywords": [
            "liquidite",
            "disponible rapidement",
            "retrait rapide",
            "court terme",
            "besoin de cash",
        ],
        "per_hit": 0.35,
        "max": 1.0,
    },
    "income_need": {
        "keywords": [
            "revenus",
            "rente",
            "cashflow",
            "distribution",
            "rendement regulier",
        ],
        "per_hit": 0.3,
        "max": 1.0,
    },
    "growth_need": {
        "keywords": [
            "croissance",
            "valorisation",
            "performance",
            "dynamique",
            "offensif",
            "agressif",
            "maximiser",
        ],
        "per_hit": 0.3,
        "max": 1.0,
    },
    "risk_low": {
        "keywords": [
            "faible risque",
            "risque faible",
            "aversion au risque",
            "prudent",
            "conservateur",
            "defensif",
        ],
        "per_hit": 0.4,
        "max": 1.0,
    },
    "risk_high": {
        "keywords": [
            "risque eleve",
            "volatilite",
            "tolerance au risque",
            "offensif",
            "agressif",
            "accepte les pertes",
        ],
        "per_hit": 0.4,
        "max": 1.0,
    },
    "declared_defensif": {
        "keywords": ["defensif", "prudent", "conservateur"],
        "per_hit": 0.5,
        "max": 1.0,
    },
    "declared_equilibre": {
        "keywords": ["equilibre", "equilibree", "modere", "moderee"],
        "per_hit": 0.6,
        "max": 1.0,
    },
    "declared_offensif": {
        "keywords": ["offensif", "dynamique", "agressif"],
        "per_hit": 0.5,
        "max": 1.0,
    },
    "horizon_short": {"keywords": ["court terme"], "per_hit": 0.8, "max": 1.0},
    "horizon_medium": {"keywords": ["moyen terme"], "per_hit": 0.8, "max": 1.0},
    "horizon_long": {"keywords": ["long terme"], "per_hit": 0.8, "max": 1.0},
    # Signal marché immobilier/SCPI: une bonne occupation (TOF) soutient le profil prudent/équilibré.
    "tof_quality": {
        "keywords": [
            "tof",
            "taux occupation financier",
            "taux d occupation financier",
            "occupation financiere",
            "occupation locative",
            "vacance faible",
        ],
        "per_hit": 0.35,
        "max": 1.0,
    },
}


BASE_PROFILE_WEIGHTS: Dict[str, Dict[str, float]] = {
    PROFILE_DEFENSIF: {
        "capital_preservation": 0.25,
        "liquidity_need": 0.2,
        "income_need": 0.15,
        "risk_low": 0.2,
        "horizon_short": 0.1,
        "declared_defensif": 0.15,
        "risk_high": -0.2,
        "growth_need": -0.1,
        "horizon_long": -0.05,
    },
    PROFILE_EQUILIBRE: {
        "horizon_medium": 0.22,
        "declared_equilibre": 0.2,
        "growth_need": 0.15,
        "income_need": 0.14,
        "risk_low": 0.08,
        "risk_high": 0.08,
        "capital_preservation": 0.1,
        "liquidity_need": 0.05,
        "horizon_long": 0.06,
        "horizon_short": 0.06,
    },
    PROFILE_OFFENSIF: {
        "growth_need": 0.28,
        "risk_high": 0.25,
        "horizon_long": 0.2,
        "declared_offensif": 0.14,
        "horizon_medium": 0.06,
        "income_need": 0.04,
        "risk_low": -0.18,
        "capital_preservation": -0.12,
        "liquidity_need": -0.08,
        "horizon_short": -0.1,
    },
}

BASE_PROFILE_BIAS: Dict[str, float] = {
    PROFILE_DEFENSIF: 0.03,
    PROFILE_EQUILIBRE: 0.05,
    PROFILE_OFFENSIF: 0.03,
}


def _weights_with_tof(defensif: float, equilibre: float, offensif: float) -> Dict[str, Dict[str, float]]:
    weights = copy.deepcopy(BASE_PROFILE_WEIGHTS)
    weights[PROFILE_DEFENSIF]["tof_quality"] = float(defensif)
    weights[PROFILE_EQUILIBRE]["tof_quality"] = float(equilibre)
    weights[PROFILE_OFFENSIF]["tof_quality"] = float(offensif)
    return weights


SCORING_CONFIGS: Dict[str, Dict[str, Any]] = {
    "1.0": {
        "version_metadata": {
            "label": "1.0",
            "released_at": "2026-02-19",
            "change_note": "Version initiale deterministic_profile_scoring.",
            "changes": [
                "Base profils defensif/equilibre/offensif",
                "Feature engineering texte client + historique",
                "Normalisation des scores sur 100",
            ],
        },
        "feature_rules": copy.deepcopy(BASE_FEATURE_RULES),
        "profile_weights": _weights_with_tof(defensif=0.04, equilibre=0.05, offensif=-0.04),
        "profile_bias": copy.deepcopy(BASE_PROFILE_BIAS),
    },
    "1.1": {
        "extends": "1.0",
        "version_metadata": {
            "label": "1.1",
            "released_at": "2026-02-19",
            "change_note": "Calibration intermediaire: poids TOF renforces pour stabiliser les profils prudents.",
            "changes": [
                "Ajustement poids TOF: defensif 0.04->0.08",
                "Ajustement poids TOF: equilibre 0.05->0.10",
                "Ajustement poids TOF: offensif -0.04->-0.06",
            ],
        },
        "profile_weights": {
            PROFILE_DEFENSIF: {"tof_quality": 0.08},
            PROFILE_EQUILIBRE: {"tof_quality": 0.10},
            PROFILE_OFFENSIF: {"tof_quality": -0.06},
        },
    },
    "1.2": {
        "extends": "1.1",
        "version_metadata": {
            "label": "1.2",
            "released_at": "2026-02-19",
            "change_note": "Scoring version 1.2, ponderation modifiee sur le TOF.",
            "changes": [
                "Ajustement poids TOF: defensif 0.08->0.12",
                "Ajustement poids TOF: equilibre 0.10->0.14",
                "Ajustement poids TOF: offensif -0.06->-0.10",
            ],
        },
        "profile_weights": {
            PROFILE_DEFENSIF: {"tof_quality": 0.12},
            PROFILE_EQUILIBRE: {"tof_quality": 0.14},
            PROFILE_OFFENSIF: {"tof_quality": -0.10},
        },
    },
}


PROFILE_LABELS = {
    PROFILE_DEFENSIF: "defensif",
    PROFILE_EQUILIBRE: "equilibre",
    PROFILE_OFFENSIF: "offensif",
}


def _normalize_text(question: str, history: Optional[List[dict]]) -> str:
    parts = [question or ""]
    for msg in (history or [])[-10:]:
        content = str(msg.get("content", "")).strip()
        if content:
            parts.append(content)
    text = " ".join(parts).lower()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("â", "a").replace("î", "i")
    text = text.replace("ô", "o").replace("û", "u").replace("ù", "u")
    text = text.replace("ç", "c")
    return text


def _extract_horizon_years(text: str) -> Optional[int]:
    matches = re.findall(r"(\d{1,2})\s*(an|ans|year|years)", text)
    if not matches:
        return None
    try:
        years = int(matches[-1][0])
    except Exception:
        return None
    if years < 0:
        return None
    return years


def _normalize_version(version: Optional[str]) -> str:
    raw = str(version or "").strip().lower()
    if not raw:
        return DEFAULT_SCORING_VERSION
    return SCORING_VERSION_ALIASES.get(raw, raw)


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _resolve_config(version: str) -> Tuple[str, Dict[str, Any]]:
    requested = _normalize_version(version)
    chosen = requested if requested in SCORING_CONFIGS else DEFAULT_SCORING_VERSION

    def _resolve(name: str, stack: Optional[set] = None) -> Dict[str, Any]:
        stack = stack or set()
        if name in stack:
            return {}
        cfg = SCORING_CONFIGS.get(name, {})
        if not isinstance(cfg, dict):
            return {}
        parent = _normalize_version(str(cfg.get("extends", ""))) if cfg.get("extends") else ""
        base = _resolve(parent, stack | {name}) if parent and parent in SCORING_CONFIGS else {}
        current = {k: v for k, v in cfg.items() if k != "extends"}
        return _deep_merge_dict(base, current)

    return chosen, _resolve(chosen)


def _feature_scores(
    text: str,
    config: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, List[str]], Optional[int]]:
    rules = config.get("feature_rules", {})
    feature_values: Dict[str, float] = {}
    evidence: Dict[str, List[str]] = {}

    for feature, rule in rules.items():
        keywords = [str(k).lower() for k in rule.get("keywords", [])]
        per_hit = float(rule.get("per_hit", 0.0))
        max_value = float(rule.get("max", 1.0))

        matched = [k for k in keywords if k and k in text]
        score = min(max_value, len(matched) * per_hit)
        feature_values[feature] = round(score, 4)
        evidence[feature] = matched

    years = _extract_horizon_years(text)
    if years is not None:
        if years <= 3:
            feature_values["horizon_short"] = 1.0
            evidence["horizon_short"] = [f"{years} ans"]
        elif years <= 8:
            feature_values["horizon_medium"] = 1.0
            evidence["horizon_medium"] = [f"{years} ans"]
        else:
            feature_values["horizon_long"] = 1.0
            evidence["horizon_long"] = [f"{years} ans"]

    return feature_values, evidence, years


def _score_profiles(
    feature_values: Dict[str, float],
    config: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, List[Dict[str, float]]]]:
    profile_weights = config.get("profile_weights", {})
    profile_bias = config.get("profile_bias", {})

    profile_scores_raw: Dict[str, float] = {}
    profile_contributions: Dict[str, List[Dict[str, float]]] = {}

    for profile, weights in profile_weights.items():
        bias = float(profile_bias.get(profile, 0.0))
        raw = bias
        contributions = [{"feature": "bias", "weight": 1.0, "value": bias, "impact": bias}]

        for feature, weight in weights.items():
            value = float(feature_values.get(feature, 0.0))
            impact = value * float(weight)
            raw += impact
            contributions.append(
                {
                    "feature": feature,
                    "weight": round(float(weight), 4),
                    "value": round(value, 4),
                    "impact": round(impact, 4),
                }
            )

        profile_scores_raw[profile] = raw
        profile_contributions[profile] = contributions

    min_raw = min(profile_scores_raw.values()) if profile_scores_raw else 0.0
    max_raw = max(profile_scores_raw.values()) if profile_scores_raw else 1.0
    spread = max(max_raw - min_raw, 1e-9)

    profile_scores_norm: Dict[str, float] = {}
    for profile, raw in profile_scores_raw.items():
        norm = ((raw - min_raw) / spread) * 100.0
        profile_scores_norm[profile] = round(norm, 2)

    return profile_scores_norm, profile_contributions


def score_profile_deterministic(
    question: str,
    history: Optional[List[dict]] = None,
    version: str = DEFAULT_SCORING_VERSION,
) -> Dict[str, Any]:
    requested_version = str(version or DEFAULT_SCORING_VERSION)
    requested_lower = requested_version.strip().lower()
    normalized_requested = _normalize_version(requested_version)
    resolved_version, config = _resolve_config(requested_version)
    text = _normalize_text(question=question, history=history)

    feature_values, evidence, horizon_years = _feature_scores(text=text, config=config)
    scores, contributions = _score_profiles(feature_values=feature_values, config=config)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    selected_profile = ranked[0][0] if ranked else PROFILE_EQUILIBRE
    top_score = ranked[0][1] if ranked else 0.0
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence_gap = round(top_score - second_score, 2)

    version_meta = config.get("version_metadata") if isinstance(config.get("version_metadata"), dict) else {}
    changes = version_meta.get("changes")
    changelog = changes if isinstance(changes, list) else []
    alias_applied = (normalized_requested != requested_lower) or (resolved_version != normalized_requested)

    return {
        "engine": "deterministic_profile_scoring",
        "version": resolved_version,
        "version_requested": requested_version,
        "version_alias_applied": alias_applied,
        "version_change_note": version_meta.get("change_note"),
        "version_released_at": version_meta.get("released_at"),
        "version_changelog": changelog,
        "selected_profile": selected_profile,
        "selected_profile_label": PROFILE_LABELS.get(selected_profile, selected_profile),
        "scores": scores,
        "confidence_gap": confidence_gap,
        "horizon_years_detected": horizon_years,
        "feature_values": feature_values,
        "evidence": evidence,
        "weights": config.get("profile_weights", {}),
        "contributions": contributions,
    }
