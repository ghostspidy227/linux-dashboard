import os
import time
import shutil
import subprocess
from collections import deque
import threading

import psutil
import socket
import platform

from fastapi import APIRouter

from backend.utils.distro import get_distro_info
from backend.utils import metricstore

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

HISTORY_LENGTH = 720

_history = deque(maxlen=HISTORY_LENGTH)

_prev_net = {}
_prev_disk = {}
_prev_time = None

_lock_hist = threading.Lock()


def _disk_base(device_name):
    """Partition -> physical disk: nvme0n1p2 -> nvme0n1, sda1 -> sda, mmcblk0p1 -> mmcblk0."""
    base = os.path.basename(device_name)
    if base.startswith(("nvme", "mmcblk")) and "p" in base:
        return base.rsplit("p", 1)[0]
    return base.rstrip("0123456789")


def _disk_io_speeds():
    """bytes/sec per physical disk, computed from the last sample interval."""
    speeds = {}
    try:
        io = psutil.disk_io_counters(perdisk=True)
        for dname, counters in io.items():
            read_speed = write_speed = 0
            prev = _prev_disk.get(dname)
            if prev and _prev_time:
                elapsed = time.time() - _prev_time
                if elapsed > 0:
                    read_speed = max(0, (counters.read_bytes - prev[0]) / elapsed)
                    write_speed = max(0, (counters.write_bytes - prev[1]) / elapsed)
            _prev_disk[dname] = (counters.read_bytes, counters.write_bytes)
            speeds[dname] = (read_speed, write_speed)
    except Exception:
        pass
    return speeds


def _get_cpu():
    try:
        model = ""
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

        temps = None
        try:
            temp_data = psutil.sensors_temperatures()
            if temp_data:
                # Prefer obvious CPU sensors over random thermal zones
                preferred = [k for k in temp_data
                             if any(s in k.lower() for s in ("coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"))]
                for name in (preferred or list(temp_data.keys())):
                    entries = temp_data.get(name)
                    if entries:
                        temps = entries[0].current
                        break
        except Exception:
            pass

        if temps is None:
            try:
                zones = [z for z in os.listdir("/sys/class/thermal")
                         if z.startswith("thermal_zone")]
                for zone in zones:
                    tpath = f"/sys/class/thermal/{zone}/temp"
                    if os.path.exists(tpath):
                        with open(tpath, "r") as f:
                            val = int(f.read().strip())
                            temps = val / 1000.0
                            break
            except Exception:
                pass

        per_core = psutil.cpu_percent(percpu=True)
        overall = psutil.cpu_percent(interval=None)

        if temps is not None and (temps < -100 or temps > 150):
            temps = None

        return {
            "percent": overall,
            "per_core": per_core,
            "count": psutil.cpu_count(logical=True),
            "model": model,
            "temperature": round(temps, 1) if temps is not None else None,
        }
    except Exception as e:
        return {"percent": 0, "per_core": [], "count": 0, "model": "", "temperature": None, "error": str(e)}


def _get_ram():
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "percent": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_percent": swap.percent if swap.total > 0 else 0,
        }
    except Exception as e:
        return {"total": 0, "available": 0, "used": 0, "percent": 0,
                "swap_total": 0, "swap_used": 0, "swap_percent": 0, "error": str(e)}


def _get_disk():
    try:
        speeds = _disk_io_speeds()
        parts = []
        for p in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(p.mountpoint)
            except PermissionError:
                continue

            base_disk = _disk_base(p.device)
            read_speed, write_speed = speeds.get(base_disk, (0, 0))

            parts.append({
                "device": p.device,
                "mountpoint": p.mountpoint,
                "fstype": p.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
                "read_bytes_per_sec": round(read_speed),
                "write_bytes_per_sec": round(write_speed),
            })
        return parts
    except Exception as e:
        return [{"error": str(e)}]


def _get_network():
    global _prev_net, _prev_time
    try:
        net_io = psutil.net_io_counters(pernic=True)
        interfaces = []
        for iface, counters in net_io.items():
            sent = counters.bytes_sent
            recv = counters.bytes_recv

            sent_speed = 0
            recv_speed = 0
            prev = _prev_net.get(iface)
            if prev and _prev_time:
                elapsed = time.time() - _prev_time
                if elapsed > 0:
                    sent_speed = max(0, (sent - prev[0]) / elapsed)
                    recv_speed = max(0, (recv - prev[1]) / elapsed)

            _prev_net[iface] = (sent, recv)

            interfaces.append({
                "interface": iface,
                "bytes_sent": sent,
                "bytes_recv": recv,
                "bytes_sent_per_sec": round(sent_speed),
                "bytes_recv_per_sec": round(recv_speed),
            })
        return interfaces
    except Exception as e:
        return [{"error": str(e)}]


def _get_system():
    try:
        return {
            "hostname": socket.gethostname(),
            "uptime": round(time.time() - psutil.boot_time()),
            "kernel": platform.release(),
            "distro": "",
            "load_avg": list(os.getloadavg()),
        }
    except Exception as e:
        return {"hostname": "", "uptime": 0, "kernel": "", "distro": "", "load_avg": [], "error": str(e)}


def _get_gpu():
    result = {"available": False, "devices": []}
    try:
        if os.path.exists("/proc/driver/nvidia/version") and shutil.which("nvidia-smi"):
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if output.returncode == 0:
                for line in output.stdout.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        result["devices"].append({
                            "name": parts[0],
                            "utilization": int(parts[1]) if parts[1].isdigit() else 0,
                            "vram_used": int(float(parts[2]) * 1024 * 1024) if parts[2].replace(".", "").isdigit() else 0,
                            "vram_total": int(float(parts[3]) * 1024 * 1024) if parts[3].replace(".", "").isdigit() else 0,
                            "temperature": float(parts[4]) if parts[4].replace(".", "").isdigit() else 0,
                        })
                result["available"] = True

        elif shutil.which("rocm-smi"):
            output = subprocess.run(
                ["rocm-smi", "--showuse", "--showmeminfo", "vram", "--showtemp", "--json"],
                capture_output=True, text=True, timeout=5
            )
            if output.returncode == 0:
                import json
                data = json.loads(output.stdout)
                for card_id, info in data.items():
                    result["devices"].append({
                        "name": info.get("Card Series", f"GPU {card_id}"),
                        "utilization": int(info.get("GPU use (%)", 0)),
                        "vram_used": int(info.get("VRAM Total Used Memory (B)", 0)),
                        "vram_total": int(info.get("VRAM Total Memory (B)", 0)),
                        "temperature": float(info.get("Temperature (Sensor edge) (C)", 0)),
                    })
                result["available"] = True

        elif os.path.exists("/sys/class/drm"):
            drm_devices = []
            for entry in sorted(os.listdir("/sys/class/drm")):
                if entry.startswith("card") and entry[4:].isdigit():
                    vendor_path = f"/sys/class/drm/{entry}/device/vendor"
                    try:
                        with open(vendor_path, "r") as vf:
                            vendor = vf.read().strip()
                        if vendor in ("0x10de", "0x1002", "0x8086"):
                            drm_devices.append({"name": f"DRM {entry}"})
                    except Exception:
                        pass
            if drm_devices:
                result["devices"] = drm_devices
                result["available"] = True

    except Exception:
        pass
    return result


def _collect_metrics(distro_name=""):
    global _prev_time
    now = time.time()
    snapshot = {
        "cpu": _get_cpu(),
        "ram": _get_ram(),
        "disk": _get_disk(),
        "network": _get_network(),
        "system": _get_system(),
        "gpu": _get_gpu(),
        "timestamp": now,
    }
    snapshot["system"]["distro"] = distro_name
    _prev_time = now
    return snapshot


def collect_and_store(distro_name=""):
    """Collect a metrics snapshot and store in history buffer."""
    snapshot = _collect_metrics(distro_name)
    with _lock_hist:
        _history.append(snapshot)
    return snapshot


def get_history():
    """Return a copy of the history buffer."""
    with _lock_hist:
        return list(_history)


@router.get("/current")
async def get_current_metrics():
    """Return the latest metrics snapshot."""
    info = get_distro_info()
    distro_name = f"{info.get('display_name', '')} {info.get('version', '')}".strip()
    return collect_and_store(distro_name)


@router.get("/history")
async def get_metrics_history(range: str = "1h"):
    """Historical samples: 1h (5s resolution), 24h / 7d (60s resolution), ~300 points."""
    return {"range": range, "points": metricstore.query(range)}
