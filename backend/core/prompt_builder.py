# backend/core/prompt_builder.py
from typing import Any, Dict, List, Optional


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _history_to_text(history: Optional[List[dict]], max_items: int = 4) -> str:
    rows: List[str] = []
    for msg in (history or [])[-max_items:]:
        role = _clean(msg.get("role")).upper() or "USER"
        content = _clean(msg.get("content"))
        if content:
            rows.append(f"{role}: {content}")
    return "\n".join(rows) if rows else "Aucun historique pertinent."


def _format_instruction(intent_type: str) -> str:
    mapping = {
        "TOP": (
            "Commence par une réponse directe, puis donne 3 à 5 points classés ou comparatifs. "
            "Si le classement exact n'est pas possible, explique le périmètre manquant."
        ),
        "KPI": (
            "Commence par la donnée clé, puis ajoute 2 à 4 points de contexte. "
            "Chaque chiffre cité doit mentionner sa source."
        ),
        "STRATEGIE": (
            "FORMAT STRATEGIE_CGP OBLIGATOIRE:\n"
            "1) Décision (1–2 lignes)\n"
            "2) Allocation proposée (TABLEAU OBLIGATOIRE)\n"
            "   Colonnes: Poche | % cible (fourchette) | Enveloppe | Objectif | Contraintes (liquidité/horizon)\n"
            "   Règles:\n"
            "   - Toujours une fourchette en % (ex: 10–20), jamais une valeur seule.\n"
            "   - Total cohérent ≈ 100%.\n"
            "   - Si info manquante: propose une allocation prudente par défaut + section 'Hypothèses'.\n"
            "3) Justification (3–6 bullets)\n"
            "4) Arbitrages (IR/IS, enveloppe, liquidité)\n"
            "5) Risques & garde-fous (3–5 bullets)\n"
            "6) Plan d’action CGP (3 étapes)\n"
            "7) Questions à préciser (max 2, seulement si nécessaire)\n"
        ),
        "RAPPORT": (
            "Réponds comme une note client courte: synthèse, points clés, vigilance, prochaine action."
        ),
        "DARWIN": (
            "Réponse directe puis points utiles sur l'offre ou la donnée Darwin, sans blabla."
        ),
    }
    return mapping.get(intent_type, "Réponse directe puis 3 points utiles maximum.")


def build_prompt(
    question: str,
    history: Optional[List[dict]],
    intent: Dict[str, Any],
    evidence_pack: Dict[str, Any],
) -> Dict[str, str]:
    intent_type = _clean(intent.get("type")).upper() or "INFO"

    evidence_lines: List[str] = []
    for index, item in enumerate(evidence_pack.get("items") or [], start=1):
        title = _clean(item.get("title"))
        snippet = _clean(item.get("snippet"))
        domain = _clean(item.get("domain"))
        date = _clean(item.get("date"))
        line = f"{index}. [{item.get('layer')}] {title}"
        if snippet:
            line += f"\n   - Fait: {snippet}"
        if domain or date:
            line += f"\n   - Source: {domain or 'n/a'}"
            if date:
                line += f" | Date: {date}"
        evidence_lines.append(line)

    evidence_text = "\n".join(evidence_lines) if evidence_lines else "Aucune preuve exploitable pour le moment."
    clarification_text = "\n".join(
        f"- {item}" for item in (intent.get("clarification_questions") or [])
    ) or "- Aucune"

    # System prompt: on durcit STRATEGIE pour être "CGP senior" et éviter l'hallucination.
    # Différence clé vs INFO/KPI:
    # - En STRATEGIE, on autorise des allocations par défaut prudentes (hypothèses explicites),
    #   MAIS on interdit d'inventer des chiffres factuels non sourcés (taux, performances, dates exactes).
    base_system_prompt = (
        "Tu es Claude, cerveau unique du pipeline Darwin.\n"
        "Tu rédiges la réponse finale en français à partir des preuves fournies.\n"
        "Règles:\n"
        "- Utilise en priorité le pack de preuves ci-dessous.\n"
        "- N'invente jamais de chiffres, dates, classements ou sources.\n"
        "- Si tu cites un chiffre factuel (taux, performance, date, KPI), mentionne la source/le domaine.\n"
        "- Réponse directe, utile, sans méta-commentaire.\n"
    )

    strategie_addendum = (
        "Mode STRATEGIE_CGP:\n"
        "- Tu dois produire une recommandation exploitable par un CGP.\n"
        "- TABLEAU D’ALLOCATION OBLIGATOIRE (fourchettes %).\n"
        "- Si des infos manquent (horizon, fiscalité, profil risque), tu proposes une allocation prudente "
        "par défaut avec une section 'Hypothèses', puis tu poses au maximum 2 questions.\n"
        "- Ne bloque jamais la réponse.\n"
    )

    if intent_type == "STRATEGIE":
        system_prompt = base_system_prompt + strategie_addendum
    else:
        system_prompt = (
            base_system_prompt
            + "- Si une preuve manque, dis-le clairement et demande au plus 2 précisions utiles.\n"
        )

    user_prompt = (
        f"QUESTION:\n{_clean(question)}\n\n"
        f"INTENTION:\n- type={intent_type}\n"
        f"- mots_cles={', '.join(intent.get('keywords') or []) or 'aucun'}\n"
        f"- kpi_target={_clean(intent.get('kpi_target')) or 'none'}\n"
        f"- annee={_clean(intent.get('year')) or 'non précisée'}\n"
        f"- darwin={bool(intent.get('is_darwin_specific'))}\n\n"
        f"HISTORIQUE COURT:\n{_history_to_text(history)}\n\n"
        f"PACK DE PREUVES:\n{evidence_text}\n\n"
        f"POINTS A CLARIFIER SI BESOIN:\n{clarification_text}\n\n"
        f"FORMAT ATTENDU:\n{_format_instruction(intent_type)}\n"
    )

    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }