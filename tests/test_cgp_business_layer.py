from backend.core.cgp_business_layer import CGPBusinessLayer


def _n(text: str) -> str:
    return " ".join((text or "").lower().split())


def test_case_1_td_darwin_2025_number_allowed_and_short_answer():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="taux de distribution 2025 darwin re01",
        intent="FACTUAL_KPI",
        material=(
            "Le taux de distribution cible de Darwin RE01 pour 2025 est de 7,5% net de frais de gestion.\n"
            "Donnée issue de la documentation Darwin RE01."
        ),
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": [{"source": "darwin_doc"}]},
        profile_scoring={},
        context={"response_mode": "compact", "constraints": {"require_source_for_numbers": True}},
    )

    assert "7,5%" in out["business_answer"]
    assert len([ln for ln in out["business_answer"].splitlines() if ln.strip()]) <= 12
    assert out["business_warnings"] == []
    assert out["business_flags"].get("forbid_numbers") is False


def test_case_2_iroko_without_web_returns_prudent_warning():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="pga de iroko ?",
        intent="FACTUAL_KPI",
        material="Je n'ai pas de donnée vérifiée ici.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        profile_scoring={},
        context={"response_mode": "compact"},
    )

    normalized_answer = _n(out["business_answer"])
    warnings = [_n(w) for w in out["business_warnings"]]

    assert any("source_manquante_a_confirmer" in w or "source" in w for w in warnings)
    assert "a confirmer" in normalized_answer or "à confirmer" in out["business_answer"].lower()
    assert out["business_flags"].get("needs_web") is True


def test_case_3_top_scpi_without_criteria_requires_clarification():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="top 10 scpi",
        intent="COMPARISON",
        material="Classement général demandé.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        profile_scoring={},
        context={"response_mode": "compact"},
    )

    flags = out["business_flags"]
    assert flags.get("needs_clarification") is True
    assert isinstance(flags.get("clarifying_questions"), list)
    assert len(flags.get("clarifying_questions")) >= 1
    assert any("top_classement_sans_criteres" in w for w in out["business_warnings"])


def test_case_4_realtime_without_live_web_sets_estimation_mode():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="dernier TD 2026 des SCPI ?",
        intent="FACTUAL_KPI",
        material="Dernières données consolidées disponibles: T3 2025.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        profile_scoring={},
        context={
            "response_mode": "compact",
            "live_web_signal": False,
            "constraints": {"require_source_for_numbers": True},
        },
    )

    assert any("pas_de_signal_live_web" in w for w in out["business_warnings"])
    assert out["business_flags"].get("requires_estimation_mode") is True
    assert "mode estimation" in _n(out["business_answer"])


def test_case_5_r9_phrase_vague_without_calc_triggers_warning():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="analyse scpi",
        intent="FACTUAL_KPI",
        material=(
            "Cela pourrait améliorer le rendement global du portefeuille.\n"
            "Diversification patrimoniale recommandée."
        ),
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": [{"source": "doc"}]},
        profile_scoring={},
        context={"response_mode": "compact"},
    )

    assert "phrase_creuse_sans_calcul" in out["business_warnings"]
    assert out["business_flags"].get("forbid_generic_claims") is True
    assert "Je ne peux pas conclure sans démonstration chiffrée." in out["business_answer"]


def test_case_6_r9_phrase_vague_with_calc_proof_does_not_trigger():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="analyse scpi",
        intent="FACTUAL_KPI",
        material=(
            "Cela pourrait améliorer le rendement global du portefeuille.\n"
            "CALC_PROOF:\n"
            "Calcul: revenu_brut = 100000 * 0.06 = 6000"
        ),
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": [{"source": "doc"}]},
        profile_scoring={},
        context={"response_mode": "compact"},
    )

    assert "phrase_creuse_sans_calcul" not in out["business_warnings"]
    assert out["business_flags"].get("forbid_generic_claims") is not True


def test_case_7_r10_financial_mode_forces_structured_calculation_output():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="calcule une projection tri scpi sur 10 ans avec 120000 eur",
        intent="STRATEGIC_ALLOCATION",
        material="CALC_PROOF:\nProjection et net yield disponibles.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": [{"source": "doc"}]},
        profile_scoring={"selected_profile": "equilibre"},
        context={
            "response_mode": "compact",
            "cgp_calc": {
                "calc_results": {
                    "NET": {
                        "gross_yield_rate": 0.075,
                        "operating_cost_rate": 0.01,
                        "prudent_haircut_rate": 0.05,
                        "social_contrib_rate": 0.172,
                        "net_prudent_rate": 0.041,
                    },
                    "PROJECTION": {
                        "capital_initial": 120000,
                        "taux_net_annuel": 0.041,
                        "horizon_years": 10,
                        "capital_final": 179387,
                        "gain": 59387,
                    },
                    "TAX": {
                        "tmi_rate": 0.30,
                        "social_contrib_rate": 0.172,
                    },
                },
                "calc_warnings": [],
            },
        },
    )

    answer = out["business_answer"]
    assert out["business_flags"].get("mode_analyse_financiere") is True
    assert "Résultat chiffré:" in answer
    assert "1) Hypothèses" in answer
    assert "2) Calcul des flux" in answer
    assert "3) Projection" in answer
    assert "4) TRI estimé" in answer
    assert "5) Sensibilité (si applicable)" in answer
    assert "6) Conclusion stratégique basée sur les chiffres" in answer
    assert len([ln for ln in answer.splitlines() if ln.strip()]) > 12


def test_case_8_r10_missing_inputs_sets_clarification():
    layer = CGPBusinessLayer()
    out = layer.apply(
        question="projection tri scpi",
        intent="FACTUAL_KPI",
        material="CALC_PROOF:\nAucune base numérique complète.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": [{"source": "doc"}]},
        profile_scoring={},
        context={
            "response_mode": "compact",
            "cgp_calc": {
                "calc_results": {},
                "calc_warnings": ["missing_inputs"],
                "calc_has_missing_inputs": True,
            },
        },
    )

    assert out["business_flags"].get("mode_analyse_financiere") is True
    assert out["business_flags"].get("needs_clarification") is True
    assert "analyse_financiere_inputs_manquants" in out["business_warnings"]
    questions = out["business_flags"].get("clarifying_questions") or []
    assert isinstance(questions, list)
    assert 1 <= len(questions) <= 3
