from backend.darwin.finalizer import (
    CGP_INTENT_KPI,
    CGP_INTENT_STRATEGIE,
    CGP_INTENT_TOP,
    INTENT_STRATEGIC_ALLOCATION,
    _build_contract_payload_deterministic,
    _coerce_contract_payload,
    _contract_intent_from_internal,
    _detect_intent,
    _render_contract_payload_text,
    _validate_rendered_contract_text,
)


def _simple_sources() -> dict:
    return {
        "sql_kpi": [],
        "rag_market": [
            {
                "source": "ASPIM",
                "url": "https://aspim.fr/scpi",
                "date": "2025",
                "snippet": "TD moyen 2025: 6,10% sur les SCPI de rendement.",
            },
            {
                "source": "France SCPI",
                "url": "https://francescpi.com/td-2025",
                "date": "2025",
                "snippet": "SCPI A: 6,4% ; SCPI B: 6,2% ; SCPI C: 6,0%.",
            },
        ],
        "rag_darwin": [],
    }


def test_contract_kpi_complete_format():
    payload = _build_contract_payload_deterministic(
        question="td 2025 de re01 ?",
        contract_intent=CGP_INTENT_KPI,
        kpi_target="td",
        answer_draft="Le TD 2025 de RE01 est de 6,1% selon ASPIM.",
        sources_by_layer=_simple_sources(),
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_KPI, rendered)
    lines = [line for line in rendered.splitlines() if line.strip()]

    assert 1 <= len(lines) <= 3
    assert "source:" in rendered.lower()
    assert "période:" in rendered.lower()
    assert "analyse:" not in rendered.lower()
    assert "http://" not in rendered and "https://" not in rendered


def test_contract_kpi_not_found_format():
    payload = _build_contract_payload_deterministic(
        question="tof de scpi ?",
        contract_intent=CGP_INTENT_KPI,
        kpi_target="tof",
        answer_draft="Aucune valeur exploitable.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_KPI, rendered)

    assert "Non trouvé dans les sources fournies" in rendered
    assert "Analyse:" not in rendered


def test_contract_top_only_numbered_lines():
    payload = _build_contract_payload_deterministic(
        question="top 3 scpi rendement 2025",
        contract_intent=CGP_INTENT_TOP,
        kpi_target="td",
        answer_draft="Classement indicatif selon les sources.",
        sources_by_layer=_simple_sources(),
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_TOP, rendered)
    lines = [line.strip() for line in rendered.splitlines() if line.strip()]

    assert len(lines) >= 1
    assert all(line.split(" ", 1)[0].rstrip(".").isdigit() for line in lines)


def test_contract_top_incomplete_adds_questions():
    payload = _build_contract_payload_deterministic(
        question="top 10 scpi",
        contract_intent=CGP_INTENT_TOP,
        kpi_target="none",
        answer_draft="Top demandé sans critères.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    lines = [line.strip() for line in rendered.splitlines() if line.strip()]

    assert any("Question:" in line for line in lines)
    assert all(line.split(" ", 1)[0].rstrip(".").isdigit() for line in lines)


def test_contract_top_prefers_scpi_name_over_site_label():
    sources = {
        "sql_kpi": [],
        "rag_market": [
            {
                "source": "France SCPI",
                "url": "https://francescpi.com/iroko-zen",
                "date": "2026",
                "title": "SCPI Iroko Zen : rendement 2026",
                "snippet": "Iroko Zen affiche 6,2% en 2026.",
            },
            {
                "source": "La Centrale des SCPI",
                "url": "https://centraledesscpi.com/corum-xl",
                "date": "2026",
                "title": "SCPI Corum XL en 2026",
                "snippet": "Corum XL 5,8% en 2026.",
            },
        ],
        "rag_darwin": [],
    }
    payload = _build_contract_payload_deterministic(
        question="top 2 scpi rendement 2026",
        contract_intent=CGP_INTENT_TOP,
        kpi_target="td",
        answer_draft="",
        sources_by_layer=sources,
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)

    assert "Iroko" in rendered or "Iroko Zen" in rendered
    assert "Corum" in rendered or "Corum XL" in rendered
    assert "france-scpi" not in rendered.lower()
    assert "la-centrale-scpi" not in rendered.lower()


def test_contract_top_coerce_rejects_site_like_names_from_llm():
    sources = {
        "sql_kpi": [],
        "rag_market": [
            {
                "source": "France SCPI",
                "url": "https://francescpi.com/scpi/iroko-zen",
                "date": "2026",
                "title": "SCPI Iroko Zen : rendement 2026",
                "snippet": "Iroko Zen affiche 6,2% en 2026.",
            },
            {
                "source": "La Centrale des SCPI",
                "url": "https://www.centraledesscpi.com/scpi/corum-xl",
                "date": "2026",
                "title": "SCPI Corum XL : rendement 2026",
                "snippet": "Corum XL affiche 5,8% en 2026.",
            },
        ],
        "rag_darwin": [],
    }
    fallback = _build_contract_payload_deterministic(
        question="top 2 scpi rendement 2026",
        contract_intent=CGP_INTENT_TOP,
        kpi_target="td",
        answer_draft="",
        sources_by_layer=sources,
        seed_questions=[],
    )
    raw_payload = {
        "status": "ok",
        "items": [
            {"name": "france-scpi", "value": "2026", "metric": "kpi", "period": "2026", "source_domain": "francescpi.com"},
            {"name": "la-centrale-scpi", "value": "5", "metric": "kpi", "period": "2026", "source_domain": "centraledesscpi.com"},
        ],
    }

    coerced = _coerce_contract_payload(
        raw_payload=raw_payload,
        fallback_payload=fallback,
        contract_intent=CGP_INTENT_TOP,
        kpi_target="td",
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(coerced)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_TOP, rendered)

    assert "france-scpi" not in rendered.lower()
    assert "la-centrale-scpi" not in rendered.lower()
    assert "Iroko" in rendered or "Corum" in rendered or "Non trouvé dans les sources fournies" in rendered
    assert isinstance(coerced.get("top_diagnostics"), dict)
    assert int(coerced["top_diagnostics"].get("received_count", 0)) >= 2
    assert bool(coerced["top_diagnostics"].get("sanitizer_applied", False)) is True
    assert str(coerced["top_diagnostics"].get("resolution_mode") or "") in {
        "resolved_from_source",
        "llm_raw",
    }


def test_contract_top_generic_slug_does_not_create_fake_scpi_name():
    sources = {
        "sql_kpi": [],
        "rag_market": [
            {
                "source": "France SCPI",
                "url": "https://francescpi.com/scpi/questions-scpi/",
                "date": "2026",
                "title": "Questions SCPI 2026",
                "snippet": "Guide SCPI général 6,2% selon les tendances du marché.",
            }
        ],
        "rag_darwin": [],
    }
    payload = _build_contract_payload_deterministic(
        question="top scpi 2026",
        contract_intent=CGP_INTENT_TOP,
        kpi_target="td",
        answer_draft="",
        sources_by_layer=sources,
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_TOP, rendered)

    assert "questions scpi" not in rendered.lower()
    assert "france-scpi" not in rendered.lower()
    assert "Non trouvé dans les sources fournies" in rendered


def test_contract_strategie_blocks_and_questions():
    payload = _build_contract_payload_deterministic(
        question="fais une stratégie SCPI",
        contract_intent=CGP_INTENT_STRATEGIE,
        kpi_target="none",
        answer_draft="Allocation progressive, prudence sur la liquidité et la fiscalité.",
        sources_by_layer={"sql_kpi": [], "rag_market": [], "rag_darwin": []},
        seed_questions=[],
    )
    rendered = _render_contract_payload_text(payload)
    rendered, _warnings = _validate_rendered_contract_text(CGP_INTENT_STRATEGIE, rendered)

    assert "Analyse:" in rendered
    assert "Recommandation:" in rendered
    assert "Risques:" in rendered
    assert "Questions manquantes:" in rendered
    assert "Projection / chiffres" not in rendered
    assert "Arbitrages:" not in rendered
    assert "Conclusion:" not in rendered


def test_intent_priority_forces_strategy_with_amount_horizon_objective():
    question = "J'ai 250k, TMI 41%, objectif revenus, horizon 15 ans, optimise-moi une allocation SCPI."
    internal_intent = _detect_intent(question, neutral_pure=False, history=[])
    assert internal_intent == INTENT_STRATEGIC_ALLOCATION
    contract_intent = _contract_intent_from_internal(question=question, intent=internal_intent)
    assert contract_intent == CGP_INTENT_STRATEGIE
