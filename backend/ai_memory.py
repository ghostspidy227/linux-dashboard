"""AI memory: chat threads (JSON files) + long-term facts. All stdlib."""
import json
import os
import threading
import uuid
from datetime import datetime, timezone

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SESSIONS_DIR = os.path.join(_DATA_DIR, "ai_sessions")
FACTS_PATH = os.path.join(_DATA_DIR, "ai_facts.json")

_facts_lock = threading.Lock()
_sessions_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Threads ────────────────────────────────────────────

def create_session(title="New chat"):
    sess = {
        "id": uuid.uuid4().hex[:12],
        "title": (title or "New chat")[:60],
        "created": _now(),
        "updated": _now(),
        "messages": [],
    }
    with _sessions_lock:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        _write_session(sess)
    return sess


def _session_path(sid):
    # id is server-generated hex; still reject anything path-shaped
    if not sid or not all(c.isalnum() for c in sid):
        raise ValueError("Invalid session id")
    return os.path.join(SESSIONS_DIR, sid + ".json")


def _write_session(sess):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    tmp = _session_path(sess["id"]) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sess, f, indent=2)
    os.replace(tmp, _session_path(sess["id"]))


def get_session(sid):
    try:
        with open(_session_path(sid), "r") as f:
            return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def list_sessions():
    if not os.path.isdir(SESSIONS_DIR):
        return []
    out = []
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, fname), "r") as f:
                s = json.load(f)
            out.append({"id": s["id"], "title": s.get("title", ""), "updated": s.get("updated", ""),
                        "message_count": len(s.get("messages", []))})
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out


def save_session(sess):
    sess["updated"] = _now()
    with _sessions_lock:
        _write_session(sess)


def delete_session(sid):
    try:
        os.remove(_session_path(sid))
    except OSError:
        pass


def rename_session(sid, title):
    sess = get_session(sid)
    if sess:
        sess["title"] = (title or sess["title"])[:60]
        save_session(sess)
    return sess


# ── Long-term facts ────────────────────────────────────

def list_facts():
    with _facts_lock:
        try:
            with open(FACTS_PATH, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []


def add_fact(text):
    text = str(text).strip()[:300]
    if not text:
        return None
    with _facts_lock:
        facts = []
        try:
            with open(FACTS_PATH, "r") as f:
                facts = json.load(f)
        except (OSError, json.JSONDecodeError):
            facts = []
        for fact in facts:
            if fact.get("text", "").lower() == text.lower():
                return fact
        fact = {"id": uuid.uuid4().hex[:8], "text": text, "added": _now()}
        facts.append(fact)
        tmp = FACTS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(facts, f, indent=2)
        os.replace(tmp, FACTS_PATH)
        return fact


def remove_fact(fact_id):
    with _facts_lock:
        facts = []
        try:
            with open(FACTS_PATH, "r") as f:
                facts = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        remaining = [f for f in facts if f.get("id") != fact_id]
        if len(remaining) == len(facts):
            return False
        tmp = FACTS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(remaining, f, indent=2)
        os.replace(tmp, FACTS_PATH)
        return True
