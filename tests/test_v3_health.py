import backend.agents.agent_core as core_module
import backend.core.orchestrator_v3 as orchestrator_v3_module
import backend.routes.api as api_module


def _fake_router(**kwargs):
    _fake_router.last_kwargs = kwargs
    return {
        "answer": "Réponse v3 test",
        "answer_text": "Réponse v3 test",
        "answer_json": {
            "format": "simple_v3",
            "intent": "INFO",
            "answer": "Réponse v3 test",
            "sources": [],
        },
        "answer_structured": None,
        "answer_structured_v2": {
            "intent": "INFO",
            "kpi_target": "none",
            "status": "ok",
            "items": [],
            "kpi_response": {
                "kpi": "none",
                "value": "Non trouvé",
                "period": "Non trouvé",
                "source_domain": "n/a",
            },
            "strategy_blocks": {
                "analyse": [],
                "recommandation": [],
                "risques": [],
                "questions_manquantes": [],
            },
            "clarification_questions": [],
            "sources_used": [],
            "rendered_text": "Réponse v3 test",
        },
        "answer_contract": {"intent": "INFO", "kpi_target": "none"},
        "details": "",
        "used_facts": [],
        "warnings": [],
        "sources": [],
        "sources_by_layer": {},
        "agent_used": "darwin_v3_brain",
        "meta": {
            "intent": "INFO",
            "intent_cgp": "INFO",
            "kpi_target": "none",
            "session_state_patch": {},
        },
    }


def test_api_defaults_to_v3(monkeypatch):
    monkeypatch.setattr(api_module, "ask_router", _fake_router)
    monkeypatch.setattr(api_module, "append_turn", lambda **_kwargs: None)
    monkeypatch.setattr(
        api_module,
        "append_ask_log",
        lambda **_kwargs: {"replay_id": "replay_test_v3", "created_at": "2026-03-02T00:00:00"},
    )
    monkeypatch.setattr(api_module, "get_history", lambda _session_id, max_messages=20: [])
    monkeypatch.setattr(api_module, "get_session_state", lambda _session_id: {})

    client = api_module.app.test_client()
    res = client.post(
        "/ask",
        json={
            "question": "Top 3 SCPI 2026 rendement",
            "session_id": "test_v3_default",
        },
    )

    assert res.status_code == 200
    assert _fake_router.last_kwargs["darwin_version"] == "v3"


def test_agent_core_retrieve_raw_uses_local_documents():
    out = core_module.retrieve_raw("structure de frais Darwin RE01", history=[], k=3)

    assert out["meta"]["mode"] == "raw_local_retriever"
    assert out["meta"]["rows_count"] >= 1
    assert len(out["results"]) >= 1
    assert any("Darwin" in str(item["metadata"].get("title", "")) for item in out["results"])


def test_orchestrate_v3_health_without_network(monkeypatch):
    def _fake_web(_question: str, max_results: int = 6):
        return {
            "results": [
                {
                    "title": "Classement SCPI 2026",
                    "href": "https://aspim.fr/scpi-2026",
                    "body": "TD moyen 2026 estimé à 6,1% selon ASPIM.",
                    "date": "2026",
                    "priority": "1",
                }
            ],
            "meta": {
                "tool": "web",
                "provider": "fake",
                "rows_count": 1,
            },
        }

    def _fake_claude(system_prompt: str, user_prompt: str):
        assert "PACK DE PREUVES" in user_prompt
        assert "INTENTION" in user_prompt
        return (
            "Réponse directe :\n- Top indicatif 2026 basé sur ASPIM.\n- TD moyen observé: 6,1%.\n- Source: aspim.fr",
            {
                "provider_requested": "anthropic",
                "provider_effective": "anthropic_mock",
                "model": "claude-sonnet-4-6",
            },
        )

    monkeypatch.setattr(orchestrator_v3_module, "_retrieve_web", _fake_web)
    monkeypatch.setattr(orchestrator_v3_module, "_call_claude", _fake_claude)

    out = orchestrator_v3_module.orchestrate_v3(
        question="Top 3 SCPI 2026 rendement",
        history=[],
        force_agent="web",
    )

    assert out["agent_used"] == "darwin_v3_brain"
    assert out["meta"]["darwin_version"] == "v3"
    assert out["meta"]["selected_agent"] == "web"
    assert out["answer_structured_v2"]["rendered_text"]
    assert out["answer_contract"]["intent"] == "TOP"
    assert len(out["sources"]) >= 1
