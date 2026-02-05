# emotion_engine.py
import re
import time
from typing import Dict, Any

SAD_WORDS = {"sad", "tired", "lonely", "depressed", "cry", "hurt", "stress", "stressed", "down", "broken"}
ANGRY_WORDS = {"angry", "mad", "annoyed", "pissed", "hate"}
SWEET_WORDS = {"miss", "missed", "love", "baby", "babe", "sweet", "honey", "darling", "cute"}
ANX_WORDS = {"anxious", "anxiety", "worried", "scared", "panic", "overthinking"}

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _default_mood() -> Dict[str, float]:
    return {
        "warmth": 0.55,
        "playful": 0.25,
        "calm": 0.55,
        "confidence": 0.45,
        "vulnerability": 0.15,
        "irritation": 0.05,
        "anxiety": 0.08,
        "fatigue": 0.08,
        "jealousy": 0.00,
    }

def infer_emotion(user_text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    raw = (user_text or "").strip()
    t = raw.lower()
    words = set(re.findall(r"[a-z']+", t))

    # Intent + tension (simple heuristic; swap to LLM later if you want)
    intent = "info" if "?" in raw else "banter"
    tension = 0.15

    delta = {k: 0.0 for k in _default_mood().keys()}

    if words & SAD_WORDS:
        intent = "support"
        tension = 0.25
        delta["warmth"] += 0.20
        delta["vulnerability"] += 0.15
        delta["playful"] -= 0.10
        delta["calm"] += 0.05

    if words & ANGRY_WORDS:
        intent = "tension"
        tension = 0.70
        delta["irritation"] += 0.25
        delta["calm"] -= 0.20
        delta["playful"] -= 0.15

    if words & ANX_WORDS:
        intent = "support"
        tension = max(tension, 0.45)
        delta["anxiety"] += 0.20
        delta["calm"] -= 0.10
        delta["warmth"] += 0.10

    if words & SWEET_WORDS:
        intent = "affection"
        tension = min(tension, 0.20)
        delta["warmth"] += 0.15
        delta["playful"] += 0.10

    # Energy hint (affects speed + emoji budget)
    energy = 0.45 + min(0.25, raw.count("!") * 0.06) + (0.10 if len(raw) <= 7 else 0.0)
    energy = _clamp01(energy)

    # Mode hint used only when mode is AUTO and mood not locked
    if intent == "support":
        mode_hint = "soft"
    elif intent == "tension":
        mode_hint = "serious"
    elif intent == "affection":
        mode_hint = "romantic"
    elif "?" in raw:
        mode_hint = "curious"
    else:
        mode_hint = "playful"

    return {
        "intent": intent,
        "tension": tension,
        "energy": energy,
        "delta": delta,
        "mode_hint": mode_hint,
        "ts": time.time(),
    }

def update_mood_vector(state: Dict[str, Any], signal: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("mood_vector") is None:
        state["mood_vector"] = _default_mood()

    # If locked, keep current mood (loop tracking still happens in safety_engine)
    if state.get("mood_locked", False):
        return state

    mood = dict(state["mood_vector"])
    delta = signal.get("delta", {})

    # Emotional sensitivity controls how strongly we move
    sensitivity = float(state.get("emotional_sensitivity", 50)) / 100.0  # 0..1
    alpha = 0.18 + 0.22 * sensitivity  # 0.18..0.40

    # Safety clamp: when de-escalating, reduce volatility
    if safety.get("mode") in {"deescalate", "boundary"}:
        alpha *= 0.6

    # Apply smoothing update
    for k, v in mood.items():
        target = _clamp01(v + float(delta.get(k, 0.0)))
        mood[k] = _clamp01((1.0 - alpha) * v + alpha * target)

    # Decay toward baseline slowly
    base = _default_mood()
    decay = 0.02  # per message; can be time-based later
    for k in mood.keys():
        mood[k] = _clamp01(mood[k] * (1.0 - decay) + base[k] * decay)

    # Optional: disable certain emotions
    disabled = set(state.get("disabled_emotions", []) or [])
    if "jealousy" in disabled:
        mood["jealousy"] = 0.0

    state["mood_vector"] = mood
    return state
