import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None


ROUTER_SYSTEM = """Tu es un mini-routeur d'intention pour un agent spécialisé.
Tu dois:
1) détecter l'intent (max 2)
2) extraire la cible exacte de réponse (answer_target) = le KPI / information principale demandée
3) définir un output_schema minimal qui empêche le bruit.

Tu retournes UNIQUEMENT du JSON valide, rien d'autre.

Intents autorisés (liste fermée):
- INFO
- CALCUL
- STRATEGIE
- TOP_LISTE
- SYNTHESE

Règles:
1) intents peut être multiple mais max 2.
2) answer_target doit contenir:
   - primary: string (ex: "kpi_value", "resultat_calcul", "strategie", "top_list")
   - required_fields: array
   - optional_fields: array
   - exclusions: array (tokens à exclure)
   - kpi_targets: array => KPI(s) explicitement demandés
   - answer_span: object { "focus": string, "include": [string], "exclude": [string] }
   - output_schema: objet JSON décrivant le format attendu (clés + types)
3) Si la demande est vague: needs_clarification=true + missing_inputs, mais propose quand même un target.
4) Priorité absolue: ne viser QUE la/les info(s) demandée(s) (kpi_targets + answer_span). Pas de contexte, pas de sources, pas de comparaisons si non demandées.
"""


FINALIZER_SYSTEM = """Tu es un finalizer STRICT.
Entrées:
- user_query
- context (peut contenir du bruit)
- routing (intents + answer_target)

Règles non négociables:
1) Tu retournes UNIQUEMENT un JSON valide conforme à routing.answer_target.output_schema.
2) Tu n'ajoutes AUCUNE clé en dehors du schema.
3) Tu exclus: titres bruts, snippets bruts, URLs, "non_renseigne", et tout contenu hors-sujet.
4) Tu réponds UNIQUEMENT à la cible: routing.answer_target.kpi_targets + routing.answer_target.answer_span.
   - Si l'utilisateur demande un KPI: tu donnes uniquement ce KPI (valeur + période si dispo).
   - Tu n'ajoutes pas de contexte, pas de comparatif, pas de recommandations, sauf si explicitement demandé.
5) Si info manquante:
   - tu remplis les champs requis avec "n/d" (ou texte indicatif sans chiffres inventés)
   - et tu ajoutes une note de vérification dans disclaimer SI (et seulement si) le schema le permet.
6) TOP_LISTE:
   - items = 1 item = 1 entité; inclure metric/value/period uniquement si le schema l'exige
   - si pas assez fiable: moins d'items (ne jamais compléter avec du bruit)
"""


REWRITE_SYSTEM = """You are a strict rewriter.
Input:
- routing
- previous_output
- errors

Task:
- return ONLY valid JSON matching output_schema
- explicitly fix listed errors
- do not add keys outside schema
- follow answer_target.required_fields / optional_fields strictly
- TOP_LISTE: items must follow output_schema (if metric/value/period are requested, keep them; never include URLs)
"""


CABINET_ROUTER_SYSTEM = """Tu es le ROUTER d’un assistant CGP cabinet.

Ta mission : analyser le message utilisateur et retourner un JSON strict indiquant :
- l’intention principale (intent)
- les intentions secondaires (intents_secondary)
- si une recherche web/données est requise (need_data)
- la métrique de classement si c’est un “top” (metric)
- les informations manquantes (missing_inputs) mais uniquement si elles sont bloquantes
- le format de réponse attendu (response_format)
- le style (cabinet_cgp)

INTENTS possibles :
- INFO_DEFINITION (définir/expliquer un concept : TOF, TD, WALT…)
- INFO_COMPARISON (différence entre 2 notions/enveloppes/produits)
- RANKING_TOP (top, classement, “meilleures SCPI…”)
- CALCULATION (projection, “combien”, rendement, mensualité…)
- STRATEGY_CGP (conseil patrimonial, allocation, fiscalité, arbitrage)
- OTHER

Règles strictes :
1) Ne pose PAS de question si l’utilisateur demande un top ou une définition : tu réponds immédiatement.
2) Ne demande des précisions que si c’est strictement bloquant (max 2 items dans missing_inputs).
3) Si le message contient explicitement une métrique (TOF, TD, WALT, frais, liquidité), utilise-la.
4) Si “top SCPI” sans métrique, metric par défaut = TD.
5) Réponds uniquement en JSON, sans texte autour.

Schéma JSON :
{
  "intent": "...",
  "intents_secondary": ["..."],
  "need_data": true/false,
  "metric": "TD|TOF|WALT|FEES|LIQUIDITY|null",
  "year": 2026|null,
  "missing_inputs": ["..."],
  "response_format": "CABINET_INFO|CABINET_CALC|CABINET_TOP|CABINET_STRATEGY",
  "style": "cabinet_cgp"
}
"""


CABINET_EXTRACTOR_SYSTEM = """Tu es un module d’extraction de données SCPI pour un cabinet CGP.

Entrée : une liste d’items (title, snippet, url) provenant du web OU une base interne.

Ta mission :
- Extraire uniquement des SCPI (noms de fonds).
- Extraire la métrique demandée (TD/TOF/WALT/frais/liquidité) si elle apparaît.
- Dédupliquer les SCPI.
- Ne jamais inclure d’articles, d’études, d’associations, ni de contenu hors SCPI.

Règles strictes :
1) Si un item n’est pas clairement une SCPI, tu l’exclus.
2) Tu n’inventes jamais de chiffres.
3) Tu dois produire un JSON strict uniquement.

Sortie JSON :
{
  "metric": "TD|TOF|WALT|FEES|LIQUIDITY",
  "candidates": [
    {
      "name": "Nom SCPI",
      "value": 98.0,
      "unit": "%",
      "period": "2024|2025|n/d",
      "source_domain": "exemple.com",
      "source_url": "https://...",
      "evidence": "extrait court prouvant le chiffre (<=200 caractères)"
    }
  ],
  "dropped": [
    {"title": "...", "reason": "not_scpi|irrelevant|no_metric"}
  ]
}
"""


CABINET_FINALIZER_SYSTEM = """Tu es le FINALIZER d’un assistant CGP cabinet.

Entrée : un JSON contenant :
- intent
- metric (si top)
- candidates (liste de SCPI + valeur + sources)
- éventuellement des hypothèses

Règles de sortie (non négociables) :
1) Répondre de manière claire, professionnelle, concise.
2) Ne jamais afficher de bruit (titres d’articles, études…).
3) Pour un TOP : afficher une liste classée, avec “Nom — metric: valeur — source: domaine”.
4) Si moins de 10 SCPI fiables, en afficher moins (ne jamais compléter avec du bruit).
5) Ajouter une ligne “À vérifier avant décision” (TD/TOF/WALT/frais/liquidité selon contexte).
6) Poser au maximum 2 questions de clarification, uniquement en fin, sans bloquer la réponse.

Format attendu :
- Titre
- Liste (≤10)
- 1 paragraphe court de lecture CGP
- À vérifier avant décision (1 ligne)
- Questions (max 2, optionnel)
"""


def simple_router(
    user_query: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Router simple en une seule fonction:
    - intent: INFO / CALCUL / STRATEGIE / TOP_LISTE / SYNTHESE
    - answer_target: required_fields + exclusions (+ schema minimal)
    - needs_clarification + 1-2 questions si ambigu
    """
    text = (user_query or "").strip()
    qn = _normalize_text(text)

    intent = "INFO"
    if any(w in qn for w in ["top", "classement", "palmares", "meilleures", "meilleurs"]):
        intent = "TOP_LISTE"
    elif any(w in qn for w in ["synthese", "synthèse", "resume", "résume", "en bref", "tldr", "tl;dr"]):
        intent = "SYNTHESE"
    elif any(w in qn for w in ["calcul", "combien", "projection", "mensualite", "mensualité", "rendement"]):
        intent = "CALCUL"
    elif any(w in qn for w in ["allocation", "strategie", "stratégie", "fiscalite", "fiscalité", "arbitrage", "recommande", "conseil"]):
        intent = "STRATEGIE"

    needs_clarification = False
    clarification_questions: List[str] = []
    token_count = len(re.findall(r"[a-z0-9]+", qn))

    if intent == "CALCUL":
        if not re.search(r"\d", qn):
            needs_clarification = True
            clarification_questions.extend([
                "Quel montant dois-je utiliser ?",
                "Quel horizon dois-je retenir (en années) ?",
            ])
    elif intent == "STRATEGIE":
        missing = []
        if not re.search(r"\b\d+\s*(ans?|mois)\b", qn):
            missing.append("horizon")
        if not re.search(r"\b\d+\s*(k|ke|keur|€|euros?)\b", qn):
            missing.append("montant")
        if missing:
            needs_clarification = True
            if "horizon" in missing:
                clarification_questions.append("Quel horizon d'investissement veux-tu retenir ?")
            if "montant" in missing:
                clarification_questions.append("Quel montant veux-tu allouer ?")
    elif intent == "INFO":
        if token_count <= 2 and not any(w in qn for w in ["tof", "td", "walt", "scpi", "frais"]):
            needs_clarification = True
            clarification_questions.append("Tu veux une définition de quoi exactement ?")

    if intent in {"TOP_LISTE", "INFO"}:
        needs_clarification = False
        clarification_questions = []

    if len(clarification_questions) > 2:
        clarification_questions = clarification_questions[:2]

    exclusions = [
        "snippets_bruts",
        "titres_bruts",
        "hors_sujet",
        "non_renseigne",
        "http://",
        "https://",
    ]

    kpi_targets = _extract_kpi_targets_from_query(text)
    if kpi_targets:
        focus = "kpi:" + ",".join([str(x) for x in kpi_targets])
        include = [str(x) for x in kpi_targets]
    else:
        focus = "reponse_directe"
        include = []

    if intent == "CALCUL":
        required_fields = ["hypotheses", "formule", "calcul", "resultat", "interpretation"]
        output_schema = {
            "type": "object",
            "properties": {
                "hypotheses": {"type": "string"},
                "formule": {"type": "string"},
                "calcul": {"type": "string"},
                "resultat": {"type": "string"},
                "interpretation": {"type": "string"},
                "disclaimer": {"type": "string"},
            },
            "required": ["hypotheses", "formule", "calcul", "resultat", "interpretation"],
        }
        optional_fields = ["disclaimer"]
    elif intent == "STRATEGIE":
        required_fields = ["analyse", "strategie", "arbitrages", "risques", "conclusion"]
        output_schema = {
            "type": "object",
            "properties": {
                "analyse": {"type": "string"},
                "strategie": {"type": "string"},
                "arbitrages": {"type": "string"},
                "risques": {"type": "string"},
                "conclusion": {"type": "string"},
            },
            "required": ["analyse", "strategie", "arbitrages", "risques", "conclusion"],
        }
        optional_fields = []
    elif intent == "TOP_LISTE":
        required_fields = ["items"]
        output_schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array"},
                "disclaimer": {"type": "string"},
            },
            "required": ["items"],
        }
        optional_fields = ["disclaimer"]
    elif intent == "SYNTHESE":
        required_fields = ["points"]
        output_schema = {
            "type": "object",
            "properties": {
                "points": {"type": "array"},
                "disclaimer": {"type": "string"},
            },
            "required": ["points"],
        }
        optional_fields = ["disclaimer"]
    else:
        required_fields = ["reponse"]
        output_schema = {
            "type": "object",
            "properties": {
                "reponse": {"type": "string"},
                "disclaimer": {"type": "string"},
            },
            "required": ["reponse"],
        }
        optional_fields = ["disclaimer"]

    return {
        "intent": intent,
        "answer_target": {
            "primary": "kpi_value" if kpi_targets else "reponse_directe",
            "required_fields": required_fields,
            "optional_fields": optional_fields,
            "exclusions": exclusions,
            "kpi_targets": kpi_targets,
            "answer_span": {
                "focus": focus,
                "include": include,
                "exclude": [
                    "sources",
                    "liens",
                    "url",
                    "comparaison",
                    "contexte",
                    "historique",
                    "snippets",
                    "titres",
                ],
            },
            "output_schema": output_schema,
        },
        "needs_clarification": needs_clarification,
        "clarification_questions": clarification_questions,
    }


KPI_KEYWORD_GROUPS: Dict[str, List[str]] = {
    "td": ["td", "tdvm", "taux de distribution", "rendement"],
    "tof": ["tof", "taux d'occupation financier", "occupation financier", "occupation"],
    "walt": ["walt", "duree moyenne des baux", "durée moyenne des baux", "baux"],
    "frais": ["frais", "frais d'entree", "frais entrée", "commission"],
    "capitalisation": ["capitalisation", "encours"],
    "collecte": ["collecte", "collecte nette"],
    "prix_part": ["prix de part", "valeur de part", "souscription", "part"],
}


CABINET_METRIC_MAP = {
    "TD": ["td", "tdvm", "taux de distribution", "rendement"],
    "TOF": ["tof", "taux d'occupation financier", "occupation financier"],
    "WALT": ["walt", "duree moyenne des baux", "durée moyenne des baux", "baux"],
    "FEES": ["frais", "commission", "frais d'entree", "frais entrée"],
    "LIQUIDITY": ["liquidite", "liquidité", "delai de retrait", "retrait"],
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _infer_metric_cabinet(question: str) -> Optional[str]:
    qn = _normalize_text(question)
    for metric, keywords in CABINET_METRIC_MAP.items():
        if any(word in qn for word in keywords):
            return metric
    return None


def _infer_year_cabinet(question: str) -> Optional[int]:
    qn = _normalize_text(question)
    m = re.search(r"\b(20\d{2})\b", qn)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _infer_intent_cabinet(question: str) -> str:
    qn = _normalize_text(question)
    if any(word in qn for word in ["top", "classement", "palmares", "meilleures", "meilleurs"]):
        return "RANKING_TOP"
    if any(word in qn for word in ["definition", "définition", "c'est quoi", "qu'est-ce", "qu est ce"]):
        return "INFO_DEFINITION"
    if any(word in qn for word in ["difference", "différence", "comparaison", "compare", "vs", "versus"]):
        return "INFO_COMPARISON"
    if any(word in qn for word in ["calcul", "combien", "projection", "mensualite", "mensualité", "rendement"]):
        return "CALCULATION"
    if any(word in qn for word in ["allocation", "strategie", "stratégie", "fiscalite", "fiscalité", "arbitrage", "recommande", "conseil"]):
        return "STRATEGY_CGP"
    return "OTHER"


def _response_format_for_intent(intent: str) -> str:
    if intent == "CALCULATION":
        return "CABINET_CALC"
    if intent == "RANKING_TOP":
        return "CABINET_TOP"
    if intent == "STRATEGY_CGP":
        return "CABINET_STRATEGY"
    return "CABINET_INFO"


def _sanitize_router_output(
    user_query: str,
    routing: Dict[str, Any],
) -> Dict[str, Any]:
    intent = str(routing.get("intent") or "").strip() or _infer_intent_cabinet(user_query)
    intents_secondary = routing.get("intents_secondary") if isinstance(routing.get("intents_secondary"), list) else []
    metric = routing.get("metric")
    if isinstance(metric, str):
        metric = metric.strip().upper()
    else:
        metric = None
    if metric not in {"TD", "TOF", "WALT", "FEES", "LIQUIDITY"}:
        metric = _infer_metric_cabinet(user_query)
    if intent == "RANKING_TOP" and not metric:
        metric = "TD"

    year = routing.get("year")
    if not isinstance(year, int):
        year = _infer_year_cabinet(user_query)

    need_data = bool(routing.get("need_data")) if isinstance(routing.get("need_data"), bool) else None
    if need_data is None:
        need_data = intent in {"RANKING_TOP", "CALCULATION", "INFO_COMPARISON"}

    response_format = str(routing.get("response_format") or "").strip().upper()
    if response_format not in {"CABINET_INFO", "CABINET_CALC", "CABINET_TOP", "CABINET_STRATEGY"}:
        response_format = _response_format_for_intent(intent)

    missing_inputs = routing.get("missing_inputs") if isinstance(routing.get("missing_inputs"), list) else []
    missing_inputs = [str(item).strip() for item in missing_inputs if str(item).strip()]

    if intent in {"RANKING_TOP", "INFO_DEFINITION"}:
        missing_inputs = []
    if metric:
        missing_inputs = [m for m in missing_inputs if "metric" not in m.lower() and "critere" not in m.lower()]
    missing_inputs = missing_inputs[:2]

    return {
        "intent": intent,
        "intents_secondary": intents_secondary[:2],
        "need_data": bool(need_data),
        "metric": metric,
        "year": year,
        "missing_inputs": missing_inputs,
        "response_format": response_format,
        "style": "cabinet_cgp",
    }


def _call_model_text(
    client: Any,
    model: str,
    max_tokens: int,
    temperature: float,
    system_prompt: str,
    payload: Dict[str, Any],
) -> str:
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    return _extract_text_from_claude(message)


def _parse_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = re.sub(r"[^\d.,-]", "", str(value))
        if not text:
            return None
        return float(text.replace(",", "."))
    except Exception:
        return None


def _dedupe_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_text(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _rank_candidates(candidates: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for item in candidates:
        value = _parse_float(item.get("value"))
        if value is None:
            continue
        scored.append({**item, "value": value})
    scored.sort(key=lambda x: x.get("value", 0.0), reverse=True)
    return scored[: max(1, limit)]


def answer_with_cabinet_pipeline(
    user_query: str,
    items: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_items: int = 10,
) -> Dict[str, Any]:
    if anthropic is None:
        raise RuntimeError("anthropic package unavailable")

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("missing ANTHROPIC_API_KEY")

    model_name = model or os.getenv("CLAUDE_OPTIMIZED_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key)

    routing_raw = _call_model(
        client=client,
        model=model_name,
        max_tokens=700,
        temperature=0.0,
        system_prompt=CABINET_ROUTER_SYSTEM,
        payload={"user_query": user_query},
    )
    routing = _sanitize_router_output(user_query, routing_raw)

    intent = routing.get("intent")
    metric = routing.get("metric")
    need_data = bool(routing.get("need_data"))

    extraction: Dict[str, Any] = {"metric": metric, "candidates": [], "dropped": []}
    ranked: List[Dict[str, Any]] = []
    errors: List[str] = []

    if need_data and items:
        extractor_payload = {
            "metric": metric,
            "items": [
                {
                    "title": str(item.get("title") or ""),
                    "snippet": str(item.get("snippet") or item.get("body") or ""),
                    "url": str(item.get("url") or item.get("href") or ""),
                }
                for item in items
            ],
        }
        extraction = _call_model(
            client=client,
            model=model_name,
            max_tokens=900,
            temperature=0.0,
            system_prompt=CABINET_EXTRACTOR_SYSTEM,
            payload=extractor_payload,
        )
        if not isinstance(extraction, dict):
            extraction = {"metric": metric, "candidates": [], "dropped": []}
        candidates = extraction.get("candidates") if isinstance(extraction.get("candidates"), list) else []
        candidates = _dedupe_candidates([item for item in candidates if isinstance(item, dict)])
        ranked = _rank_candidates(candidates, limit=max_items)
        extraction["candidates"] = candidates
    else:
        errors.append("no_data_needed_or_items")

    final_payload = {
        "intent": intent,
        "metric": metric,
        "candidates": ranked,
        "hypotheses": {
            "year": routing.get("year"),
            "missing_inputs": routing.get("missing_inputs"),
        },
    }
    answer = _call_model_text(
        client=client,
        model=model_name,
        max_tokens=1000,
        temperature=0.2,
        system_prompt=CABINET_FINALIZER_SYSTEM,
        payload=final_payload,
    )
    answer = re.sub(r"https?://\\S+", "", (answer or "")).strip()

    return {
        "answer": answer,
        "routing": routing,
        "extraction": extraction,
        "ranked": ranked,
        "ok": bool(answer),
        "errors": errors,
        "meta": {
            "model": model_name,
            "intent": intent,
            "metric": metric,
        },
    }


def is_router_pipeline_available() -> bool:
    if anthropic is None:
        return False
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    return bool(api_key)


def _extract_text_from_claude(message: Any) -> str:
    parts: List[str] = []
    for block in getattr(message, "content", []) or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        else:
            if getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


def _safe_json_load(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        loaded = json.loads(match.group(0))
        return loaded if isinstance(loaded, dict) else {}


def _extract_kpi_targets_from_query(user_query: str) -> List[str]:
    qn = (user_query or "").lower()
    found: List[str] = []
    for target, keywords in KPI_KEYWORD_GROUPS.items():
        if any(keyword in qn for keyword in keywords):
            found.append(target)
    return found


def _call_model(
    client: Any,
    model: str,
    max_tokens: int,
    temperature: float,
    system_prompt: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    raw = _extract_text_from_claude(message)
    return _safe_json_load(raw)


def route_intent_and_target(client: Any, model: str, user_query: str) -> Dict[str, Any]:
    payload = {"user_query": user_query}
    routing = _call_model(
        client=client,
        model=model,
        max_tokens=700,
        temperature=0.0,
        system_prompt=ROUTER_SYSTEM,
        payload=payload,
    )
    if not isinstance(routing, dict):
        routing = {}

    if "answer_target" not in routing or not isinstance(routing.get("answer_target"), dict):
        routing["answer_target"] = {}
    answer_target: Dict[str, Any] = routing["answer_target"]

    # deterministic fallback for KPI target extraction
    kpi_targets = answer_target.get("kpi_targets")
    if not isinstance(kpi_targets, list) or not kpi_targets:
        answer_target["kpi_targets"] = _extract_kpi_targets_from_query(user_query)

    # answer_span: force a narrow focus to reduce over-inclusion
    if not isinstance(answer_target.get("answer_span"), dict):
        focus = ""
        if answer_target.get("kpi_targets"):
            focus = "kpi:" + ",".join([str(x) for x in answer_target.get("kpi_targets")])
        answer_target["answer_span"] = {
            "focus": focus or "reponse_directe",
            "include": [str(x) for x in (answer_target.get("kpi_targets") or [])],
            "exclude": [
                "sources",
                "liens",
                "url",
                "comparaison",
                "contexte",
                "historique",
                "snippets",
                "titres",
            ],
        }

    # minimal fallback schema if model under-specifies target
    if not isinstance(answer_target.get("output_schema"), dict):
        # If KPI detected, enforce a KPI-only schema.
        if answer_target.get("kpi_targets"):
            answer_target["output_schema"] = {
                "type": "object",
                "properties": {
                    "kpi": {"type": "string"},
                    "value": {"type": "string"},
                    "period": {"type": "string"},
                    "disclaimer": {"type": "string"},
                },
                "required": ["kpi", "value"],
            }
            answer_target["required_fields"] = ["kpi", "value"]
            answer_target["optional_fields"] = ["period", "disclaimer"]
            answer_target.setdefault("primary", "kpi_value")
        else:
            answer_target["output_schema"] = {
                "type": "object",
                "properties": {
                    "reponse": {"type": "string"},
                    "disclaimer": {"type": "string"},
                },
                "required": ["reponse"],
            }
            answer_target["required_fields"] = ["reponse"]
            answer_target.setdefault("optional_fields", ["disclaimer"])
            answer_target.setdefault("primary", "reponse_directe")

        answer_target.setdefault(
            "exclusions",
            ["snippets_bruts", "titres_bruts", "hors_sujet", "non_renseigne", "http://", "https://"],
        )

    return routing


def finalize_answer(
    client: Any,
    model: str,
    user_query: str,
    context: str,
    routing: Dict[str, Any],
    temperature: float = 0.2,
) -> Dict[str, Any]:
    payload = {
        "user_query": user_query,
        "context": context,
        "routing": routing,
    }
    return _call_model(
        client=client,
        model=model,
        max_tokens=1200,
        temperature=temperature,
        system_prompt=FINALIZER_SYSTEM,
        payload=payload,
    )


@dataclass
class CheckResult:
    ok: bool
    errors: List[str]


def _get_schema(routing: Dict[str, Any]) -> Dict[str, Any]:
    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    schema = target.get("output_schema")
    return schema if isinstance(schema, dict) else {}


def _allowed_keys(schema: Dict[str, Any]) -> List[str]:
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    return list(props.keys())


def _check_keys_only(output: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    allowed = set(_allowed_keys(schema))
    if not allowed:
        return []
    extras = [key for key in output.keys() if key not in allowed]
    if extras:
        return [f"extra_keys:{extras}"]
    return []


def _check_required(output: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    errors: List[str] = []
    for key in required:
        if key not in output:
            errors.append(f"missing_required:{key}")
    return errors


def _check_types(output: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    errors: List[str] = []
    for key, rule in props.items():
        if key not in output or not isinstance(rule, dict):
            continue
        expected = rule.get("type")
        value = output.get(key)
        if expected == "string" and not isinstance(value, str):
            errors.append(f"type_error:{key}:string")
        if expected == "array" and not isinstance(value, list):
            errors.append(f"type_error:{key}:array")
        if expected == "object" and not isinstance(value, dict):
            errors.append(f"type_error:{key}:object")
    return errors


def _flatten_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, list):
        return " ".join(_flatten_text(x) for x in obj)
    if isinstance(obj, dict):
        return " ".join(_flatten_text(v) for v in obj.values())
    return str(obj)


def _check_noise(output: Dict[str, Any], routing: Dict[str, Any]) -> List[str]:
    text = _flatten_text(output).lower()
    errors: List[str] = []
    markers = [
        "non_renseigne",
        "non renseigne",
        "non renseigné",
        "non renseignee",
        "non renseignée",
        "snippet",
        "snippets bruts",
        "snippets_bruts",
        "titres bruts",
        "titres_bruts",
        "title:",
        "http://",
        "https://",
        "sources utilisées",
        "sources utilisees",
    ]
    for marker in markers:
        if marker in text:
            errors.append(f"noise:{marker}")

    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    exclusions = target.get("exclusions") if isinstance(target.get("exclusions"), list) else []
    for exclusion in exclusions:
        token = str(exclusion or "").strip().lower()
        if not token:
            continue
        if token in text:
            errors.append(f"excluded_token:{token}")
    return errors


def _check_kpi_focus(output: Dict[str, Any], routing: Dict[str, Any], user_query: str) -> List[str]:
    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    kpi_targets = target.get("kpi_targets") if isinstance(target.get("kpi_targets"), list) else []
    if not kpi_targets:
        kpi_targets = _extract_kpi_targets_from_query(user_query)
    if not kpi_targets:
        return []

    text = _flatten_text(output).lower()
    matched = False
    for kpi in kpi_targets:
        words = KPI_KEYWORD_GROUPS.get(str(kpi), [str(kpi)])
        if any(word in text for word in words):
            matched = True
            break
    if matched:
        return []
    return [f"kpi_focus_missing:{','.join([str(k) for k in kpi_targets])}"]


def _check_answer_target_fields(output: Dict[str, Any], routing: Dict[str, Any]) -> List[str]:
    if not isinstance(output, dict):
        return ["output_not_object"]
    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    required = target.get("required_fields") if isinstance(target.get("required_fields"), list) else []
    optional = target.get("optional_fields") if isinstance(target.get("optional_fields"), list) else []
    errors: List[str] = []

    for key in required:
        if key not in output:
            errors.append(f"missing_required_field:{key}")

    allowed = [str(k) for k in (required + optional) if str(k)]
    if allowed:
        extras = [k for k in output.keys() if k not in allowed]
        if extras:
            errors.append(f"extra_fields:{extras}")
    return errors


def _is_top_routing(routing: Dict[str, Any]) -> bool:
    intent = str(routing.get("intent") or "").upper()
    if "TOP" in intent:
        return True
    intents = routing.get("intents")
    if isinstance(intents, list):
        if any("TOP" in str(x).upper() for x in intents):
            return True
    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    required = target.get("required_fields") if isinstance(target.get("required_fields"), list) else []
    if "items" in [str(x) for x in required]:
        return True
    schema = target.get("output_schema") if isinstance(target.get("output_schema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if "items" in props:
        return True
    return False


def _is_entity_only_string(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    lowered = _normalize_text(text)
    disallowed_tokens = [
        "td",
        "tdvm",
        "tof",
        "walt",
        "rendement",
        "taux de distribution",
        "source",
        "http://",
        "https://",
        "%",
        "pga",
        "collecte",
        "capitalisation",
        "frais",
        "liquidite",
    ]
    if any(tok in lowered for tok in disallowed_tokens):
        return False
    if re.search(r"\d+(?:[.,]\d+)?\s*%", text):
        return False
    if len(text) > 80:
        return False
    separators = [" - ", " — ", " | ", ":"]
    if any(sep in text for sep in separators) and re.search(r"\d", text):
        return False
    return True


def _check_top_entities(output: Dict[str, Any], routing: Dict[str, Any]) -> List[str]:
    if not _is_top_routing(routing):
        return []
    if not isinstance(output, dict):
        return ["top_output_not_object"]
    items = output.get("items")
    if items is None:
        return ["top_items_missing"]
    if not isinstance(items, list):
        return ["top_items_not_array"]
    errors: List[str] = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, str):
            if not _is_entity_only_string(item):
                errors.append(f"top_item_not_entity:{idx}")
            continue
        if isinstance(item, dict):
            name = item.get("name") or item.get("label") or ""
            if not _is_entity_only_string(str(name)):
                errors.append(f"top_item_not_entity:{idx}")
            allowed_item_keys = {"name", "label", "metric", "value", "period", "source"}
            extra_keys = [k for k in item.keys() if k not in allowed_item_keys]
            if extra_keys:
                errors.append(f"top_item_extra_fields:{idx}:{extra_keys}")
            value = str(item.get("value") or "")
            if "http://" in value or "https://" in value:
                errors.append(f"top_item_value_contains_url:{idx}")
            continue
        errors.append(f"top_item_invalid_type:{idx}")
    return errors


def check_output(output: Dict[str, Any], routing: Dict[str, Any], user_query: str) -> CheckResult:
    schema = _get_schema(routing)
    errors: List[str] = []
    errors.extend(_check_answer_target_fields(output, routing))
    errors.extend(_check_required(output, schema))
    errors.extend(_check_keys_only(output, schema))
    errors.extend(_check_types(output, schema))
    errors.extend(_check_noise(output, routing))
    errors.extend(_check_kpi_focus(output, routing, user_query=user_query))
    errors.extend(_check_top_entities(output, routing))
    return CheckResult(ok=(len(errors) == 0), errors=errors)


def rewrite_output(
    client: Any,
    model: str,
    routing: Dict[str, Any],
    previous_output: Dict[str, Any],
    errors: List[str],
) -> Dict[str, Any]:
    payload = {
        "routing": routing,
        "previous_output": previous_output,
        "errors": errors,
    }
    return _call_model(
        client=client,
        model=model,
        max_tokens=900,
        temperature=0.0,
        system_prompt=REWRITE_SYSTEM,
        payload=payload,
    )


def answer_with_router_finalizer_checker(
    user_query: str,
    context: str,
    model: Optional[str] = None,
    max_retries: int = 1,
    routing_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if anthropic is None:
        raise RuntimeError("anthropic package unavailable")

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("missing ANTHROPIC_API_KEY")

    model_name = model or os.getenv("CLAUDE_OPTIMIZED_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key)

    if isinstance(routing_override, dict) and routing_override:
        routing = routing_override
    else:
        routing = simple_router(user_query=user_query, model=model_name)
    output = finalize_answer(
        client=client,
        model=model_name,
        user_query=user_query,
        context=context,
        routing=routing,
    )

    check = check_output(output=output, routing=routing, user_query=user_query)
    attempts = 0
    retry_budget = min(max(0, int(max_retries or 0)), 1)
    while (not check.ok) and attempts < retry_budget:
        output = rewrite_output(
            client=client,
            model=model_name,
            routing=routing,
            previous_output=output,
            errors=check.errors,
        )
        check = check_output(output=output, routing=routing, user_query=user_query)
        attempts += 1

    answer_text = ""
    if isinstance(output, dict):
        answer_text = render_router_output_text(output, routing)
    warnings = list(check.errors) if not check.ok else []

    return {
        "routing": routing,
        "output": output,
        "answer_text": answer_text,
        "answer_json": output if isinstance(output, dict) else None,
        "warnings": warnings,
        "used_facts": [],
        "ok": check.ok,
        "errors": check.errors,
        "meta": {
            "model": model_name,
            "attempts": attempts,
            "routing_override_used": bool(isinstance(routing_override, dict) and routing_override),
        },
    }


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        lines: List[str] = []
        for idx, item in enumerate(value, start=1):
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("label") or "").strip()
                metric = str(item.get("metric") or "").strip()
                source = str(item.get("source") or "").strip()
                if name and metric and source:
                    lines.append(f"{idx}. {name} - {metric} ({source})")
                elif name and metric:
                    lines.append(f"{idx}. {name} - {metric}")
                elif name:
                    lines.append(f"{idx}. {name}")
                else:
                    lines.append(f"{idx}. {json.dumps(item, ensure_ascii=False)}")
            else:
                lines.append(f"- {str(item)}")
        return "\n".join(lines).strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_router_output_text(output: Dict[str, Any], routing: Dict[str, Any]) -> str:
    if not isinstance(output, dict):
        return ""
    target = routing.get("answer_target") if isinstance(routing.get("answer_target"), dict) else {}
    required = target.get("required_fields") if isinstance(target.get("required_fields"), list) else []
    optional = target.get("optional_fields") if isinstance(target.get("optional_fields"), list) else []

    ordered_fields: List[str] = []
    for key in required + optional:
        if key not in ordered_fields:
            ordered_fields.append(str(key))
    if not ordered_fields:
        ordered_fields = list(output.keys())

    lines: List[str] = []
    for key in ordered_fields:
        if key not in output:
            continue
        text_value = _value_to_text(output.get(key))
        if not text_value:
            continue
        if "\n" in text_value:
            lines.append(f"{key}:")
            lines.append(text_value)
        else:
            lines.append(f"{key}: {text_value}")

    return "\n".join(lines).strip()
