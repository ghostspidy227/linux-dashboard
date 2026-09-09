import re
import subprocess

import psutil
from fastapi import APIRouter, HTTPException

from backend.utils.ip_resolver import resolve_ip_sync, resolve_ip, get_ip_log
from backend.routers.services import service_of_pid

router = APIRouter(prefix="/api/network", tags=["network"])


def _parse_proc_net():
    """Parse /proc/net/tcp and /proc/net/udp for raw connection data."""
    connections = []

    for proto, path in [("tcp", "/proc/net/tcp"), ("tcp6", "/proc/net/tcp6"),
                         ("udp", "/proc/net/udp"), ("udp6", "/proc/net/udp6")]:
        try:
            with open(path, "r") as f:
                lines = f.readlines()[1:]
        except FileNotFoundError:
            continue

        for line in lines:
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            local_parts = parts[1].split(":")
            remote_parts = parts[2].split(":")

            def _hex_to_ip(segments, v6=False):
                if v6 or len(segments) > 4:
                    hex_part = "".join(segments)
                    groups = [hex_part[i:i+4] for i in range(0, len(hex_part), 4)]
                    # Convert IPv6 hex to address
                    try:
                        addr = ":".join([str(int(g[:2], 16)) + str(int(g[2:], 16)) for g in groups])
                    except Exception:
                        return "", 0
                    return addr, 0
                else:
                    try:
                        addr = ".".join([str(int(seg, 16)) for seg in reversed(segments[:4])])
                        port = int(segments[-1], 16) if len(segments) > 4 else 0
                        return addr, port
                    except Exception:
                        return "", 0

            local_ip, local_port = _hex_to_ip(local_parts, v6="6" in proto)
            remote_ip, remote_port = _hex_to_ip(remote_parts, v6="6" in proto)
            st_hex = parts[3]
            state_code = int(st_hex, 16) if st_hex else 0

            tcp_states = {
                1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV",
                4: "FIN_WAIT1", 5: "FIN_WAIT2", 6: "TIME_WAIT",
                7: "CLOSE", 8: "CLOSE_WAIT", 9: "LAST_ACK",
                10: "LISTEN", 11: "CLOSING",
            }
            state_str = tcp_states.get(state_code, "UNKNOWN") if "tcp" in proto else ""

            connections.append({
                "protocol": proto.replace("6", "").upper(),
                "local_addr": local_ip,
                "local_port": local_port,
                "remote_addr": remote_ip,
                "remote_port": remote_port,
                "state": state_str,
                "inode": int(parts[9], 10) if len(parts) > 9 else 0,
                "uid": int(parts[7], 10) if len(parts) > 7 else 0,
            })

    return connections


def _get_connections():
    """Get all network connections with process info from psutil."""
    results = []

    try:
        raw_connections = psutil.net_connections(kind="all")
    except psutil.AccessDenied:
        raw_connections = []

    for conn in raw_connections:
        entry = {
            "protocol": "",
            "local_addr": "",
            "local_port": 0,
            "remote_addr": "",
            "remote_port": 0,
            "state": "",
            "pid": 0,
            "process": "",
            "direction": "",
        }

        if conn.type == 1:  # SOCK_STREAM (TCP)
            entry["protocol"] = "TCP"
        elif conn.type == 2:  # SOCK_DGRAM (UDP)
            entry["protocol"] = "UDP"
        else:
            entry["protocol"] = f"TYPE_{conn.type}"

        if conn.laddr:
            if hasattr(conn.laddr, "ip"):
                entry["local_addr"] = conn.laddr.ip or ""
                entry["local_port"] = conn.laddr.port or 0
            elif isinstance(conn.laddr, (tuple, list)) and len(conn.laddr) >= 2:
                entry["local_addr"] = conn.laddr[0] or ""
                entry["local_port"] = conn.laddr[1] or 0
            else:
                entry["local_addr"] = str(conn.laddr) or ""
        if conn.raddr:
            if hasattr(conn.raddr, "ip"):
                entry["remote_addr"] = conn.raddr.ip or ""
                entry["remote_port"] = conn.raddr.port or 0
            elif isinstance(conn.raddr, (tuple, list)) and len(conn.raddr) >= 2:
                entry["remote_addr"] = conn.raddr[0] or ""
                entry["remote_port"] = conn.raddr[1] or 0
            else:
                entry["remote_addr"] = str(conn.raddr) or ""

        entry["state"] = conn.status or ""

        # Direction inference
        if conn.status == "LISTEN":
            entry["direction"] = "inbound"
        elif conn.status == "NONE" and entry["remote_addr"]:
            # UDP "connection" — no state
            entry["direction"] = "outbound" if entry["local_port"] > 1024 else "inbound"
        elif conn.raddr:
            if entry["local_port"] < 1024 and entry["remote_port"] > 1024:
                entry["direction"] = "inbound"
            else:
                entry["direction"] = "outbound"
        else:
            entry["direction"] = "inbound"

        if conn.pid:
            entry["pid"] = conn.pid
            try:
                proc = psutil.Process(conn.pid)
                entry["process"] = proc.name() or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                entry["process"] = ""
            entry["service"] = service_of_pid(conn.pid)
        else:
            entry["service"] = ""

        results.append(entry)

    return results


def _ss_fallback():
    """Run ss -tulpna as a fallback for additional data."""
    connections = []
    try:
        proc = subprocess.run(
            ["ss", "-tulpna"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode != 0:
            return connections

        for line in proc.stdout.split("\n")[1:]:
            line = line.strip()
            if not line or line.startswith("Netid"):
                continue
            parts = line.split()
            if len(parts) < 6:
                continue

            proto = parts[0].upper()
            if proto not in ("TCP", "UDP"):
                continue

            state = parts[1] if proto == "TCP" else ""
            # recvq/sendq (parts[2], parts[3]) unused

            local = parts[4]
            remote = parts[5]
            process = " ".join(parts[6:]) if len(parts) > 6 else ""

            local_addr = local.rsplit(":", 1)[0] if ":" in local else local
            local_port = local.rsplit(":", 1)[1] if ":" in local else "0"
            remote_addr = remote.rsplit(":", 1)[0] if ":" in remote else remote
            remote_port = remote.rsplit(":", 1)[1] if ":" in remote else "0"

            try:
                local_port = int(local_port)
                remote_port = int(remote_port)
            except ValueError:
                continue

            direction = "inbound" if state == "LISTEN" else "outbound"

            pid = 0
            pname = ""
            if process:
                pid_match = re.search(r'pid=(\d+)', process)
                if pid_match:
                    pid = int(pid_match.group(1))

            connections.append({
                "protocol": proto,
                "local_addr": local_addr,
                "local_port": local_port,
                "remote_addr": remote_addr,
                "remote_port": remote_port,
                "state": state,
                "pid": pid,
                "process": pname,
                "direction": direction,
                "service": service_of_pid(pid) if pid else "",
            })
    except Exception:
        pass
    return connections


@router.get("/connections")
def get_connections():
    connections = _get_connections()

    # If psutil returned very few connections, try ss fallback
    if len(connections) < 3:
        connections = _ss_fallback()

    # Resolve hostnames for remote IPs (sync, basic)
    for conn in connections:
        if conn.get("remote_addr") and conn["remote_addr"] not in ("0.0.0.0", "::", "127.0.0.1", "::1"):
            resolved = resolve_ip_sync(conn["remote_addr"])
            conn["remote_hostname"] = resolved.get("hostname", "")
        else:
            conn["remote_hostname"] = ""

        if conn.get("local_addr") and conn["local_addr"] not in ("0.0.0.0", "::"):
            resolved = resolve_ip_sync(conn["local_addr"])
            conn["local_hostname"] = resolved.get("hostname", "")
        else:
            conn["local_hostname"] = ""

    # Sort: LISTEN first, then ESTABLISHED, then by port
    state_order = {"LISTEN": 0, "ESTABLISHED": 1}
    connections.sort(key=lambda c: (state_order.get(c.get("state", ""), 99), c.get("local_port", 0)))

    return {"connections": connections}


@router.get("/iplog")
def network_ip_log():
    return {"entries": get_ip_log()}


@router.post("/resolve")
async def resolve_ip_endpoint(body: dict):
    ip = body.get("ip", "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="IP is required")
    result = await resolve_ip(ip)
    return result
