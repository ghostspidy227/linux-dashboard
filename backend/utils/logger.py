import os
import json
import threading
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "audit.log.json")
MAX_ENTRIES = 5000

_lock = threading.Lock()


def log_action(section, action, target, result, details=""):
    """Append an action entry to the audit log (capped at MAX_ENTRIES)."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "section": section,
        "action": action,
        "target": target,
        "result": result,
        "details": details
    }
    with _lock:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        entries = []
        if os.path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, "r") as f:
                    entries = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                entries = []
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, LOG_PATH)


def get_log():
    """Return all audit log entries."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return []


def clear_log():
    """Clear the audit log."""
    with _lock:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "w") as f:
            json.dump([], f)
