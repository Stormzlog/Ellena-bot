# memory.py
import sqlite3
import json
import time
from typing import Dict, Any, List, Optional

DB_PATH = "memory.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def _now() -> float:
    return time.time()


def ensure_schema():
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        first_seen REAL,
        last_seen REAL,
        interaction_count INTEGER,
        user_state TEXT,
        topic_weights TEXT,
        summary TEXT
    )
    """)
    conn.commit()


# -------------------------
# Defaults
# -------------------------
DEFAULT_USER_STATE = {
    "recent_events": [],     # list of {ts, label, intent, outcome, note}
    "last_summary_ts": 0.0,  # when summary was last refreshed
    "last_user_intent": None,
    "last_user_emotion": None,
}

DEFAULT_TOPIC_WEIGHTS = {}  # e.g. {"work": 0.4, "love": 0.2}


def _safe_json_load(s: Optional[str], fallback):
    if not s:
        return fallback
    try:
        v = json.loads(s)
        return v if v is not None else fallback
    except Exception:
        return fallback


def _safe_json_dump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return json.dumps({})


def get_or_create_user(chat_id: int, username: str):
    ensure_schema()
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (chat_id, username, first_seen, last_seen, interaction_count, user_state, topic_weights, summary) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                chat_id,
                username or "",
                _now(),
                _now(),
                0,
                _safe_json_dump(dict(DEFAULT_USER_STATE)),
                _safe_json_dump(dict(DEFAULT_TOPIC_WEIGHTS)),
                "",
            ),
        )
        conn.commit()


def bump_user(chat_id: int, username: str):
    get_or_create_user(chat_id, username)
    cur = conn.cursor()
    cur.execute("""
    UPDATE users
    SET last_seen=?, interaction_count=interaction_count+1,
        username=COALESCE(NULLIF(?, ''), username)
    WHERE chat_id=?
    """, (_now(), username or "", chat_id))
    conn.commit()


def get_user_state(chat_id: int) -> Dict[str, Any]:
    ensure_schema()
    cur = conn.cursor()
    cur.execute("SELECT user_state FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        return dict(DEFAULT_USER_STATE)
    st = _safe_json_load(row["user_state"], dict(DEFAULT_USER_STATE))
    # ensure defaults
    for k, v in DEFAULT_USER_STATE.items():
        if k not in st:
            st[k] = v if not isinstance(v, (list, dict)) else (list(v) if isinstance(v, list) else dict(v))
    if not isinstance(st.get("recent_events"), list):
        st["recent_events"] = []
    return st


def set_user_state(chat_id: int, state: Dict[str, Any]):
    ensure_schema()
    # ensure defaults
    fixed = dict(DEFAULT_USER_STATE)
    fixed.update(state or {})
    if not isinstance(fixed.get("recent_events"), list):
        fixed["recent_events"] = []
    cur = conn.cursor()
    cur.execute("UPDATE users SET user_state=? WHERE chat_id=?", (_safe_json_dump(fixed), chat_id))
    conn.commit()


def get_topic_weights(chat_id: int) -> Dict[str, float]:
    ensure_schema()
    cur = conn.cursor()
    cur.execute("SELECT topic_weights FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    if not row:
        return {}
    tw = _safe_json_load(row["topic_weights"], {})
    return tw if isinstance(tw, dict) else {}


def set_topic_weights(chat_id: int, weights: Dict[str, float]):
    ensure_schema()
    cur = conn.cursor()
    cur.execute("UPDATE users SET topic_weights=? WHERE chat_id=?", (_safe_json_dump(weights or {}), chat_id))
    conn.commit()


def get_summary(chat_id: int) -> str:
    ensure_schema()
    cur = conn.cursor()
    cur.execute("SELECT summary FROM users WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    return (row["summary"] if row and row["summary"] else "") or ""


def set_summary(chat_id: int, summary: str):
    ensure_schema()
    cur = conn.cursor()
    cur.execute("UPDATE users SET summary=? WHERE chat_id=?", ((summary or "").strip(), chat_id))
    conn.commit()


# -------------------------
# Emotional event memory
# -------------------------
def add_event(
    chat_id: int,
    label: str,
    intent: str,
    note: str,
    outcome: str = "",
    keep_last: int = 8,
):
    """
    Store a short event summary for continuity.
    Keep it small and non-sensitive.
    """
    st = get_user_state(chat_id)
    ev = {
        "ts": _now(),
        "label": (label or "").strip()[:40],
        "intent": (intent or "").strip()[:24],
        "outcome": (outcome or "").strip()[:40],
        "note": (note or "").strip()[:160],
    }
    st["recent_events"] = (st.get("recent_events", []) + [ev])[-keep_last:]
    st["last_user_intent"] = intent
    st["last_user_emotion"] = label
    set_user_state(chat_id, st)


def get_recent_events(chat_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    st = get_user_state(chat_id)
    evs = st.get("recent_events", [])
    if not isinstance(evs, list):
        return []
    return evs[-limit:]


# -------------------------
# Reset
# -------------------------
def reset_memory(chat_id: int):
    """
    Clears summary + user_state + topic weights for that chat.
    (Does NOT touch bot.db taught pairs or style profile.)
    """
    ensure_schema()
    cur = conn.cursor()
    cur.execute("""
    UPDATE users
    SET user_state=?, topic_weights=?, summary=?
    WHERE chat_id=?
    """, (_safe_json_dump(dict(DEFAULT_USER_STATE)), _safe_json_dump({}), "", chat_id))
    conn.commit()
