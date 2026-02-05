# style_engine.py
import random

def apply_style(text: str, linebreak_level: float = 0.75) -> str:
    """
    Keep it short, line-broken, casual.
    """
    if not text:
        return "Ok"

    # Normalize whitespace a bit
    t = " ".join(text.split())

    # Occasionally split into two lines if it's a bit long
    if len(t) > 45 and "\n" not in t and random.random() < float(linebreak_level):
        mid = len(t) // 2
        return t[:mid].rstrip() + "\n\n" + t[mid:].lstrip()

    return t
