import os
import re
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Body

from backend.utils.logger import log_action

router = APIRouter(prefix="/api/services", tags=["services"])

UNIT_DIRS = ["/etc/systemd/system/", "/lib/systemd/system/", "/run/systemd/system/"]

_UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9_@.\-]+$")
_USER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_RESTART_POLICIES = {"no", "on-success", "on-failure", "on-abnormal", "on-watchdog", "on-abort", "always", "unless-stopped"}


def _run(*args, timeout=15):
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _norm_name(name):
    if not name.endswith(".service"):
        return name + ".service"
    return name


def _safe_unit_name(name):
    """Unit name that can never escape the unit directories (no '/')."""
    fname = name if name.endswith(".service") else name + ".service"
    if not _UNIT_NAME_RE.match(fname) or fname.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid service name")
    return fname


def _clean(value):
    """Single-line safe value for unit file directives (no newline directive injection)."""
    if value is None:
        return ""
    return re.sub(r"[\r\n\x00-\x1f]", " ", str(value)).strip()


_VERIFY_LINE_RE = re.compile(r"^[^:\s]+\.service:(\d+):\s*(.*)$", re.MULTILINE)


def _verify_unit_content(content):
    """Run systemd-analyze verify against a temp copy. Returns [{"line", "message"}]."""
    if not shutil.which("systemd-analyze"):
        return []
    import uuid
    tmp = f"/tmp/lxdash-verify-{uuid.uuid4().hex}.service"
    try:
        with open(tmp, "w") as f:
            f.write(content)
        code, out, err = _run("systemd-analyze", "verify", tmp, timeout=20)
        text = (out + "\n" + err).replace(tmp, "unit.service")
        errors = [{"line": int(m.group(1)), "message": m.group(2).strip()}
                  for m in _VERIFY_LINE_RE.finditer(text)]
        if not errors and code != 0:
            for line in text.split("\n"):
                line = line.strip()
                if line and "unit.service" in line:
                    errors.append({"line": 0, "message": line})
        return errors
    except Exception:
        return []  # ponytail: verify is a guard, not a gate — never block saving on its own failure
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


_CGROUP_UNIT_RE = re.compile(r"([A-Za-z0-9_@.\-]+\.(?:service|scope))")


def service_of_pid(pid):
    """Owning systemd unit for a PID via /proc cgroup — exact and cheap."""
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            content = f.read()
    except OSError:
        return ""
    m = _CGROUP_UNIT_RE.search(content)
    if not m:
        return ""
    unit = m.group(1)
    return unit[:-len(".service")] if unit.endswith(".service") else unit


def processes_of_service(name):
    """Live processes belonging to a service unit."""
    import psutil
    target = name[:-len(".service")] if name.endswith(".service") else name
    out = []
    for proc in psutil.process_iter():
        try:
            if service_of_pid(proc.pid) != target:
                continue
            with proc.oneshot():
                out.append({
                    "pid": proc.pid,
                    "name": proc.name() or "",
                    "cpu": round(proc.cpu_percent() or 0, 1),
                    "rss": proc.memory_info().rss if proc.memory_info() else 0,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
        if len(out) >= 50:
            break
    return out


def _strip(name):
    return name.replace(".service", "")


def _find_unit_file(name):
    """Find the unit file path for a service. Returns (path, content) or raises."""
    fname = _safe_unit_name(name)
    for d in UNIT_DIRS:
        path = os.path.join(d, fname)
        if os.path.exists(path):
            with open(path, "r") as f:
                return path, f.read()
    raise HTTPException(status_code=404, detail=f"Unit file not found for {name}")


@router.get("")
def list_services():
    services = {}

    # Start from list-unit-files (includes ALL services, even ones never loaded)
    code, out, err = _run("systemctl", "list-unit-files", "--type=service",
                          "--no-legend", "--no-pager")
    if code != 0:
        raise HTTPException(status_code=500, detail=f"systemctl list-unit-files failed: {err}")

    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        fname = parts[0]
        rest = parts[1].split()
        state = rest[0] if rest else "unknown"
        services[fname] = {
            "name": _strip(fname),
            "load": "unknown",
            "active": "inactive",
            "sub": "dead",
            "description": _strip(fname),
            "enabled": state,
            "type": "",
        }

    # Enrich with active state from list-units
    code, out, err = _run("systemctl", "list-units", "--all", "--type=service",
                          "--no-legend", "--no-pager")
    if code == 0:
        for line in out.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            sname = parts[0]
            load = parts[1]
            active = parts[2]
            substate = parts[3]
            desc = parts[4] if len(parts) > 4 else ""
            if sname in services:
                services[sname]["load"] = load
                services[sname]["active"] = active
                services[sname]["sub"] = substate
                if desc and desc != _strip(sname):
                    services[sname]["description"] = desc
            else:
                services[sname] = {
                    "name": _strip(sname),
                    "load": load,
                    "active": active,
                    "sub": substate,
                    "description": desc,
                    "enabled": "unknown",
                    "type": "",
                }

    # Batch query systemctl show for Type and Description
    all_names = list(services.keys())
    for i in range(0, len(all_names), 50):
        batch = all_names[i:i+50]
        show_args = ["systemctl", "show"] + batch + \
                    ["-p", "Type", "--no-pager"]
        code, out, err = _run(*show_args, timeout=30)
        if code == 0:
            for line in out.strip().split("\n"):
                match = re.match(r"^([\w@.\-]+\.service):Type=(.*)", line)
                if match:
                    sn, stype = match.group(1), match.group(2)
                    if sn in services:
                        services[sn]["type"] = stype

        show_args2 = ["systemctl", "show"] + batch + \
                     ["-p", "Description", "--no-pager"]
        code2, out2, err2 = _run(*show_args2, timeout=30)
        if code2 == 0:
            for line in out2.strip().split("\n"):
                match = re.match(r"^([\w@.\-]+\.service):Description=(.*)", line)
                if match:
                    sn, desc = match.group(1), match.group(2)
                    if sn in services and desc and desc != _strip(sn):
                        services[sn]["description"] = desc

    return {"services": list(services.values())}


@router.post("/{name}/start")
def start_service(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("systemctl", "start", fname)
    if code == 0:
        log_action("services", "start", name, "success", f"Started {fname}")
        return {"status": "ok", "message": f"Service {name} started"}
    log_action("services", "start", name, "error", err)
    raise HTTPException(status_code=500, detail=err.strip() or f"Failed to start {name}")


@router.post("/{name}/stop")
def stop_service(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("systemctl", "stop", fname)
    if code == 0:
        log_action("services", "stop", name, "success", f"Stopped {fname}")
        return {"status": "ok", "message": f"Service {name} stopped"}
    log_action("services", "stop", name, "error", err)
    raise HTTPException(status_code=500, detail=err.strip() or f"Failed to stop {name}")


@router.post("/{name}/restart")
def restart_service(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("systemctl", "restart", fname)
    if code == 0:
        log_action("services", "restart", name, "success", f"Restarted {fname}")
        return {"status": "ok", "message": f"Service {name} restarted"}
    log_action("services", "restart", name, "error", err)
    raise HTTPException(status_code=500, detail=err.strip() or f"Failed to restart {name}")


@router.post("/{name}/enable")
def enable_service(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("systemctl", "enable", fname)
    if code == 0:
        log_action("services", "enable", name, "success", f"Enabled {fname}")
        return {"status": "ok", "message": f"Service {name} enabled"}
    log_action("services", "enable", name, "error", err)
    raise HTTPException(status_code=500, detail=err.strip() or f"Failed to enable {name}")


@router.post("/{name}/disable")
def disable_service(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("systemctl", "disable", fname)
    if code == 0:
        log_action("services", "disable", name, "success", f"Disabled {fname}")
        return {"status": "ok", "message": f"Service {name} disabled"}
    log_action("services", "disable", name, "error", err)
    raise HTTPException(status_code=500, detail=err.strip() or f"Failed to disable {name}")


@router.get("/{name}/logs")
def get_service_logs(name: str):
    fname = _safe_unit_name(name)
    code, out, err = _run("journalctl", "-u", fname, "-n", "100", "--no-pager",
                          "-o", "short-iso", timeout=10)
    if code != 0:
        return {"logs": [], "error": err.strip()}
    lines = out.strip().split("\n") if out.strip() else []
    return {"logs": lines, "unit": name}


@router.get("/{name}/unit")
def get_service_unit(name: str):
    path, content = _find_unit_file(name)
    return {"path": path, "content": content}


@router.put("/{name}/unit")
def save_service_unit(name: str, body: dict = Body(...)):
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    fname = _safe_unit_name(name)
    target = os.path.join("/etc/systemd/system", fname)

    # Check if it's a vendor unit we need to override
    vendor_path = None
    if not os.path.exists(target):
        for d in UNIT_DIRS:
            p = os.path.join(d, fname)
            if os.path.exists(p):
                vendor_path = p
                break
        if not vendor_path:
            target = os.path.join("/etc/systemd/system", fname)

    # Syntax-check before touching the real file (bypass with force=true)
    if not body.get("force"):
        errors = _verify_unit_content(content)
        if errors:
            return {"status": "verify_failed", "errors": errors,
                    "message": "systemd-analyze found problems — fix or save again with force"}

    # Write the file
    try:
        with open(target, "w") as f:
            f.write(content)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied writing unit file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Reload systemd
    _run("systemctl", "daemon-reload")
    log_action("services", "edit_unit", name, "success", f"Edited {fname}")
    return {"status": "ok", "message": f"Unit file for {name} saved to {target}"}


@router.post("")
def create_service(body: dict = Body(...)):
    required = ["name", "execstart"]
    for key in required:
        if not body.get(key):
            raise HTTPException(status_code=400, detail=f"'{key}' is required")

    name = body["name"]
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    fname = _safe_unit_name(name)
    target = os.path.join("/etc/systemd/system", fname)

    if os.path.exists(target):
        raise HTTPException(status_code=409, detail=f"Service {name} already exists")

    description = _clean(body.get("description", name))
    execstart = _clean(body["execstart"])
    workingdir = _clean(body.get("workingdir", ""))
    user = _clean(body.get("user", "root"))
    restart = _clean(body.get("restart", "no"))
    enabled = bool(body.get("enabled", False))
    environment = _clean(body.get("environment", ""))
    execstop = _clean(body.get("execstop", ""))
    after = _clean(body.get("after", ""))

    if user and not _USER_RE.match(user):
        raise HTTPException(status_code=400, detail="Invalid user name")
    if restart not in _RESTART_POLICIES:
        raise HTTPException(status_code=400, detail="Invalid restart policy")

    lines = ["[Unit]", f"Description={description or name}"]
    if after:
        lines.append(f"After={after}")
    lines.append("")
    lines.append("[Service]")
    lines.append(f"ExecStart={execstart}")
    if execstop:
        lines.append(f"ExecStop={execstop}")
    if workingdir:
        lines.append(f"WorkingDirectory={workingdir}")
    if user:
        lines.append(f"User={user}")
    if restart:
        lines.append(f"Restart={restart}")
    if environment:
        lines.append(f"Environment={environment}")
    lines.append("")
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target")
    lines.append("")

    content = "\n".join(lines)

    # Syntax-check before creating (bypass with force=true)
    if not body.get("force"):
        errors = _verify_unit_content(content)
        if errors:
            return {"status": "verify_failed", "errors": errors,
                    "message": "systemd-analyze found problems — fix or create again with force"}

    try:
        with open(target, "w") as f:
            f.write(content)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied writing unit file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    _run("systemctl", "daemon-reload")
    log_action("services", "create", name, "success", f"Created unit {fname}")

    if enabled:
        _run("systemctl", "enable", fname)
        _run("systemctl", "start", fname)

    return {
        "status": "ok",
        "message": f"Service {name} created{' and started' if enabled else ''}",
        "unit_file": content,
    }


@router.delete("/{name}")
def delete_service(name: str):
    fname = _safe_unit_name(name)
    target = os.path.join("/etc/systemd/system", fname)

    # Stop and disable first
    _run("systemctl", "stop", fname, timeout=10)
    _run("systemctl", "disable", fname, timeout=10)

    if os.path.exists(target):
        try:
            os.remove(target)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied deleting unit file")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    _run("systemctl", "daemon-reload")
    log_action("services", "delete", name, "success", f"Deleted {fname}")
    return {"status": "ok", "message": f"Service {name} deleted"}


@router.get("/{name}")
def get_service_detail(name: str):
    fname = _safe_unit_name(name)

    # systemctl show for all properties
    code, out, err = _run("systemctl", "show", fname, "--no-pager", timeout=10)
    if code != 0:
        raise HTTPException(status_code=404, detail=f"Service {name} not found")

    props = {}
    for line in out.strip().split("\n"):
        if "=" in line:
            key, _, val = line.partition("=")
            props[key] = val

    if props.get("LoadState") == "not-found":
        raise HTTPException(status_code=404, detail=f"Service {name} not found")

    # systemctl status
    code, status_out, _ = _run("systemctl", "status", fname, "--no-pager", "-l",
                               timeout=10)
    status_text = status_out if code == 0 else ""

    # Unit file
    unit_path = ""
    unit_content = ""
    try:
        unit_path, unit_content = _find_unit_file(name)
    except HTTPException:
        pass

    # Deps
    deps = {}
    for dep_type in ["Requires", "Wants", "After", "Before"]:
        deps[dep_type.lower()] = [
            s.strip() for s in props.get(dep_type, "").split() if s.strip()
        ]

    # Runtime info
    runtime = {}
    for key in ["MainPID", "ExecMainPID", "MemoryCurrent", "CPUUsageNSec", "WatchdogTimestamp"]:
        runtime[key] = props.get(key, "")

    return {
        "name": _strip(fname),
        "properties": props,
        "status_text": status_text,
        "unit_path": unit_path,
        "unit_content": unit_content,
        "dependencies": deps,
        "runtime": runtime,
        "processes": processes_of_service(name),
    }
