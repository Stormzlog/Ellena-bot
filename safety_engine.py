# safety_engine.py
import re
from typing import Dict, Any

# Same list as main.py (keep consistent)
EXPLICIT_WORDS = {"fuck", "pussy", "dick", "blowjob", "cum", "nude", "naked", "sex"}

# Optional: extra harassment / aggressive slurs gate (keep small + generic)
HARASS_WORDS = {"bitch", "slut", "whore"}  # keep minimal; you can expand later


def _has_word(text: str, wordset) -> bool:
    raw = (text or "").lower()
    for w in wordset:
        if re.search(rf"\b{re.escape(w)}\b", raw):
            return True
    return False


def evaluate_safety(user_text: str, state: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns safety directives that control:
      - reply pace (fast/normal/slow)
      - whether to remove teasing / reduce emotional intensity
      - whether to force concise replies
      - a 'mode' that the rest of the system can react to

    Also updates state["negative_loop_score"] in-place.
    """
    raw = (user_text or "").strip()
    t = raw.lower()

    tension = float(signal.get("tension", 0.15))
    rel = state.get("relationship", "warm")
    flirt = bool(state.get("flirt", True))

    # -------------------------
    # Negative loop detection
    # -------------------------
    loop = int(state.get("negative_loop_score", 0) or 0)

    # Up/down logic: if tension is sustained, raise loop score
    if tension >= 0.75:
        loop = min(10, loop + 2)
    elif tension >= 0.45:
        loop = min(10, loop + 1)
    else:
        loop = max(0, loop - 1)

    state["negative_loop_score"] = loop

    # -------------------------
    # Content gates
    # -------------------------
    has_explicit = _has_word(t, EXPLICIT_WORDS)
    has_harass = _has_word(t, HARASS_WORDS)

    # -------------------------
    # Decide safety mode
    # -------------------------
    mode = "normal"

    # Harassment → boundary mode immediately
    if has_harass:
        mode = "boundary"

    # Explicit content → boundary if flirt off or relationship too early
    elif has_explicit and (not flirt or rel == "new"):
        mode = "boundary"

    # High tension or rising loop → deescalate
    elif loop >= 6 or tension >= 0.80:
        mode = "deescalate"

    # -------------------------
    # Output directives
    # -------------------------
    # Pace: tension/sad → slower, joy/energy → faster
    energy = float(signal.get("energy", 0.45))

    if mode in {"boundary", "deescalate"}:
        pace = "slow"
    else:
        pace = "fast" if energy >= 0.72 else "normal"

    # Reduce teasing in tense situations
    no_teasing = mode in {"boundary", "deescalate"} or tension >= 0.65

    # Keep it concise when tense/boundary
    force_concise = mode in {"boundary", "deescalate"}

    # Optional: if boundary triggered by explicit, you can set a stock reply later.
    # For now we only provide directives; your generator already has explicit handling.
    return {
        "mode": mode,                 # normal|deescalate|boundary
        "pace": pace,                 # fast|normal|slow
        "no_teasing": no_teasing,     # remove 😏 etc.
        "force_concise": force_concise,
}
