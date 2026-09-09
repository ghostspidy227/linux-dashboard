import os
import re
import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Body

from backend.utils import jobs
from backend.utils.logger import log_action

router = APIRouter(prefix="/api/packages", tags=["packages"])

MANAGERS = {
    "apt": {"cmd": "dpkg", "args": ["--list"], "binary": True},
    "snap": {"cmd": "snap", "args": ["list"], "binary": True},
    "flatpak": {"cmd": "flatpak", "args": ["list"], "binary": True},
    "pacman": {"cmd": "pacman", "args": ["-Q"], "binary": True},
    "rpm": {"cmd": "rpm", "args": ["-qa"], "binary": True},
}


def _run(*args, timeout=60):
    cmd = [str(a) for a in args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _detect_managers():
    available = []
    for mgr, info in MANAGERS.items():
        if shutil.which(info["cmd"]):
            available.append(mgr)
    return available


@router.get("")
def list_managers():
    managers = _detect_managers()
    return {"managers": managers}


# --- apt / dpkg ---

def _list_apt():
    packages = []
    code, out, err = _run("dpkg-query", "-W", "-f",
                          "${Package}\\t${Version}\\t${Installed-Size}\\t${Description}\\n", timeout=30)
    if code == 0:
        for line in out.strip().split("\n"):
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            name, version, size_kb, desc = parts
            if name:
                packages.append({
                    "name": name,
                    "version": version,
                    "size": int(size_kb) * 1024 if size_kb.isdigit() else 0,
                    "description": desc,
                })
    return packages


def _detail_apt(name):
    # apt-cache show
    code, out, _ = _run("apt-cache", "show", name, timeout=10)
    detail = {
        "name": name,
        "version": "",
        "description": "",
        "homepage": "",
        "size": 0,
        "install_date": "",
        "maintainer": "",
        "section": "",
        "dependencies": [],
        "reverse_deps": [],
        "files": [],
    }

    if code == 0:
        current = {}
        for line in out.split("\n"):
            if not line.strip():
                if current:
                    if not detail["description"]:
                        for k in ("Description-en", "Description"):
                            if current.get(k):
                                detail["description"] = current[k]
                                break
                    if not detail["version"]:
                        detail["version"] = current.get("Version", "")
                    if not detail["homepage"]:
                        detail["homepage"] = current.get("Homepage", "")
                    if not detail["size"]:
                        try:
                            detail["size"] = int(current.get("Size", 0))
                        except ValueError:
                            pass
                    if not detail["maintainer"]:
                        detail["maintainer"] = current.get("Maintainer", "")
                    if not detail["section"]:
                        detail["section"] = current.get("Section", "")
                    current = {}
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key not in current:
                    current[key] = val
        if current:
            for k in ("Description-en", "Description"):
                if current.get(k) and not detail["description"]:
                    detail["description"] = current[k]
            if not detail["version"]:
                detail["version"] = current.get("Version", "")

    # Install date from dpkg-query
    code, out, _ = _run("dpkg-query", "-W", "-f", "${db-fsys:Last-Modified}", name, timeout=5)
    if code == 0:
        detail["install_date"] = out.strip()

    # Dependencies
    code, out, _ = _run("apt-cache", "depends", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            line = line.strip()
            if line.startswith("Depends:") or line.startswith("PreDepends:"):
                dep_name = line.split(":", 1)[1].strip()
                dep_name = re.sub(r'[<>=].*', '', dep_name).strip()
                dep_name = re.sub(r':\w+$', '', dep_name).strip()
                if dep_name and dep_name not in detail["dependencies"]:
                    detail["dependencies"].append(dep_name)

    # Reverse dependencies
    code, out, _ = _run("apt-cache", "rdepends", name, timeout=10)
    if code == 0:
        in_deps = False
        for line in out.split("\n"):
            line = line.strip()
            if line.startswith("Reverse Depends:"):
                in_deps = True
                continue
            if in_deps and line and ":" not in line:
                dep = line.strip()
                if dep and dep not in detail["reverse_deps"]:
                    detail["reverse_deps"].append(dep)

    # Installed files
    code, out, _ = _run("dpkg-query", "-L", name, timeout=10)
    if code == 0:
        detail["files"] = [f for f in out.strip().split("\n") if f.strip()]

    return detail


# --- snap ---

def _list_snap():
    packages = []
    code, out, _ = _run("snap", "list", timeout=15)
    if code == 0:
        lines = out.strip().split("\n")[1:]  # skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0]
                version = parts[1]
                desc = " ".join(parts[4:]) if len(parts) > 4 else ""
                packages.append({
                    "name": name,
                    "version": version,
                    "size": 0,
                    "description": desc,
                })
    return packages


def _detail_snap(name):
    detail = {
        "name": name, "version": "", "description": "", "homepage": "",
        "size": 0, "install_date": "", "dependencies": [], "reverse_deps": [],
        "files": [], "publisher": "", "channel": "",
    }
    code, out, _ = _run("snap", "info", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key == "summary":
                    detail["description"] = val
                elif key == "publisher":
                    detail["publisher"] = val
                elif key == "tracking":
                    detail["channel"] = val
                elif key == "installed":
                    detail["version"] = val
                elif key == "contact":
                    detail["homepage"] = val
    return detail


# --- pacman ---

def _list_pacman():
    packages = []
    code, out, _ = _run("pacman", "-Q", "--info", timeout=30)
    if code == 0:
        current = {}
        for line in out.split("\n"):
            line = line.strip()
            if not line:
                if current.get("Name"):
                    packages.append({
                        "name": current["Name"],
                        "version": current.get("Version", ""),
                        "size": int(current.get("Installed Size", "0").split()[0]) * 1024 if current.get("Installed Size") else 0,
                        "description": current.get("Description", ""),
                    })
                current = {}
            elif ":" in line:
                key, _, val = line.partition(":")
                current[key.strip()] = val.strip()
        if current.get("Name"):
            packages.append({
                "name": current["Name"],
                "version": current.get("Version", ""),
                "size": 0,
                "description": current.get("Description", ""),
            })
    return packages


def _detail_pacman(name):
    detail = {
        "name": name, "version": "", "description": "", "homepage": "",
        "size": 0, "install_date": "", "dependencies": [], "reverse_deps": [],
        "files": [], "maintainer": "",
    }
    code, out, _ = _run("pacman", "-Qi", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                kl = key.lower()
                if kl == "description":
                    detail["description"] = val
                elif kl == "version":
                    detail["version"] = val
                elif kl == "url":
                    detail["homepage"] = val
                elif kl == "installed size":
                    try:
                        detail["size"] = int(float(val.split()[0]) * 1024 * 1024)
                    except Exception:
                        pass
                elif kl == "install date":
                    detail["install_date"] = val
                elif kl == "depends on":
                    if val and val != "None":
                        for dep in val.split():
                            dep = dep.strip()
                            if dep and dep != "None":
                                detail["dependencies"].append(dep)
                elif kl == "required by":
                    if val and val != "None":
                        for dep in val.split():
                            dep = dep.strip()
                            if dep and dep != "None":
                                detail["reverse_deps"].append(dep)
                elif kl == "packager":
                    detail["maintainer"] = val
    # Files
    code, out, _ = _run("pacman", "-Ql", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                detail["files"].append(parts[1])
    return detail


# --- rpm / dnf ---

def _list_rpm():
    packages = []
    code, out, _ = _run("rpm", "-qa", "--queryformat",
                        "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{SIZE}\\t%{SUMMARY}\\n", timeout=30)
    if code == 0:
        for line in out.strip().split("\n"):
            parts = line.split("\t", 3)
            if len(parts) >= 4:
                name, version, size_str, desc = parts
                packages.append({
                    "name": name,
                    "version": version,
                    "size": int(size_str) if size_str.isdigit() else 0,
                    "description": desc,
                })
    return packages


def _detail_rpm(name):
    detail = {
        "name": name, "version": "", "description": "", "homepage": "",
        "size": 0, "install_date": "", "dependencies": [], "reverse_deps": [],
        "files": [], "maintainer": "",
    }
    code, out, _ = _run("rpm", "-qi", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                kl = key.lower()
                if kl == "description":
                    pass
                elif kl == "name":
                    detail["version"] = val
                elif kl == "version":
                    detail["version"] = f"{detail.get('version','')}-{val}"
                elif kl == "url":
                    detail["homepage"] = val
                elif kl == "size":
                    try:
                        detail["size"] = int(val)
                    except Exception:
                        pass
                elif kl == "install date":
                    detail["install_date"] = val
                elif kl == "vendor":
                    detail["maintainer"] = val
    # Requires
    code, out, _ = _run("rpm", "-qR", name, timeout=10)
    if code == 0:
        for line in out.strip().split("\n"):
            dep = line.strip()
            if dep and not dep.startswith("rpmlib(") and not dep.startswith("/"):
                dep = re.sub(r'[<>=].*', '', dep).strip()
                if dep and dep not in detail["dependencies"]:
                    detail["dependencies"].append(dep)
    # Reverse deps
    code, out, _ = _run("rpm", "-q", "--whatrequires", name, timeout=10)
    if code == 0:
        detail["reverse_deps"] = [d for d in out.strip().split("\n") if d.strip()]
    # Files
    code, out, _ = _run("rpm", "-ql", name, timeout=10)
    if code == 0:
        detail["files"] = [f for f in out.strip().split("\n") if f.strip()]
    return detail


# --- flatpak ---

def _list_flatpak():
    packages = []
    code, out, _ = _run("flatpak", "list", "--columns=application,version,description", timeout=15)
    if code == 0:
        for line in out.strip().split("\n"):
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                packages.append({
                    "name": parts[0],
                    "version": parts[1] if len(parts) > 1 else "",
                    "size": 0,
                    "description": parts[2] if len(parts) > 2 else "",
                })
    return packages


def _detail_flatpak(name):
    detail = {
        "name": name, "version": "", "description": "", "homepage": "",
        "size": 0, "install_date": "", "dependencies": [], "reverse_deps": [],
        "files": [],
    }
    code, out, _ = _run("flatpak", "info", name, timeout=10)
    if code == 0:
        for line in out.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key in ("description", "summary"):
                    if not detail["description"]:
                        detail["description"] = val
                elif key == "version":
                    detail["version"] = val
                elif key == "installed":
                    detail["install_date"] = val
    return detail


# --- List / Detail dispatchers ---

_LISTERS = {"apt": _list_apt, "snap": _list_snap, "flatpak": _list_flatpak,
            "pacman": _list_pacman, "rpm": _list_rpm}
_DETAILERS = {"apt": _detail_apt, "snap": _detail_snap, "flatpak": _detail_flatpak,
              "pacman": _detail_pacman, "rpm": _detail_rpm}


# --- Actions: search / install / remove / updates ---
# NOTE: these routes are declared before /{manager}/{name} so "search"/"updates"
# are never swallowed as package names.

_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9+.\-_]+$")
_APT_ENV = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}


def _check_name(name):
    if not name or not _PKG_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid package name")
    return name


def _dnf_binary():
    for b in ("dnf5", "dnf"):
        if shutil.which(b):
            return b
    raise HTTPException(status_code=400, detail="dnf not available")


def _search_apt(q):
    code, out, _ = _run("apt-cache", "search", q, timeout=15)
    results = []
    for line in out.split("\n"):
        if " - " in line:
            name, desc = line.split(" - ", 1)
            results.append({"name": name.strip(), "version": "", "description": desc.strip()})
    return results


def _search_pacman(q):
    code, out, _ = _run("pacman", "-Ss", q, timeout=15)
    results = []
    name = version = desc = ""
    for line in out.split("\n"):
        if line.startswith((" ", "\t")):
            desc = line.strip()
        elif "/" in line:
            if name:
                results.append({"name": name, "version": version, "description": desc})
            repo, _, rest = line.strip().partition("/")
            name_part, _, ver = rest.partition(" ")
            name, version, desc = name_part, ver, ""
    if name:
        results.append({"name": name, "version": version, "description": desc})
    return results


def _search_dnf(q):
    dnf = _dnf_binary()
    code, out, _ = _run(dnf, "search", "-q", q, timeout=30)
    results = []
    for line in out.split("\n"):
        m = re.match(r"^([a-zA-Z0-9+.\-_]+)\s*:\s+(.+)$", line.strip())
        if m:
            results.append({"name": m.group(1), "version": "", "description": m.group(2)})
    return results


def _search_snap(q):
    code, out, _ = _run("snap", "find", q, timeout=20)
    results = []
    for line in out.split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 4:
            results.append({"name": parts[0], "version": parts[1], "description": " ".join(parts[4:]) or " ".join(parts[2:4])})
    return results


def _search_flatpak(q):
    code, out, _ = _run("flatpak", "search", q, timeout=20)
    results = []
    for line in out.split("\n"):
        cols = line.split("\t")
        if len(cols) >= 3:
            results.append({"name": cols[2].strip(), "version": (cols[3].strip() if len(cols) > 3 else ""), "description": cols[1].strip()})
    return results


_SEARCHERS = {"apt": _search_apt, "pacman": _search_pacman, "rpm": _search_dnf,
              "snap": _search_snap, "flatpak": _search_flatpak}


@router.get("/{manager}/search")
def search_packages(manager: str, q: str):
    if manager not in _SEARCHERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    q = q.strip()
    if not q or len(q) > 100:
        raise HTTPException(status_code=400, detail="Query required")
    results = _SEARCHERS[manager](q)
    return {"manager": manager, "results": results[:50], "count": len(results[:50])}


def _action_cmd(manager, action, name):
    """Build the argv for install/remove/upgrade. No shell, validated names only."""
    if manager == "apt":
        if action == "install":
            return ["apt-get", "install", "-y", name]
        if action == "remove":
            return ["apt-get", "remove", "-y", name]
        return ["apt-get", "install", "--only-upgrade", "-y", name]
    if manager == "pacman":
        if action == "install":
            return ["pacman", "-S", "--noconfirm", "--needed", name]
        if action == "remove":
            return ["pacman", "-R", "--noconfirm", name]
        return ["pacman", "-Syu", "--noconfirm"]  # pacman upgrades are all-or-nothing
    if manager == "rpm":
        dnf = _dnf_binary()
        if action == "install":
            return [dnf, "install", "-y", name]
        if action == "remove":
            return [dnf, "remove", "-y", name]
        return [dnf, "upgrade", "-y"] + ([name] if name else [])
    if manager == "snap":
        if action == "install":
            return ["snap", "install", name]
        if action == "remove":
            return ["snap", "remove", name]
        return ["snap", "refresh"] + ([name] if name else [])
    if manager == "flatpak":
        if action == "install":
            return ["flatpak", "install", "--noninteractive", "-y", name]
        if action == "remove":
            return ["flatpak", "uninstall", "--noninteractive", "-y", name]
        return ["flatpak", "update", "--noninteractive", "-y"] + ([name] if name else [])
    raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")


def _start_pkg_job(manager, action, name=""):
    cmd = _action_cmd(manager, action, name)
    # apt needs a fresh index before install/upgrade
    pre = []
    if manager == "apt" and action in ("install", "upgrade") and shutil.which("apt-get"):
        from backend.utils.distro import _apt_cache_stale
        if _apt_cache_stale():
            pre = [["apt-get", "update", "-qq"]]
    job_id = jobs.start_command(pre + [cmd], env=_APT_ENV)
    log_action("packages", action, name or manager, "success", " ".join(cmd))
    return job_id


@router.post("/{manager}/install")
def install_package(manager: str, body: dict = Body(...)):
    if manager not in MANAGERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    name = _check_name(body.get("name", ""))
    return {"job_id": _start_pkg_job(manager, "install", name)}


@router.post("/{manager}/upgrade")
def upgrade_package(manager: str, body: dict = Body(None)):
    if manager not in MANAGERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    name = _check_name(body.get("name", "")) if body and body.get("name") else ""
    if manager == "pacman" and name:
        raise HTTPException(status_code=400, detail="pacman upgrades are all-or-nothing (-Syu)")
    return {"job_id": _start_pkg_job(manager, "upgrade", name)}


@router.delete("/{manager}/{name}")
def remove_package(manager: str, name: str):
    if manager not in MANAGERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    _check_name(name)
    return {"job_id": _start_pkg_job(manager, "remove", name)}


def _updates_apt():
    code, out, _ = _run("apt", "list", "--upgradable", timeout=30)
    updates = []
    for line in out.split("\n")[1:]:
        if "/" not in line:
            continue
        name_part, _, rest = line.partition("/")
        cols = rest.split()
        if cols:
            updates.append({"name": name_part, "version": cols[0] if cols else ""})
    return updates


def _updates_pacman():
    binary = "checkupdates" if shutil.which("checkupdates") else "pacman"
    args = [binary] if binary == "checkupdates" else ["pacman", "-Qu"]
    code, out, _ = _run(*args, timeout=60)
    updates = []
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            updates.append({"name": parts[0], "version": parts[3]})
    return updates


def _updates_dnf():
    dnf = _dnf_binary()
    proc = subprocess.run([dnf, "check-update", "-q"], capture_output=True, text=True, timeout=120)
    updates = []
    for line in proc.stdout.split("\n"):
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"[a-zA-Z0-9+.\-_]+", parts[0]):
            updates.append({"name": parts[0], "version": parts[1]})
    return updates


def _updates_snap():
    code, out, _ = _run("snap", "refresh", "--list", timeout=30)
    updates = []
    for line in out.split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] != "Name":
            updates.append({"name": parts[0], "version": parts[1]})
    return updates


def _updates_flatpak():
    code, out, _ = _run("flatpak", "remote-ls", "--updates", timeout=60)
    updates = []
    for line in out.split("\n"):
        cols = line.split("\t")
        if len(cols) >= 4 and cols[1].strip() and cols[0].strip() != "Name":
            updates.append({"name": cols[1].strip(), "version": cols[2].strip()})
    return updates


_UPDATERS = {"apt": _updates_apt, "pacman": _updates_pacman, "rpm": _updates_dnf,
             "snap": _updates_snap, "flatpak": _updates_flatpak}


@router.get("/{manager}/updates")
def list_updates(manager: str):
    if manager not in _UPDATERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    try:
        updates = _UPDATERS[manager]()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"manager": manager, "updates": updates, "count": len(updates)}


@router.get("/{manager}")
def list_packages(manager: str):
    if manager not in _LISTERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    packages = _LISTERS[manager]()
    return {"manager": manager, "packages": packages, "count": len(packages)}


@router.get("/{manager}/{name}")
def get_package_detail(manager: str, name: str):
    if manager not in _DETAILERS:
        raise HTTPException(status_code=400, detail=f"Unsupported manager: {manager}")
    detail = _DETAILERS[manager](name)
    if not detail or not detail.get("version"):
        raise HTTPException(status_code=404, detail=f"Package {name} not found")
    return detail
