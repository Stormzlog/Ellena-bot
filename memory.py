import sqlite3, json, time

conn = sqlite3.connect("memory.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
  chat_id INTEGER PRIMARY KEY,
  username TEXT,
  first_seen REAL,
  last_seen REAL,
  interaction_count INTEGER,
  style_bias TEXT,
  topic_weights TEXT,
  tone_history TEXT,
  summary TEXT
)
""")
conn.commit()

def get_user(chat_id, username):
    c.execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?, ?,?)",
            (chat_id, username, time.time(), time.time(), 0,
             json.dumps({}), json.dumps({}), json.dumps([]), "")
        )
        conn.commit()

def update_interaction(chat_id):
    c.execute(
        "UPDATE users SET interaction_count = interaction_count + 1, last_seen=? WHERE chat_id=?",
        (time.time(), chat_id)
    )
    conn.commit()

def reset_memory(chat_id):
    c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit()
