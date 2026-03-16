from backend.core.cgp_strategic_layer import (
    INTENT_DARWIN_SPECIFIC,
    INTENT_STRATEGIC_ALLOCATION,
    decide,
)


def test_decide_darwin_kpi_with_period_routes_web_core_sql():
    out = decide(
        question="taux de distribution 2025 darwin re01",
        history=[],
        intent=None,
        flags={"prefer_live_web_default": True},
    )

    assert out.intent_refined == INTENT_DARWIN_SPECIFIC
    assert out.needs_clarification is False
    assert out.routing["agents_order"][:3] == ["online", "core", "sql_kpi"]
    assert out.constraints["require_source_for_numbers"] is True


def test_decide_pga_iroko_asks_period_and_web_first():
    out = decide(
        question="pga de iroko ?",
        history=[],
        intent=None,
        flags={"prefer_live_web_default": True},
    )

    assert out.needs_clarification is True
    assert any("periode" in q.lower() or "période" in q.lower() for q in out.clarifying_questions)
    assert out.routing["agents_order"][0] == "online"


def test_decide_top10_scpi_requires_scope():
    out = decide(
        question="top 10 scpi",
        history=[],
        intent=None,
        flags={"prefer_live_web_default": True},
    )

    assert out.needs_clarification is True
    assert any("top" in rule.lower() for rule in out.business_rules_triggered)


def test_decide_allocation_missing_risk_profile():
    out = decide(
        question="fais une allocation SCPI 120k IR horizon 10 ans",
        history=[],
        intent=None,
        flags={"prefer_live_web_default": True},
    )

    assert out.intent_refined == INTENT_STRATEGIC_ALLOCATION
    assert out.needs_clarification is True
    assert any("profil" in q.lower() for q in out.clarifying_questions)
