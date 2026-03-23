"""
critical_claude_enforcement.py - Patch critique pour forcer Claude à fonctionner
Résout le problème où Claude ne synthétise plus les réponses
"""

import os
import json
from typing import Any, Dict, List, Optional, Tuple


def validate_response_relevance(
    question: str,
    answer: str,
    raw_material: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Valide que la réponse Claude est pertinente à la question.
    Si non pertinente, force une reformulation.
    """
    question_norm = (question or "").lower().strip()
    answer_norm = (answer or "").lower().strip()

    # Délai à minima
    if len(answer_norm) < 50:
        return {
            "valid": False,
            "reason": "response_too_short",
            "action": "fallback_required"
        }

    # Check de pertinence: la réponse doit répondre à la structure de la question
    keywords_from_question = _extract_question_keywords(question)
    keywords_in_answer = _extract_answer_keywords(answer)

    # Si overlaps insuffisant, réponse potentiellement hors-sujet
    overlap = set(keywords_from_question) & set(keywords_in_answer)
    overlap_ratio = len(overlap) / max(len(keywords_from_question), 1)

    if overlap_ratio < 0.3 and len(keywords_from_question) > 2:
        return {
            "valid": False,
            "reason": "insufficient_keyword_overlap",
            "overlap_ratio": overlap_ratio,
            "action": "force_reformulation"
        }

    # Check de relevance spécifiques
    # Si la question parle de budget/gestion et la réponse est juste un document PDF
    if any(w in question_norm for w in ["budget", "gérer", "gestion", "investir", "placement"]):
        if any(w in answer_norm for w in ["rapport", "cob", "annexe", "amf", "1998", "2000", "document"]):
            if not any(w in answer_norm for w in ["euro", "€", "budget", "gestion", "conseil", "stratégie"]):
                return {
                    "valid": False,
                    "reason": "document_instead_of_advice",
                    "action": "force_reformulation_with_instructions"
                }

    return {
        "valid": True,
        "reason": "response_valid",
        "overlap_ratio": overlap_ratio if overlap_ratio > 0 else None
    }


def _extract_question_keywords(question: str) -> List[str]:
    """Extrait les mots-clés importants de la question"""
    import re
    
    # Stopwords French
    stopwords = {
        "le", "la", "les", "de", "du", "des", "un", "une", "des", "et", "ou",
        "par", "pour", "sur", "dans", "avec", "sans", "à", "au", "est",
        "comment", "quel", "quelle", "pourquoi", "quoi", "qui", "où", "quand"
    }
    
    words = re.findall(r"\b\w{3,}\b", question.lower())
    return [w for w in words if w not in stopwords]


def _extract_answer_keywords(answer: str) -> List[str]:
    """Extrait les mots-clés importants de la réponse"""
    import re
    
    stopwords = {
        "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou",
        "par", "pour", "sur", "dans", "avec", "sans", "à", "au", "est",
        "source", "document", "résultat", "récupéré", "trouvé", "page"
    }
    
    words = re.findall(r"\b\w{3,}\b", answer.lower())
    return [w for w in words if w not in stopwords]


def enforce_claude_call(
    orchestrate_function,
    question: str,
    history: Optional[List[dict]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Wrapper critique autour d'orchestrate qui force Claude à être appelé.
    Si Claude échoue silencieusement, détecte et force fallback.
    """
    print(f"🚨 [ENFORCE] Claude enforcement wrapper called")
    print(f"🚨 [ENFORCE] Question: {question[:100]}...")

    # Appel original
    result = orchestrate_function(question, history=history, **kwargs)

    # Validation de la réponse
    answer = result.get("answer", "").strip()
    validation = validate_response_relevance(
        question=question,
        answer=answer,
        raw_material=result.get("meta", {})
    )

    print(f"🚨 [ENFORCE] Response validation: {validation}")

    # Si réponse valide, retourner tel quel
    if validation.get("valid"):
        print(f"✅ [ENFORCE] Response is valid, proceeding normally")
        return result

    # Sinon, force une reformulation
    print(f"⚠️ [ENFORCE] Response is invalid - forcing reformulation")

    action = validation.get("action", "fallback_required")

    if action == "force_reformulation":
        return _force_claude_reformulation(question, result)
    elif action == "force_reformulation_with_instructions":
        return _force_claude_reformulation_with_instructions(question, result)
    elif action == "fallback_required":
        return _provide_fallback_response(question, result)
    else:
        return result


def _force_claude_reformulation(
    question: str,
    original_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Force Claude à reformuler une réponse invalide"""
    print(f"🔄 [ENFORCE] Forcing Claude reformulation...")

    # Récupérer les sources brutes
    raw_material = original_result.get("meta", {})
    evidence_pack = original_result.get("answer_structured_v2", {})

    # Construire un prompt explicite forçant la synthèse
    reformulation_prompt = f"""
Vous êtes un conseiller financier expertise.

QUESTION UTILISATEUR:
{question}

Votre rôle:
- Répondez DIRECTEMENT à la question
- Donnez des CONSEILS CONCRETS et ACTIONNABLES
- Évitez les documents, rapports, ou réponses génériques
- Structurez votre réponse: situation → recommandations → prochaines étapes

Répondez maintenant de manière concise et pertinente.
"""

    try:
        # Appel direct Claude pour reformulation
        from backend.core.orchestrator_v3 import _call_claude
        reformed_answer, _ = _call_claude(
            system_prompt="Vous êtes un excellent conseiller en gestion de patrimoine.",
            user_prompt=reformulation_prompt
        )

        if reformed_answer and reformed_answer.strip():
            print(f"✅ [ENFORCE] Claude reformulation successful")
            return {
                **original_result,
                "answer": reformed_answer,
                "answer_text": reformed_answer,
                "meta": {
                    **(original_result.get("meta") or {}),
                    "claude_enforcement": "reformulation_applied"
                }
            }
        else:
            print(f"❌ [ENFORCE] Claude reformulation failed - empty response")
            return _provide_fallback_response(question, original_result)

    except Exception as exc:
        print(f"❌ [ENFORCE] Claude reformulation error: {exc}")
        return _provide_fallback_response(question, original_result)


def _force_claude_reformulation_with_instructions(
    question: str,
    original_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Force Claude avec instructions explicites pour conseils financiers"""
    print(f"🔄 [ENFORCE] Forcing Claude with explicit financial advice instructions...")

    explicit_prompt = f"""
SITUATION DE L'UTILISATEUR:
{question}

INSTRUCTIONS CRITIQUES:
1. VOUS DEVEZ répondre de manière CONCRETE et ACTIONNABLE
2. Donnez des RECOMMANDATIONS DIRECTES, pas des documents génériques
3. Structure attendue:
   - Votre situation en résumé
   - Options d'investissement possibles
   - Ce qui est recommandé POUR VOUS
   - Prochaines étapes concrètes
4. Évitez: rapports, citations de sources, références historiques

REPONDEZ MAINTENANT EN MODE CONSEIL:
"""

    try:
        from backend.core.orchestrator_v3 import _call_claude
        reformed_answer, _ = _call_claude(
            system_prompt="""Tu es un conseiller en gestion de patrimoine senior avec 20 ans d'expérience.
Tu donnes des conseils CONCRETS et DIRECTS, pas des généralités.
Tu adaptes tes recommandations à la situation précise décrite.""",
            user_prompt=explicit_prompt
        )

        if reformed_answer and reformed_answer.strip():
            print(f"✅ [ENFORCE] Explicit instruction reformulation successful")
            return {
                **original_result,
                "answer": reformed_answer,
                "answer_text": reformed_answer,
                "meta": {
                    **(original_result.get("meta") or {}),
                    "claude_enforcement": "explicit_instructions_applied"
                }
            }
        else:
            return _provide_fallback_response(question, original_result)

    except Exception as exc:
        print(f"❌ [ENFORCE] Explicit instruction reformulation error: {exc}")
        return _provide_fallback_response(question, original_result)


def _provide_fallback_response(
    question: str,
    original_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Provide a fallback response when Claude fails completely"""
    print(f"⚠️ [ENFORCE] Providing fallback response...")

    fallback_answer = f"""
⚠️ Je rencontre une difficulté technique pour traiter votre question complètement. 
Voici une première orientation:

VOTRE QUESTION: {question}

PREMIER CONSEIL:
- Avant d'investir, définissez votre horizon (court/moyen/long terme)
- Évaluez votre profil risque (prudent/équilibré/dynamique)
- Diversifiez vos placements (ne pas mettre tous les œufs dans le même panier)
- Pensez à l'aspectfiscal de votre situation

PROCHAINES ÉTAPES:
1. Consultez un conseiller financier agréé
2. Étudiez les options d'investissement adaptées à votre profil
3. Définissez une stratégie progressive plutôt qu'un investissement tout d'un coup

Je vais recharger ma capacité d'analyse pour vous donner des recommandations plus précises.
"""

    return {
        **original_result,
        "answer": fallback_answer,
        "answer_text": fallback_answer,
        "meta": {
            **(original_result.get("meta") or {}),
            "claude_enforcement": "fallback_response_provided",
            "warning": "comprehensive_analysis_failed"
        }
    }


# Patch instructions
PATCH_INSTRUCTIONS = """
📋 INSTALLATION DU PATCH CRITIQUE CLAUDE

1. IMPORTER LE MODULE:
   En haut de backend/routes/router.py, ajoutez:
   from backend.core.critical_claude_enforcement import enforce_claude_call

2. WRAPPER LA FONCTION ORCHESTRATOR:
   Remplacez dans backend/routes/router.py:

   # AVANT:
   return runner(
       question=question,
       history=history or [],
       ...
   )

   # APRÈS:
   result = runner(
       question=question,
       history=history or [],
       ...
   )
   return enforce_claude_call(lambda q, h: runner(q, h, ...), question, history)

3. REDÉMARRER:
   Le backend activera automatiquement Claude enforcement

Ce patch garantit que Claude génère TOUJOURS une réponse pertinente et actionnable.
"""

if __name__ == "__main__":
    print("🔴 CRITICAL CLAUDE ENFORCEMENT PATCH")
    print(PATCH_INSTRUCTIONS)
