import os
import json
import shutil
import subprocess
import socket
import ipaddress
import threading
import time
from collections import OrderedDict

import httpx

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ip_log.json")
CACHE_MAX = 2048

_rfc1918_nets = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_memory_cache = OrderedDict()  # ponytail: single in-process cache, oldest-evicted
_cache_lock = threading.Lock()
_log_lock = threading.Lock()


def _cache_get(ip_str):
    with _cache_lock:
        return _memory_cache.get(ip_str)


def _cache_put(ip_str, value):
    with _cache_lock:
        _memory_cache[ip_str] = value
        _memory_cache.move_to_end(ip_str)
        while len(_memory_cache) > CACHE_MAX:
            _memory_cache.popitem(last=False)


def is_private(ip_str):
    try:
        addr = ipaddress.ip_address(ip_str.strip())
        for net in _rfc1918_nets:
            if addr in net:
                return True
        return False
    except ValueError:
        return True


def _reverse_dns(ip_str):
    try:
        return socket.gethostbyaddr(ip_str)[0]
    except Exception:
        return ""


def _whois_lookup(ip_str):
    """Extract useful fields from whois output. Returns a dict with org, country, netname, descr, address."""
    if not shutil.which("whois"):
        return {}

    result = {"org": "", "country": "", "netname": "", "descr": "", "address": ""}

    try:
        proc = subprocess.run(["whois", ip_str], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return result

        lines = proc.stdout.split("\n")
        for line in lines:
            line_lower = line.lower().strip()
            if not line_lower or line_lower.startswith("#") or line_lower.startswith("%"):
                continue

            if ":" not in line_lower:
                continue

            key, _, val = line_lower.partition(":")
            key = key.strip()
            val = val.strip()

            if not val:
                continue

            if key in ("orgname", "org-name", "organisation", "organization", "owner"):
                if not result["org"]:
                    result["org"] = val
            elif key == "netname":
                if not result["netname"]:
                    result["netname"] = val
            elif key in ("descr", "description"):
                if not result["descr"]:
                    result["descr"] = val
            elif key == "country" and len(val) == 2:
                if not result["country"]:
                    result["country"] = val.upper()
            elif key == "country":
                # Full country name
                if not result["country"] or len(result["country"]) > 3:
                    result["country"] = val
            elif key in ("address", "street"):
                if result["address"]:
                    result["address"] += ", "
                result["address"] += val
            elif key in ("city", "stateprov"):
                # Capture city if we don't have address data
                if not result["address"]:
                    result["address"] = val

        # If no org found from these keys, try netname as org
        if not result["org"] and result["netname"]:
            result["org"] = result["netname"]

        return result
    except Exception:
        return result


async def _ip_api_lookup(ip_str):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip_str}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return {
                        "country": data.get("country", ""),
                        "city": data.get("city", ""),
                        "org": data.get("org", ""),
                        "isp": data.get("isp", ""),
                        "region": data.get("regionName", ""),
                    }
        return None
    except Exception:
        return None


async def resolve_ip(ip_str):
    ip_str = ip_str.strip()
    if not ip_str:
        return {"ip": ip_str, "error": "Empty IP"}

    cached = _cache_get(ip_str)
    if cached and cached.get("country") is not None:
        # Only use cache if it has full data (country field indicates full resolution)
        return cached

    result = {
        "ip": ip_str,
        "private": is_private(ip_str),
        "hostname": "",
        "country": "",
        "city": "",
        "org": "",
        "isp": "",
        "region": "",
    }

    if result["private"]:
        result["hostname"] = _reverse_dns(ip_str)
        if not result["hostname"]:
            result["hostname"] = "Local Network"
    else:
        result["hostname"] = _reverse_dns(ip_str)

        api_data = await _ip_api_lookup(ip_str)
        if api_data:
            result.update(api_data)

        # Fill missing fields and enrich from whois
        whois_data = _whois_lookup(ip_str)
        if whois_data:
            for field in ["org", "country"]:
                if not result.get(field) and whois_data.get(field):
                    result[field] = whois_data[field]
            for field in ["netname", "descr", "address"]:
                if whois_data.get(field):
                    result[field] = whois_data[field]

    _cache_put(ip_str, result)
    _update_ip_log(ip_str, result)
    return result


def resolve_ip_sync(ip_str):
    """Synchronous wrapper that uses host/nslookup only (no IP-API)."""
    ip_str = ip_str.strip()
    if not ip_str:
        return {"ip": ip_str, "hostname": "", "private": False}

    cached = _cache_get(ip_str)
    if cached:
        return cached

    result = {
        "ip": ip_str,
        "private": is_private(ip_str),
        "hostname": "",
    }

    if result["private"]:
        result["hostname"] = _reverse_dns(ip_str) or "Local Network"
    else:
        result["hostname"] = _reverse_dns(ip_str) or ""

    _cache_put(ip_str, result)
    return result


def _update_ip_log(ip_str, resolved):
    with _log_lock:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        entries = []
        if os.path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, "r") as f:
                    entries = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                entries = []

        now = time.time()
        for entry in entries:
            if entry.get("ip") == ip_str:
                entry["last_seen"] = now
                entry["times_seen"] = entry.get("times_seen", 0) + 1
                if resolved.get("hostname"):
                    entry["hostname"] = resolved["hostname"]
                if resolved.get("country"):
                    entry["country"] = resolved["country"]
                if resolved.get("city"):
                    entry["city"] = resolved["city"]
                if resolved.get("org"):
                    entry["org"] = resolved["org"]
                if resolved.get("isp"):
                    entry["isp"] = resolved["isp"]
                break
        else:
            entries.append({
                "ip": ip_str,
                "hostname": resolved.get("hostname", ""),
                "country": resolved.get("country", ""),
                "city": resolved.get("city", ""),
                "org": resolved.get("org", ""),
                "isp": resolved.get("isp", ""),
                "first_seen": now,
                "last_seen": now,
                "times_seen": 1,
            })

        tmp = LOG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, LOG_PATH)


def get_ip_log():
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r") as f:
            entries = json.load(f)
        entries.sort(key=lambda e: e.get("last_seen", 0), reverse=True)
        return entries
    except (json.JSONDecodeError, FileNotFoundError):
        return []
