# backend/routes/router.py
from typing import Dict, Any, Optional, List

from ..darwin.finalizer import orchestrate, orchestrate_v2
from ..core.orchestrator_v3 import orchestrate_v3


def ask_router(
    question: str,
    history: Optional[List[dict]] = None,
    force_agent: Optional[str] = None,
    neutral_pure: Optional[bool] = None,
    audit_detail: Optional[bool] = None,
    portfolio_simulation: Optional[Dict[str, Any]] = None,
    scoring_version: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
    darwin_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Point d'entrée routeur:
    délègue la décision et l'orchestration au Darwin Finalizer.
    """
    requested_version = str(darwin_version or "").strip().lower()
    if requested_version == "v3":
        runner = orchestrate_v3
    elif requested_version == "v2":
        runner = orchestrate_v2
    else:
        runner = orchestrate

    return runner(
        question=question,
        history=history or [],
        force_agent=force_agent,
        neutral_pure=neutral_pure,
        audit_detail=audit_detail,
        portfolio_simulation_input=portfolio_simulation,
        scoring_version=scoring_version,
        session_state=session_state or {},
    )
