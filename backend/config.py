import os
import copy
import json

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")

DEFAULT_CONFIG = {
    "bind": "127.0.0.1",
    "port": 7000,
    "sections": {
        "metrics": True,
        "network": True,
        "services": True,
        "processes": True,
        "packages": True,
        "firewall": True
    },
    "ai": {
        "enabled": False,
        "provider": "ollama",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3",
        "openai_key": "",
        "openrouter_key": "",
        "gemini_key": "",
        "openai_model": "gpt-4o",
        "openrouter_model": "mistralai/mistral-7b-instruct",
        "gemini_model": "gemini-1.5-flash",
        "custom_url": "",
        "custom_key": "",
        "custom_model": ""
    },
    "auth": {
        "username": "admin",
        "password_hash": ""
    }
}


def load_config():
    """Load config from data/config.json, creating default if missing."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        config = copy.deepcopy(DEFAULT_CONFIG)
        save_config(config)
        return config
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        # ponytail: corrupt config falls back to defaults; password reset is required then.
        config = {}
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(config)
    return merged


def save_config(config):
    """Save config dict to data/config.json atomically."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def is_section_enabled(config, section_name):
    """Check if a dashboard section is enabled."""
    return config.get("sections", {}).get(section_name, True)
