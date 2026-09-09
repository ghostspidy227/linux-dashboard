import os
import re
import shutil
import subprocess

import ipaddress
from fastapi import APIRouter, HTTPException, Body

from backend.utils.logger import log_action

router = APIRouter(prefix="/api/firewall", tags=["firewall"])

ACTIONS = ("allow", "deny", "reject", "limit")
DIRECTIONS = ("in", "out")
PROTOCOLS = ("tcp", "udp", "ah", "esp", "gre", "ipv6", "igmp")
_CHAINS = ("INPUT", "OUTPUT", "FORWARD")
_IFACE_RE = re.compile(r"^[a-zA-Z0-9.\-@:]{1,15}$")
_PORT_RE = re.compile(r"^\d+$")
_PORT_RANGE_RE = re.compile(r"^(\d+)[-:](\d+)$")


def _run(*args, timeout=15):
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _detect_firewall():
    if shutil.which("ufw"):
        return "ufw"
    if shutil.which("iptables"):
        return "iptables"
    return "none"


# ── Validation ─────────────────────────────────────────

def _v_port(port):
    """'' / 'any' -> None; int or 'N:M'/'N-M' -> normalized 'N'/'N:M'."""
    if port is None or str(port).strip().lower() in ("", "any"):
        return None
    port = str(port).strip().lower()
    if _PORT_RE.match(port):
        p = int(port)
        if not (0 < p <= 65535):
            raise ValueError(f"Port {p} out of range")
        return str(p)
    m = _PORT_RANGE_RE.match(port)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if not (0 < lo < hi <= 65535):
            raise ValueError(f"Port range {port} invalid")
        return f"{lo}:{hi}"
    raise ValueError(f"Port '{port}' is not a number or range")


def _v_addr(addr):
    """'any' -> None; bare IP or CIDR -> ipaddress network."""
    if addr is None or str(addr).strip().lower() in ("", "any"):
        return None
    try:
        return ipaddress.ip_network(str(addr).strip(), strict=False)
    except ValueError:
        raise ValueError(f"'{addr}' is not a valid IP or CIDR")


def validate_rule(rule: dict) -> dict:
    """Normalize + validate a rule dict. Raises ValueError with a readable message."""
    action = str(rule.get("action", "allow")).lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    direction = str(rule.get("direction", "in")).lower()
    if direction not in DIRECTIONS:
        raise ValueError("direction must be 'in' or 'out'")

    proto = str(rule.get("protocol", "")).strip().lower()
    if proto in ("any", "both", "none"):
        proto = ""
    if proto and proto not in PROTOCOLS:
        raise ValueError(f"protocol must be one of {PROTOCOLS} or empty")

    # "both" protocol → two rules (handled by caller via expand_rule)
    to_port = _v_port(rule.get("port"))
    from_port = _v_port(rule.get("from_port"))
    to_addr = _v_addr(rule.get("to"))
    from_addr = _v_addr(rule.get("from"))

    if to_port is not None and from_port is not None:
        raise ValueError("Specify a port on either 'from' or 'to', not both")

    iface = str(rule.get("interface", "")).strip()
    if iface and not _IFACE_RE.match(iface):
        raise ValueError("Invalid interface name")

    comment = str(rule.get("comment", "")).strip()
    if "\n" in comment or "\r" in comment or "'" in comment:
        raise ValueError("Comment must be a single line without quotes")
    if len(comment) > 128:
        raise ValueError("Comment too long (max 128)")

    chain = str(rule.get("chain", "")).upper()
    if chain and chain not in _CHAINS:
        raise ValueError(f"chain must be one of {_CHAINS}")
    if chain and action == "limit":
        raise ValueError("'limit' action is only supported with UFW")

    return {
        "action": action, "direction": direction, "protocol": proto,
        "port": to_port, "from_port": from_port,
        "to": to_addr, "from": from_addr,
        "interface": iface, "comment": comment, "chain": chain,
    }


def _rule_family(rule) -> int:
    for addr in (rule["to"], rule["from"]):
        if addr is not None and addr.version == 6:
            return 6
    return 4


# ── Command builders (single source of truth for add + preview) ──

def build_ufw_cmd(rule: dict) -> list:
    """Per `man ufw`: ufw ACTION [in|out [on IF]] [proto P] [from A [port P]] [to D [port P]] [comment C]"""
    cmd = ["ufw", rule["action"]]
    if rule["interface"]:
        cmd += [rule["direction"], "on", rule["interface"]]
    elif rule["direction"] == "out":
        cmd += ["out"]
    if rule["protocol"]:
        cmd += ["proto", rule["protocol"]]
    if rule["from"] is not None or rule["from_port"] is not None:
        cmd += ["from", str(rule["from"]) if rule["from"] is not None else "any"]
        if rule["from_port"]:
            cmd += ["port", rule["from_port"]]
    if rule["port"] is not None or rule["to"] is not None:
        cmd += ["to", str(rule["to"]) if rule["to"] is not None else "any"]
        if rule["port"]:
            cmd += ["port", rule["port"]]
    if rule["comment"]:
        cmd += ["comment", rule["comment"]]
    return cmd


def build_iptables_cmd(rule: dict) -> list:
    """iptables -A CHAIN [-i/-o IF] [-p P] [--dport/--sport N] [-s S] [-d D] [-j T] [-m comment --comment C]"""
    chain = rule["chain"] or ("OUTPUT" if rule["direction"] == "out" else "INPUT")
    family = _rule_family(rule)
    binary = "ip6tables" if family == 6 else "iptables"
    cmd = [binary, "-A", chain]

    if rule["interface"]:
        cmd += ["-o" if chain == "OUTPUT" else "-i", rule["interface"]]
    if rule["protocol"]:
        cmd += ["-p", rule["protocol"]]
    if rule["port"] is not None:
        cmd += ["--dport", rule["port"]]
    if rule["from_port"] is not None:
        cmd += ["--sport", rule["from_port"]]
    if rule["from"] is not None:
        cmd += ["-s", str(rule["from"])]
    if rule["to"] is not None:
        cmd += ["-d", str(rule["to"])]

    target = {"allow": "ACCEPT", "deny": "DROP", "reject": "REJECT"}.get(rule["action"], "ACCEPT")
    cmd += ["-j", target]
    if rule["comment"]:
        cmd += ["-m", "comment", "--comment", rule["comment"]]
    return cmd


def build_rule_cmd(fw_type: str, rule: dict) -> list:
    if fw_type == "ufw":
        return build_ufw_cmd(rule)
    return build_iptables_cmd(rule)


def _persist_iptables():
    """Best-effort rules save so iptables changes survive reboot (netfilter-persistent layout)."""
    if not os.path.isdir("/etc/iptables"):
        return
    for binary, path in (("iptables-save", "/etc/iptables/rules.v4"),
                         ("ip6tables-save", "/etc/iptables/rules.v6")):
        if not shutil.which(binary):
            continue
        try:
            code, out, _ = _run(binary, timeout=10)
            if code == 0:
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    f.write(out)
                os.replace(tmp, path)
        except Exception:
            pass  # ponytail: best-effort persistence; check /etc/iptables manually if reboot drops rules


# ── Status parsing ─────────────────────────────────────

def _ufw_status():
    data = {
        "type": "ufw",
        "enabled": False,
        "default_in": "unknown",
        "default_out": "unknown",
        "rules": [],
    }

    code, out, _ = _run("ufw", "status", timeout=5)
    if code != 0:
        return data
    if "inactive" in out.lower():
        return data
    data["enabled"] = True

    code, out, _ = _run("ufw", "status", "verbose", timeout=5)
    if code == 0:
        for line in out.split("\n"):
            m = re.match(r"Default:\s+(\w+)\s+\((\w+)\)", line)
            if m:
                policy, direction = m.group(1).lower(), m.group(2)
                if direction == "incoming":
                    data["default_in"] = policy
                elif direction == "outgoing":
                    data["default_out"] = policy
                    break

    code, out, _ = _run("ufw", "status", "numbered", timeout=5)
    if code == 0:
        for line in out.split("\n"):
            m = re.match(r'^\[\s*(\d+)\]\s+(.+)$', line)
            if not m:
                continue

            num = int(m.group(1))
            rest = m.group(2)

            comment = ""
            if "#" in rest:
                rest, comment = rest.split("#", 1)
                comment = comment.strip()

            rest = rest.strip()
            action, direction = "", ""
            to_str, from_str = "", ""

            parts = rest.split()
            for i, p in enumerate(parts):
                if p.upper() in ("ALLOW", "DENY", "REJECT", "LIMIT"):
                    action = p.lower()
                    if i + 1 < len(parts) and parts[i + 1].upper() in ("IN", "OUT"):
                        direction = parts[i + 1].lower()
                    to_str = " ".join(parts[:i])
                    from_str = " ".join(parts[i + 2:])
                    break

            data["rules"].append({
                "num": num, "action": action, "direction": direction,
                "to": to_str, "from": from_str, "comment": comment,
            })

    return data


_CHAIN_RE = re.compile(r"^Chain\s+([A-Za-z0-9_\-]+)\s+\(policy\s+(\w+)")
_RULE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)")


def _iptables_table_status(binary, table):
    """Parse `iptables -t TABLE -L -n -v --line-numbers` for one table."""
    code, out, _ = _run(binary, "-t", table, "-L", "-n", "-v", "--line-numbers", timeout=5)
    if code != 0:
        return None
    table_data = {"chains": {}}
    current_chain = None
    for line in out.split("\n"):
        chain_match = _CHAIN_RE.match(line)
        if chain_match:
            current_chain = chain_match.group(1)
            table_data["chains"][current_chain] = {"policy": chain_match.group(2), "rules": []}
            continue

        rule_match = _RULE_RE.match(line)
        if rule_match and current_chain:
            rest = rule_match.group(9).split()
            table_data["chains"][current_chain]["rules"].append({
                "num": int(rule_match.group(1)),
                "pkts": rule_match.group(2),
                "bytes": rule_match.group(3),
                "target": rule_match.group(4),
                "prot": rule_match.group(5),
                "opt": rule_match.group(6),
                "in": rule_match.group(7),
                "out": rule_match.group(8),
                "source": rest[0] if rest else "",
                "destination": rest[1] if len(rest) > 1 else "",
            })
    return table_data


def _iptables_status():
    data = {"type": "iptables", "enabled": True, "tables": {}, "tables6": {}}
    for table in ["filter", "nat", "mangle"]:
        t4 = _iptables_table_status("iptables", table)
        if t4 is not None:
            data["tables"][table] = t4
        if shutil.which("ip6tables"):
            t6 = _iptables_table_status("ip6tables", table)
            if t6 is not None:
                data["tables6"][table] = t6
    return data


# ── Endpoints ──────────────────────────────────────────

@router.get("/status")
def get_firewall_status():
    fw_type = _detect_firewall()
    if fw_type == "ufw":
        return _ufw_status()
    elif fw_type == "iptables":
        return _iptables_status()
    else:
        return {"type": "none", "enabled": False, "message": "No firewall detected"}


@router.post("/preview")
def preview_firewall_rule(body: dict = Body(...)):
    """Validate a rule and return the exact command that would run. No changes made."""
    fw_type = _detect_firewall()
    if fw_type == "none":
        raise HTTPException(status_code=400, detail="No supported firewall detected")
    try:
        rule = validate_rule(body)
    except ValueError as e:
        return {"valid": False, "error": str(e)}
    cmd = build_rule_cmd(fw_type, rule)
    return {"valid": True, "command": " ".join(cmd)}


@router.post("/enable")
def enable_firewall():
    fw_type = _detect_firewall()
    if fw_type == "ufw":
        code, out, err = _run("ufw", "--force", "enable", timeout=10)
        if code == 0:
            log_action("firewall", "enable", "ufw", "success", "UFW enabled")
            return {"status": "ok", "message": "UFW enabled"}
        log_action("firewall", "enable", "ufw", "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or "Failed to enable UFW")
    raise HTTPException(status_code=400, detail="Only UFW enable is supported")


@router.post("/disable")
def disable_firewall():
    fw_type = _detect_firewall()
    if fw_type == "ufw":
        code, out, err = _run("ufw", "--force", "disable", timeout=10)
        if code == 0:
            log_action("firewall", "disable", "ufw", "success", "UFW disabled")
            return {"status": "ok", "message": "UFW disabled"}
        log_action("firewall", "disable", "ufw", "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or "Failed to disable UFW")
    raise HTTPException(status_code=400, detail="Only UFW disable is supported")


@router.post("/rules")
def add_firewall_rule(body: dict = Body(...)):
    fw_type = _detect_firewall()
    if fw_type == "none":
        raise HTTPException(status_code=400, detail="No supported firewall detected")

    try:
        rule = validate_rule(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # protocol "both" → two rules
    rules = []
    if body.get("protocol", "").strip().lower() == "both":
        for p in ("tcp", "udp"):
            r2 = dict(rule)
            r2["protocol"] = p
            rules.append(r2)
    else:
        rules.append(rule)

    results = []
    for r in rules:
        cmd = build_rule_cmd(fw_type, r)
        code, out, err = _run(*cmd, timeout=15)
        if code != 0:
            log_action("firewall", "add_rule", _rule_label(r), "error", err)
            raise HTTPException(status_code=500, detail=err.strip() or "Failed to add rule")
        results.append(" ".join(cmd))
        log_action("firewall", "add_rule", _rule_label(r), "success", " ".join(cmd))

    if fw_type == "iptables":
        _persist_iptables()

    return {"status": "ok", "message": "Rule added", "command": " & ".join(results)}


def _rule_label(rule):
    return f"{rule['action']} {rule['port'] or rule['from_port'] or 'any'}/{rule['protocol'] or 'any'} {rule['direction']}"


@router.delete("/rules/{rule_num}")
def delete_firewall_rule(rule_num: int, chain: str = "INPUT", table: str = "filter", v6: bool = False):
    fw_type = _detect_firewall()
    if fw_type == "ufw":
        code, out, err = _run("ufw", "--force", "delete", str(rule_num), timeout=10)
        if code == 0:
            log_action("firewall", "delete_rule", str(rule_num), "success", "Deleted UFW rule")
            return {"status": "ok", "message": f"Rule {rule_num} deleted"}
        log_action("firewall", "delete_rule", str(rule_num), "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or f"Failed to delete rule {rule_num}")

    elif fw_type == "iptables":
        if chain.upper() not in _CHAINS:
            raise HTTPException(status_code=400, detail=f"chain must be one of {_CHAINS}")
        if table not in ("filter", "nat", "mangle", "raw", "security"):
            raise HTTPException(status_code=400, detail="Invalid table")
        binary = "ip6tables" if v6 else "iptables"
        cmd = [binary, "-t", table, "-D", chain.upper(), str(rule_num)]
        code, out, err = _run(*cmd, timeout=10)
        if code == 0:
            _persist_iptables()
            log_action("firewall", "delete_rule", f"{table}/{chain} #{rule_num}", "success", " ".join(cmd))
            return {"status": "ok", "message": f"Rule {rule_num} deleted from {chain}"}
        log_action("firewall", "delete_rule", f"{table}/{chain} #{rule_num}", "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or f"Failed to delete rule {rule_num}")

    else:
        raise HTTPException(status_code=400, detail="No supported firewall detected")


@router.post("/reset")
def reset_firewall():
    fw_type = _detect_firewall()
    if fw_type == "ufw":
        code, out, err = _run("ufw", "--force", "reset", timeout=10)
        if code == 0:
            log_action("firewall", "reset", "ufw", "success", "UFW reset to defaults")
            return {"status": "ok", "message": "UFW reset to defaults"}
        log_action("firewall", "reset", "ufw", "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or "Failed to reset UFW")

    elif fw_type == "iptables":
        for chain in ["INPUT", "OUTPUT", "FORWARD"]:
            _run("iptables", "-F", chain, timeout=10)
            if shutil.which("ip6tables"):
                _run("ip6tables", "-F", chain, timeout=10)
        for binary in ("iptables", "ip6tables"):
            if shutil.which(binary):
                _run(binary, "-P", "INPUT", "ACCEPT", timeout=5)
                _run(binary, "-P", "OUTPUT", "ACCEPT", timeout=5)
                _run(binary, "-P", "FORWARD", "ACCEPT", timeout=5)
        _persist_iptables()
        log_action("firewall", "reset", "iptables", "success", "iptables flushed and reset")
        return {"status": "ok", "message": "iptables flushed and reset to ACCEPT"}

    else:
        raise HTTPException(status_code=400, detail="No supported firewall detected")


@router.put("/policy")
def set_firewall_policy(body: dict = Body(...)):
    direction = body.get("direction", "incoming")
    policy = str(body.get("policy", "deny")).lower()
    if policy not in ("allow", "deny", "reject"):
        raise HTTPException(status_code=400, detail="policy must be allow, deny or reject")
    fw_type = _detect_firewall()

    if fw_type == "ufw":
        direction_arg = "incoming" if direction in ("in", "incoming") else "outgoing"
        code, out, err = _run("ufw", "default", policy, direction_arg, timeout=10)
        if code == 0:
            log_action("firewall", "set_policy", f"{direction} -> {policy}", "success")
            return {"status": "ok", "message": f"Default {direction} policy set to {policy}"}
        log_action("firewall", "set_policy", f"{direction} -> {policy}", "error", err)
        raise HTTPException(status_code=500, detail=err.strip() or "Failed to set policy")

    elif fw_type == "iptables":
        chain_map = {"incoming": "INPUT", "in": "INPUT", "outgoing": "OUTPUT", "out": "OUTPUT", "forward": "FORWARD"}
        chain = chain_map.get(direction, "INPUT")
        policy_map = {"allow": "ACCEPT", "deny": "DROP", "reject": "DROP"}
        _policy = policy_map[policy]
        for binary in ("iptables", "ip6tables"):
            if shutil.which(binary):
                code, out, err = _run(binary, "-P", chain, _policy, timeout=10)
                if code != 0:
                    log_action("firewall", "set_policy", f"{chain} -> {_policy}", "error", err)
                    raise HTTPException(status_code=500, detail=err.strip() or "Failed to set policy")
        _persist_iptables()
        log_action("firewall", "set_policy", f"{chain} -> {_policy}", "success")
        return {"status": "ok", "message": f"Default {chain} policy set to {_policy}"}

    else:
        raise HTTPException(status_code=400, detail="No supported firewall detected")
