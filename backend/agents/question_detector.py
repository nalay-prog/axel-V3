import re
from dataclasses import dataclass
from typing import List, Set


@dataclass
class QuestionAnalysis:
    type: str
    confidence: float
    keywords_matched: List[str]
    numerical_values: List[str]
    requires_context: bool
    detected_labels: List[str]


class QuestionDetector:
    CALCUL_KEYWORDS = [
        "combien",
        "calcul",
        "projection",
        "rendement",
        "mensualite",
        "mensualité",
        "taux",
        "%",
        "tri",
        "irr",
        "cash flow",
        "cashflow",
    ]
    INFO_KEYWORDS = [
        "definition",
        "définition",
        "c'est quoi",
        "qu'est-ce",
        "explique",
        "compar",
        "difference",
        "différence",
        "top",
        "liste",
        "classement",
    ]
    STRATEGIE_KEYWORDS = [
        "optimisation",
        "allocation",
        "fiscalite",
        "fiscalité",
        "investissement",
        "investir",
        "que faire",
        "recommander",
        "strategie",
        "stratégie",
        "arbitrage",
        "patrimoine",
    ]
    CONTEXT_SENSITIVE_KEYWORDS = [
        "top",
        "classement",
        "liste",
        "rendement",
        "fiscalite",
        "fiscalité",
        "allocation",
        "scpi",
    ]

    def analyze(self, query: str) -> QuestionAnalysis:
        text = (query or "").strip()
        normalized = text.lower()
        numbers = re.findall(r"\d+(?:[.,]\d+)?", text)

        labels: Set[str] = set()
        matched: List[str] = []

        for kw in self.CALCUL_KEYWORDS:
            if kw in normalized:
                labels.add("CALCUL")
                matched.append(kw)
        for kw in self.INFO_KEYWORDS:
            if kw in normalized:
                labels.add("INFO")
                matched.append(kw)
        for kw in self.STRATEGIE_KEYWORDS:
            if kw in normalized:
                labels.add("STRATEGIE_CGP")
                matched.append(kw)

        has_rank_info = any(
            kw in normalized
            for kw in ["top", "classement", "liste", "palmares", "compar", "meilleure", "meilleur"]
        )
        has_strong_calc_signal = any(
            kw in normalized
            for kw in ["combien", "calcul", "projection", "mensualite", "mensualité", "tri", "irr", "cash flow", "cashflow", "%"]
        )

        if not labels:
            labels.add("INFO")

        if "CALCUL" in labels and "STRATEGIE_CGP" in labels:
            q_type = "mixte_calcul_strategie"
        elif "CALCUL" in labels and "INFO" in labels and has_rank_info and not has_strong_calc_signal:
            # ex: "top 10 scpi rendement" => surtout demande d'information/comparatif.
            q_type = "info"
        elif "STRATEGIE_CGP" in labels:
            q_type = "strategie_cgp"
        elif "CALCUL" in labels:
            q_type = "calcul"
        else:
            q_type = "info"

        requires_context = any(kw in normalized for kw in self.CONTEXT_SENSITIVE_KEYWORDS)
        confidence = 0.6 + min(0.35, 0.05 * len(labels) + 0.02 * len(numbers))
        confidence = max(0.55, min(0.95, confidence))

        return QuestionAnalysis(
            type=q_type,
            confidence=confidence,
            keywords_matched=matched[:8],
            numerical_values=numbers[:8],
            requires_context=requires_context,
            detected_labels=sorted(labels),
        )


detector = QuestionDetector()
