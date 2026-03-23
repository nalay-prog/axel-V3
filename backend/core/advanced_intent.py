# advanced_intent.py - Détection d'Intention Avancée
# Remplace simple_intent.py avec plus de précision et de catégories

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION ÉLARGIE DES INTENTIONS
# ════════════════════════════════════════════════════════════════════════════

# Catégories d'intention enrichies
INTENT_CATEGORIES = {
    "KPI_SPECIFIC": {
        "terms": ["td", "taux de distribution", "rendement", "frais", "collecte", "capitalisation", "walt", "vacance", "tof"],
        "description": "Demande de donnée chiffrée spécifique",
        "response_style": "direct_numerical"
    },

    "KPI_COMPARISON": {
        "terms": ["comparer", "vs", "contre", "différence", "meilleur", "pire", "classement"],
        "description": "Comparaison entre plusieurs éléments",
        "response_style": "comparative_analysis"
    },

    "TREND_ANALYSIS": {
        "terms": ["tendance", "évolution", "historique", "progression", "changement", "développement"],
        "description": "Analyse des tendances temporelles",
        "response_style": "temporal_analysis"
    },

    "RISK_ASSESSMENT": {
        "terms": ["risque", "volatilité", "sécurité", "garantie", "fiabilité", "sécurisé"],
        "description": "Évaluation des risques",
        "response_style": "risk_focused"
    },

    "MARKET_TIMING": {
        "terms": ["moment", "timing", "opportunité", "conjoncture", "marché actuel", "bon moment"],
        "description": "Questions sur le timing du marché",
        "response_style": "market_context"
    },

    "PORTFOLIO_ADVICE": {
        "terms": ["allocation", "répartition", "diversification", "portefeuille", "stratégie d'investissement"],
        "description": "Conseils de constitution de portefeuille",
        "response_style": "strategic_advice"
    },

    "TAX_OPTIMIZATION": {
        "terms": ["fiscal", "impôt", "avantage fiscal", "déduction", "optimisation fiscale"],
        "description": "Aspects fiscaux et optimisation",
        "response_style": "tax_focused"
    },

    "REGULATORY_INFO": {
        "terms": ["réglementation", "autorisation", "conformité", "légale", "autorité"],
        "description": "Informations réglementaires",
        "response_style": "regulatory_info"
    },

    "COMPETITOR_ANALYSIS": {
        "terms": ["concurrent", "marché", "positionnement", "leadership", "part de marché"],
        "description": "Analyse concurrentielle",
        "response_style": "market_analysis"
    },

    "SCENARIO_SIMULATION": {
        "terms": ["si", "dans le cas où", "hypothèse", "projection", "estimation", "prévision"],
        "description": "Simulation de scénarios",
        "response_style": "scenario_based"
    },

    "DARWIN_SPECIFIC": {
        "terms": ["darwin", "re01", "darwin invest", "offre darwin", "produit darwin", "frais darwin"],
        "description": "Questions spécifiques à Darwin",
        "response_style": "product_focused"
    }
}

# Contextes temporels
TIME_CONTEXTS = {
    "HISTORICAL": {
        "terms": ["historique", "depuis", "évolution", "passé", "précédent"],
        "weight": 1.0
    },
    "CURRENT": {
        "terms": ["actuel", "maintenant", "aujourd'hui", "présent", "en cours"],
        "weight": 1.0
    },
    "FUTURE": {
        "terms": ["projection", "prévision", "futur", "2026", "2027", "prochain"],
        "weight": 1.0
    },
    "COMPARATIVE": {
        "terms": ["comparé à", "vs", "contre", "différence", "par rapport à"],
        "weight": 0.8
    }
}

# Niveaux de complexité
COMPLEXITY_LEVELS = {
    "SIMPLE": {
        "max_terms": 3,
        "response_length": "short",
        "detail_level": "basic"
    },
    "MODERATE": {
        "max_terms": 6,
        "response_length": "medium",
        "detail_level": "detailed"
    },
    "COMPLEX": {
        "max_terms": 10,
        "response_length": "long",
        "detail_level": "comprehensive"
    },
    "EXPERT": {
        "max_terms": 15,
        "response_length": "extended",
        "detail_level": "technical"
    }
}

# ════════════════════════════════════════════════════════════════════════════
# FONCTIONS UTILITAIRES
# ════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalisation avancée du texte"""
    base = unicodedata.normalize("NFKD", text or "")
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower().strip()
    return re.sub(r"\s+", " ", base)


def _extract_keywords(question: str, max_items: int = 8) -> List[str]:
    """Extraction intelligente de mots-clés"""
    tokens = re.findall(r"[a-zàâäéèêëïîôöùûüÿç]{3,}", _normalize(question))
    keywords = []

    # Filtrage des stopwords enrichis
    stopwords = {
        "le", "la", "les", "de", "du", "des", "et", "ou", "un", "une", "sur", "pour",
        "dans", "que", "quel", "quelle", "quels", "quelles", "avec", "sans", "par",
        "est", "sont", "être", "faire", "plus", "moins", "très", "bien", "mal",
        "comment", "pourquoi", "quand", "où", "qui", "quoi", "combien"
    }

    for token in tokens:
        if token not in stopwords and len(token) >= 3:
            keywords.append(token)
            if len(keywords) >= max_items:
                break

    return keywords


def _calculate_term_matches(text: str, term_list: List[str]) -> Tuple[int, List[str]]:
    """Calcule les correspondances de termes avec scoring"""
    normalized_text = _normalize(text)
    matches = []
    score = 0

    for term in term_list:
        normalized_term = _normalize(term)
        if normalized_term in normalized_text:
            matches.append(term)
            # Score basé sur la longueur du terme (termes plus spécifiques = score plus élevé)
            score += len(normalized_term.split()) * 2

    return score, matches


def _detect_time_context(question: str) -> Optional[str]:
    """Détection du contexte temporel"""
    question_norm = _normalize(question)

    for context, config in TIME_CONTEXTS.items():
        score, _ = _calculate_term_matches(question_norm, config["terms"])
        if score > 0:
            return context

    return None


def _detect_complexity(keywords: List[str]) -> str:
    """Détection du niveau de complexité"""
    term_count = len(keywords)

    if term_count <= COMPLEXITY_LEVELS["SIMPLE"]["max_terms"]:
        return "SIMPLE"
    elif term_count <= COMPLEXITY_LEVELS["MODERATE"]["max_terms"]:
        return "MODERATE"
    elif term_count <= COMPLEXITY_LEVELS["COMPLEX"]["max_terms"]:
        return "COMPLEX"
    else:
        return "EXPERT"


def _extract_year(question: str) -> Optional[str]:
    """Extraction d'années dans la question"""
    match = re.search(r"\b(20\d{2})\b", question)
    if match:
        year = match.group(1)
        current_year = datetime.now().year
        # Validation : année entre 2010 et current_year + 5
        if 2010 <= int(year) <= current_year + 5:
            return year
    return None


def _extract_kpi_target(question: str) -> str:
    """Extraction intelligente de la cible KPI"""
    question_norm = _normalize(question)

    # Mapping enrichi des KPIs
    kpi_mappings = {
        "td": ["taux de distribution", "td ", "distribution"],
        "rendement": ["rendement", "performance", "retour"],
        "frais": ["frais", "commission", "honoraire"],
        "collecte": ["collecte", "levée de fonds", "souscription"],
        "capitalisation": ["capitalisation", "capital", "actif"],
        "walt": ["walt", "durée de détention"],
        "vacance": ["vacance", "taux de vacance", "disponibilité"],
        "tof": ["tof", "turnover", "rotation"]
    }

    for kpi_key, terms in kpi_mappings.items():
        score, matches = _calculate_term_matches(question_norm, terms)
        if score > 0:
            return kpi_key

    return "none"


def _detect_primary_intent(question: str) -> Tuple[str, float, Dict[str, Any]]:
    """Détection de l'intention principale avec scoring avancé"""
    question_norm = _normalize(question)
    intent_scores = {}

    # Calcul des scores pour chaque catégorie d'intention
    for intent_name, config in INTENT_CATEGORIES.items():
        score, matches = _calculate_term_matches(question_norm, config["terms"])

        # Bonus pour les termes spécifiques Darwin
        if intent_name == "DARWIN_SPECIFIC" and score > 0:
            score *= 1.5

        # Pénalité pour les intentions trop génériques
        if intent_name in ["KPI_SPECIFIC", "KPI_COMPARISON"] and len(matches) < 2:
            score *= 0.8

        intent_scores[intent_name] = {
            "score": score,
            "matches": matches,
            "description": config["description"],
            "response_style": config["response_style"]
        }

    # Sélection de l'intention principale
    if intent_scores:
        primary_intent = max(intent_scores.items(), key=lambda x: x[1]["score"])
        intent_name, intent_data = primary_intent

        # Seuil minimum de confiance
        if intent_data["score"] >= 2:  # Au moins 2 points de score
            return intent_name, intent_data["score"], intent_data

    # Fallback vers une intention générique
    return "GENERAL_INFO", 0.0, {
        "score": 0,
        "matches": [],
        "description": "Question générale",
        "response_style": "general_info"
    }


def _generate_clarification_questions(question: str, intent_data: Dict[str, Any], keywords: List[str]) -> List[str]:
    """Génération intelligente de questions de clarification"""
    questions = []

    # Si score d'intention faible, demander plus de contexte
    if intent_data.get("score", 0) < 3:
        if not keywords:
            questions.append("Pouvez-vous préciser le sujet de votre question ?")
        elif len(keywords) <= 2:
            questions.append("Pouvez-vous donner plus de contexte ou préciser votre demande ?")

    # Questions spécifiques selon l'intention détectée
    intent_type = intent_data.get("response_style", "")

    if intent_type == "comparative_analysis" and "vs" not in question.lower():
        questions.append("Quels éléments souhaitez-vous comparer précisément ?")

    elif intent_type == "temporal_analysis" and not _extract_year(question):
        questions.append("Sur quelle période souhaitez-vous analyser l'évolution ?")

    elif intent_type == "scenario_based" and "si " not in question.lower():
        questions.append("Quel scénario ou hypothèse souhaitez-vous explorer ?")

    # Limiter à 2 questions maximum
    return questions[:2]


# ════════════════════════════════════════════════════════════════════════════
# FONCTION PRINCIPALE
# ════════════════════════════════════════════════════════════════════════════

def detect_intent(question: str, history: Optional[List[dict]] = None, neutral_pure: bool = False) -> Dict[str, Any]:
    """
    Détection d'intention avancée avec analyse contextuelle

    Args:
        question: La question de l'utilisateur
        history: Historique de conversation (optionnel)
        neutral_pure: Mode neutre (pas de biais)

    Returns:
        Dictionnaire enrichi avec analyse détaillée
    """
    if not question or not question.strip():
        return {
            "type": "EMPTY",
            "confidence": 0.0,
            "keywords": [],
            "kpi_target": "none",
            "year": None,
            "time_context": None,
            "complexity": "SIMPLE",
            "needs_clarification": True,
            "clarification_questions": ["Pouvez-vous poser une question ?"],
            "is_darwin_specific": False,
            "response_style": "general_info"
        }

    # Analyse de base
    keywords = _extract_keywords(question)
    primary_intent, confidence_score, intent_data = _detect_primary_intent(question)

    # Analyses complémentaires
    time_context = _detect_time_context(question)
    year = _extract_year(question)
    kpi_target = _extract_kpi_target(question)
    complexity = _detect_complexity(keywords)

    # Détection Darwin spécifique
    darwin_score, _ = _calculate_term_matches(question, INTENT_CATEGORIES["DARWIN_SPECIFIC"]["terms"])
    is_darwin_specific = darwin_score > 0

    # Génération des questions de clarification
    clarification_questions = _generate_clarification_questions(question, intent_data, keywords)
    needs_clarification = len(clarification_questions) > 0

    # Analyse de l'historique si disponible
    conversation_context = "none"
    if history and len(history) > 0:
        recent_questions = [msg.get("content", "") for msg in history[-3:] if msg.get("role") == "user"]
        if recent_questions:
            # Détection de continuité thématique
            recent_keywords = []
            for q in recent_questions:
                recent_keywords.extend(_extract_keywords(q))

            # Si beaucoup de mots-clés communs, c'est une conversation continue
            common_keywords = set(keywords) & set(recent_keywords)
            if len(common_keywords) >= 2:
                conversation_context = "continuing_discussion"

    # Construction du résultat enrichi
    result = {
        "type": primary_intent,
        "confidence": min(confidence_score / 10.0, 1.0),  # Normalisation 0-1
        "keywords": keywords,
        "kpi_target": kpi_target,
        "year": year,
        "time_context": time_context,
        "complexity": complexity,
        "needs_clarification": needs_clarification,
        "clarification_questions": clarification_questions,
        "is_darwin_specific": is_darwin_specific,
        "response_style": intent_data.get("response_style", "general_info"),
        "conversation_context": conversation_context,
        "intent_description": intent_data.get("description", "Question générale"),
        "matched_terms": intent_data.get("matches", []),
        "analysis_metadata": {
            "total_keywords": len(keywords),
            "intent_score_raw": confidence_score,
            "has_temporal_element": time_context is not None,
            "has_numerical_element": bool(re.search(r'\d', question)),
            "question_length": len(question.split())
        }
    }

    return result


# ════════════════════════════════════════════════════════════════════════════
# FONCTIONS DE COMPATIBILITÉ (pour migration progressive)
# ════════════════════════════════════════════════════════════════════════════

def detect_intent_legacy(question: str, history: Optional[List[dict]] = None) -> Dict[str, Any]:
    """
    Version legacy pour compatibilité avec l'ancien système
    """
    advanced_result = detect_intent(question, history)

    # Mapping vers l'ancien format
    legacy_mapping = {
        "KPI_SPECIFIC": "KPI",
        "KPI_COMPARISON": "TOP",
        "TREND_ANALYSIS": "INFO",
        "RISK_ASSESSMENT": "STRATEGIE",
        "MARKET_TIMING": "STRATEGIE",
        "PORTFOLIO_ADVICE": "STRATEGIE",
        "TAX_OPTIMIZATION": "INFO",
        "REGULATORY_INFO": "INFO",
        "COMPETITOR_ANALYSIS": "TOP",
        "SCENARIO_SIMULATION": "STRATEGIE",
        "DARWIN_SPECIFIC": "DARWIN"
    }

    legacy_type = legacy_mapping.get(advanced_result["type"], "INFO")

    return {
        "type": legacy_type,
        "kpi_target": advanced_result["kpi_target"],
        "keywords": advanced_result["keywords"],
        "year": advanced_result["year"],
        "needs_clarification": advanced_result["needs_clarification"],
        "clarification_questions": advanced_result["clarification_questions"],
        "is_darwin_specific": advanced_result["is_darwin_specific"]
    }


# ════════════════════════════════════════════════════════════════════════════
# TESTS ET VALIDATION
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Tests de validation
    test_questions = [
        "Quel est le taux de distribution de Darwin RE01 ?",
        "Comparer les rendements de SCPI en 2025",
        "Quelle est l'évolution des frais de Darwin depuis 2020 ?",
        "Est-ce un bon moment pour investir dans les SCPI ?",
        "Comment optimiser fiscalement un investissement SCPI ?",
        "Quels sont les risques des SCPI ?",
        "Darwin RE01 vs autres SCPI du marché",
        "Si les taux remontent, que se passe-t-il ?"
    ]

    print("🧪 TESTS DE VALIDATION - INTENT DETECTION AVANCÉE")
    print("=" * 60)

    for i, question in enumerate(test_questions, 1):
        print(f"\n[Question {i}] {question}")
        result = detect_intent(question)

        print(f"  Intent: {result['type']} (confiance: {result['confidence']:.2f})")
        print(f"  Style: {result['response_style']}")
        print(f"  Keywords: {', '.join(result['keywords'][:5])}")
        print(f"  KPI Target: {result['kpi_target']}")
        print(f"  Time Context: {result['time_context']}")
        print(f"  Complexity: {result['complexity']}")
        if result['needs_clarification']:
            print(f"  Clarification: {result['clarification_questions']}")

    print("\n" + "=" * 60)
    print("✅ Tests terminés - Intent detection avancée opérationnelle!")