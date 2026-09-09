import os
import shutil
import subprocess

TOOL_PACKAGES = {
    "whois": {
        "apt":    "whois",
        "dnf":    "whois",
        "pacman": "whois",
        "yay":    "whois",
    },
    "host": {
        "apt":    "dnsutils",
        "dnf":    "bind-utils",
        "pacman": "bind",
        "yay":    "bind",
    },
    "nslookup": {
        "apt":    "dnsutils",
        "dnf":    "bind-utils",
        "pacman": "bind",
        "yay":    "bind",
    },
    "ss": {
        "apt":    "iproute2",
        "dnf":    "iproute",
        "pacman": "iproute2",
        "yay":    "iproute2",
    },
    "nmap": {
        "apt":    "nmap",
        "dnf":    "nmap",
        "pacman": "nmap",
        "yay":    "nmap",
    },
}

INSTALL_COMMANDS = {
    "apt":    ["apt-get", "install", "-y"],
    "dnf":    ["dnf", "install", "-y"],
    "pacman": ["pacman", "-S", "--noconfirm"],
    "yay":    ["yay", "-S", "--noconfirm"],
}

REQUIRED_TOOLS = ["whois", "host", "nslookup", "ss", "nmap"]
OPTIONAL_TOOLS = ["nvidia-smi", "rocm-smi", "netstat"]

TOOLS_TO_DETECT = [
    "ss", "netstat", "whois", "host", "nslookup", "nmap",
    "nvidia-smi", "rocm-smi", "ufw", "iptables",
]


def _parse_os_release():
    """Read /etc/os-release and return a dict of key-value pairs."""
    info = {}
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    info[key] = value
    except FileNotFoundError:
        pass
    return info


def detect_distro():
    """Detect the Linux distribution and return structured info."""
    os_release = _parse_os_release()

    name = os_release.get("ID", "unknown")
    version = os_release.get("VERSION_ID", "")
    id_like = os_release.get("ID_LIKE", "").split()

    # Map common IDs to display names
    NAME_MAP = {
        "ubuntu": "Ubuntu",
        "debian": "Debian",
        "fedora": "Fedora",
        "centos": "CentOS",
        "rhel": "RHEL",
        "arch": "Arch Linux",
        "manjaro": "Manjaro",
        "opensuse": "openSUSE",
        "alpine": "Alpine Linux",
        "amzn": "Amazon Linux",
    }
    display_name = NAME_MAP.get(name, name.capitalize())

    # Detect package managers
    pkg_managers = []
    for mgr in ["apt", "dnf", "pacman", "yay"]:
        if shutil.which(mgr):
            pkg_managers.append(mgr)

    # AUR helper detection (Arch only)
    if "pacman" in pkg_managers or "arch" in [name] + id_like:
        for aur in ["yay", "paru"]:
            if shutil.which(aur) and aur not in pkg_managers:
                pkg_managers.append(aur)

    # Add snap and flatpak if available
    for extra in ["snap", "flatpak"]:
        if shutil.which(extra):
            pkg_managers.append(extra)

    # Detect firewall
    firewall = ""
    if shutil.which("ufw"):
        firewall = "ufw"
    elif shutil.which("iptables"):
        firewall = "iptables"

    return {
        "name": name,
        "display_name": display_name,
        "version": version,
        "id_like": id_like,
        "pkg_manager": pkg_managers,
        "firewall": firewall,
        "has_systemd": os.path.isdir("/run/systemd/system"),
    }


def _get_pkg_manager():
    """Return the primary package manager to use for installations."""
    for mgr in ["apt", "dnf", "pacman"]:
        if shutil.which(mgr):
            return mgr
    return None


def _run(*args, timeout=120):
    """Run a command via subprocess, return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "Command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "Timed out"


def _is_root():
    return os.geteuid() == 0


def _apt_cache_stale():
    """Check if apt cache is older than 1 hour."""
    import time
    cache_file = "/var/cache/apt/pkgcache.bin"
    if not os.path.exists(cache_file):
        return True
    return (time.time() - os.stat(cache_file).st_mtime) > 3600


def install_tool(tool_name):
    """
    Install a tool using the detected package manager.
    Returns True if installed successfully, False otherwise.
    """
    if shutil.which(tool_name):
        return True

    mgr = _get_pkg_manager()
    if not mgr:
        return False

    # Look up the package name for this tool + manager
    pkg_name = TOOL_PACKAGES.get(tool_name, {}).get(mgr)
    if not pkg_name:
        # Try yay if available on Arch
        for aur in ["yay", "paru"]:
            if shutil.which(aur):
                pkg_name = TOOL_PACKAGES.get(tool_name, {}).get("yay")
                if pkg_name:
                    mgr = "yay"
                    break
        if not pkg_name:
            return False

    print(f"[distro.py] Installing {tool_name} ({pkg_name}) via {mgr}...")

    try:
        if mgr == "apt":
            if _apt_cache_stale():
                print("[distro.py] Updating apt cache...")
                _run("apt-get", "update", "-qq", timeout=120)
            _run("apt-get", "install", "-y", pkg_name, timeout=120)

        elif mgr == "dnf":
            # dnf5 uses same flags
            cmd = "dnf5" if shutil.which("dnf5") else "dnf"
            _run(cmd, "install", "-y", pkg_name, timeout=120)

        elif mgr == "pacman":
            if shutil.which("yay"):
                # yay must not run as root
                if _is_root():
                    print(f"[distro.py] Warning: yay cannot run as root; installing {pkg_name} with pacman...")
                    _run("pacman", "-S", "--noconfirm", pkg_name, timeout=120)
                else:
                    _run("yay", "-S", "--noconfirm", pkg_name, timeout=120)
            else:
                _run("pacman", "-S", "--noconfirm", pkg_name, timeout=120)

        elif mgr == "yay":
            if _is_root():
                print(f"[distro.py] Warning: yay cannot run as root; skipping {tool_name}...")
                return False
            _run("yay", "-S", "--noconfirm", pkg_name, timeout=120)
    except Exception as e:
        print(f"[distro.py] Failed to install {tool_name}: {e}")
        return False

    return shutil.which(tool_name) is not None


def detect_tools():
    """Detect which tools are available on the system."""
    tools = {}
    for tool in TOOLS_TO_DETECT:
        tools[tool] = shutil.which(tool) is not None
    return tools


def ensure_required_tools():
    """Check for required tools and attempt to install missing ones.
    Returns a list of tools that are still missing after installation attempts."""
    missing = []
    for tool in REQUIRED_TOOLS:
        if not shutil.which(tool):
            # host and nslookup come from the same package, deduplicate install
            if tool == "nslookup":
                if shutil.which("host"):
                    continue  # nslookup is bundled with host in bind-utils/dnsutils/bind
            if tool == "host":
                if shutil.which("nslookup"):
                    pass  # proceed to install if nslookup but not host (rare)
            success = install_tool(tool)
            if not success:
                missing.append(tool)

    # Warn about missing optional tools
    for tool in OPTIONAL_TOOLS:
        if not shutil.which(tool):
            print(f"[distro.py] Optional tool '{tool}' not found. Some features will be unavailable.")

    return missing


def detect_all():
    """Run full system detection and return DISTRO_INFO."""
    distro = detect_distro()
    distro["tools"] = detect_tools()
    return distro


# Cache the distro info after first detection
_distro_info = None


def get_distro_info():
    """Return cached distro info, detecting on first call."""
    global _distro_info
    if _distro_info is None:
        _distro_info = detect_all()
    return _distro_info
