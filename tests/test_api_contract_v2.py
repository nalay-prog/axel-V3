import backend.routes.api as api_module


def test_ask_endpoint_returns_dual_schema(monkeypatch):
    def _fake_router(**_kwargs):
        return {
            "answer": "td: 6,1% | période: 2025 | source: aspim.fr",
            "answer_text": "td: 6,1% | période: 2025 | source: aspim.fr",
            "answer_json": {
                "format": "KPI",
                "sections": [{"title": "REPONSE_DIRECTE", "content": "td: 6,1% | période: 2025 | source: aspim.fr"}],
                "questions_a_preciser": [],
                "sources_utilisees": [],
            },
            "answer_structured": None,
            "answer_structured_v2": {
                "intent": "KPI",
                "kpi_target": "td",
                "status": "ok",
                "items": [],
                "kpi_response": {
                    "kpi": "td",
                    "value": "6,1%",
                    "period": "2025",
                    "source_domain": "aspim.fr",
                },
                "strategy_blocks": {
                    "analyse": [],
                    "recommandation": [],
                    "risques": [],
                    "questions_manquantes": [],
                },
                "clarification_questions": [],
                "sources_used": [],
                "rendered_text": "td: 6,1% | période: 2025 | source: aspim.fr",
            },
            "answer_contract": {"intent": "KPI", "kpi_target": "td"},
            "details": "",
            "used_facts": [],
            "warnings": [],
            "sources": [],
            "sources_by_layer": {"sql_kpi": [], "rag_market": [], "rag_darwin": []},
            "agent_used": "darwin_finalizer",
            "meta": {
                "intent": "FACTUAL_KPI",
                "intent_cgp": "KPI",
                "kpi_target": "td",
                "contract_format_enforced": True,
                "contract_rewrite_engine": "deterministic_fallback",
                "top_items_received_count": 0,
                "top_items_valid_count": 0,
                "top_items_rejected_count": 0,
                "top_name_sanitizer_applied": False,
                "top_name_resolution_mode": "deterministic_fallback",
                "session_state_patch": {},
            },
        }

    monkeypatch.setattr(api_module, "ask_router", _fake_router)

    client = api_module.app.test_client()
    res = client.post(
        "/ask",
        json={
            "question": "td re01 ?",
            "session_id": "test_contract_v2",
            "history": [],
        },
    )
    assert res.status_code == 200
    data = res.get_json()

    assert "result_structured" in data
    assert "result_structured_v2" in data
    assert "result_contract" in data
    assert data["result_contract"]["intent"] == "KPI"
    assert data["result_contract"]["kpi_target"] == "td"
    assert data["result_text"] == "td: 6,1% | période: 2025 | source: aspim.fr"
    assert data["result"] == data["result_text"]
    assert data["meta"]["contract_format_enforced"] is True
    assert "top_items_received_count" in data["meta"]
    assert "top_items_valid_count" in data["meta"]
    assert "top_items_rejected_count" in data["meta"]
    assert "top_name_sanitizer_applied" in data["meta"]
    assert "top_name_resolution_mode" in data["meta"]
