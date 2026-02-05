# relationship_engine.py
from typing import Dict, Any

def apply_relationship_limits(state: Dict[str, Any]) -> Dict[str, Any]:
    rel = state.get("relationship", "warm")

    if rel == "new":
        return {"max_vulnerability": 0.20, "max_tease": 0.15, "allow_jealousy": False}

    if rel == "close":
        return {"max_vulnerability": 0.55, "max_tease": 0.70, "allow_jealousy": True}

    # warm
    return {"max_vulnerability": 0.35, "max_tease": 0.45, "allow_jealousy": False}
