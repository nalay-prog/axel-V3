from backend.core.synthesis_agent import synthesize_answer


def test_synthesize_answer_with_available_facts():
    question = "taux de distribution 2025 darwin re01 ?"
    material = """
RAG DARWIN:
Le taux de distribution cible de Darwin RE01 pour 2025 est de 7,5% net de frais de gestion.
La duree de placement recommandee est de 8 ans.
SOURCES WEB DISPONIBLES:
- https://example.com/darwin-re01
"""

    out = synthesize_answer(question=question, material=material, mode="compact")

    assert isinstance(out, dict)
    assert "7,5%" in out["answer"] or "7.5%" in out["answer"]
    assert len(out["used_facts"]) >= 1
    assert not any("non trouv" in w.lower() for w in out["warnings"])
    assert out["details"]


def test_synthesize_answer_missing_info_no_hallucination():
    question = "pga de iroko ?"
    material = """
SCORING DETERMINISTE:
- moteur: deterministic_profile_scoring/1.2
MODE AUDIT / DETAIL
PONDERATIONS
DATES
"""

    out = synthesize_answer(question=question, material=material, mode="compact")

    assert isinstance(out, dict)
    assert (
        "pas trouvé" in out["answer"].lower()
        or "pas trouve" in out["answer"].lower()
        or "non trouvé" in out["answer"].lower()
        or "non trouve" in out["answer"].lower()
    )
    assert any("non trouv" in w.lower() for w in out["warnings"])
    assert out["used_facts"] == []
    assert "7,5%" not in out["answer"]
