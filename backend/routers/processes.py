import signal
import time

import psutil
from fastapi import APIRouter, HTTPException

from backend.utils.logger import log_action
from backend.routers.services import service_of_pid

router = APIRouter(prefix="/api/processes", tags=["processes"])


def _process_to_dict(proc):
    try:
        with proc.oneshot():
            name = proc.name() or ""
            username = proc.username() or ""
            cpu = proc.cpu_percent() or 0
            mem_rss = proc.memory_info().rss if proc.memory_info() else 0
            status = proc.status() or "?"
            create_time = proc.create_time()
            cmdline = proc.cmdline() or []
            cmd = " ".join(cmdline) if cmdline else name
            ppid = proc.ppid() or 0
        return {
            "pid": proc.pid,
            "name": name,
            "user": username,
            "cpu": round(cpu, 2),
            "rss": mem_rss,
            "status": status,
            "started": create_time,
            "command": cmd,
            "ppid": ppid,
            "service": service_of_pid(proc.pid),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
        return None


def _process_detail(pid):
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            name = proc.name() or ""
            username = proc.username() or ""
            cpu = proc.cpu_percent() or 0
            mem_rss = proc.memory_info().rss if proc.memory_info() else 0
            mem_vms = proc.memory_info().vms if proc.memory_info() else 0
            status = proc.status() or "?"
            create_time = proc.create_time()
            cmdline = proc.cmdline() or []
            ppid = proc.ppid() or 0
            ppid_name = ""
            try:
                ppid_name = psutil.Process(ppid).name() or ""
            except Exception:
                pass

            open_files_count = 0
            try:
                open_files_count = len(proc.open_files())
            except Exception:
                pass

            connections = []
            try:
                for conn in proc.net_connections(kind="all"):
                    connections.append({
                        "fd": conn.fd,
                        "family": str(conn.family),
                        "type": str(conn.type),
                        "laddr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
                        "raddr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "",
                        "status": conn.status or "",
                    })
            except Exception:
                pass

            num_threads = proc.num_threads() or 0
            nice = proc.nice() or 0
            io_counters = {}
            try:
                io = proc.io_counters()
                io_counters = {
                    "read_bytes": io.read_bytes,
                    "write_bytes": io.write_bytes,
                    "read_count": io.read_count,
                    "write_count": io.write_count,
                }
            except Exception:
                pass

            # Env vars (sensitive, might want to filter later)
            env = {}
            try:
                env = dict(proc.environ())
            except Exception:
                pass

            cwd = ""
            try:
                cwd = proc.cwd() or ""
            except Exception:
                pass

            exe = ""
            try:
                exe = proc.exe() or ""
            except Exception:
                pass

        return {
            "pid": proc.pid,
            "name": name,
            "user": username,
            "cpu": round(cpu, 2),
            "rss": mem_rss,
            "vms": mem_vms,
            "status": status,
            "started": create_time,
            "command": " ".join(cmdline) if cmdline else name,
            "cmdline": cmdline,
            "ppid": ppid,
            "ppid_name": ppid_name,
            "open_files": open_files_count,
            "connections": connections,
            "num_threads": num_threads,
            "nice": nice,
            "io_counters": io_counters,
            "environ": env,
            "cwd": cwd,
            "exe": exe,
            "service": service_of_pid(pid),
        }
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail=f"Process {pid} not found")
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_processes():
    processes = []
    for proc in psutil.process_iter():
        info = _process_to_dict(proc)
        if info:
            processes.append(info)
    processes.sort(key=lambda p: (p.get("cpu") or 0), reverse=True)
    return {"processes": processes}


@router.get("/{pid}")
def get_process(pid: int):
    return _process_detail(pid)


@router.post("/{pid}/kill")
def kill_process(pid: int):
    try:
        proc = psutil.Process(pid)
        name = proc.name() or str(pid)
        proc.send_signal(signal.SIGKILL)
        time.sleep(0.1)
        if not proc.is_running():
            log_action("processes", "kill", str(pid), "success", f"Killed process {name}")
            return {"status": "ok", "message": f"Process {pid} ({name}) killed"}
        log_action("processes", "kill", str(pid), "success", f"Sent SIGKILL to {name}")
        return {"status": "ok", "message": f"SIGKILL sent to {pid} ({name})"}
    except psutil.NoSuchProcess:
        log_action("processes", "kill", str(pid), "error", "Process not found")
        raise HTTPException(status_code=404, detail=f"Process {pid} not found")
    except psutil.AccessDenied:
        log_action("processes", "kill", str(pid), "error", "Access denied")
        raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")
    except Exception as e:
        log_action("processes", "kill", str(pid), "error", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{pid}/stop")
def stop_process(pid: int):
    try:
        proc = psutil.Process(pid)
        name = proc.name() or str(pid)
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        try:
            if not proc.is_running():
                log_action("processes", "stop", str(pid), "success", f"Process {name} terminated")
                return {"status": "ok", "message": f"Process {pid} ({name}) terminated"}
        except psutil.NoSuchProcess:
            log_action("processes", "stop", str(pid), "success", f"Process {name} terminated")
            return {"status": "ok", "message": f"Process {pid} ({name}) terminated"}
        log_action("processes", "stop", str(pid), "success", f"Sent SIGTERM to {name}")
        return {"status": "ok", "message": f"SIGTERM sent to {pid} ({name})"}
    except psutil.NoSuchProcess:
        log_action("processes", "stop", str(pid), "error", "Process not found")
        raise HTTPException(status_code=404, detail=f"Process {pid} not found")
    except psutil.AccessDenied:
        log_action("processes", "stop", str(pid), "error", "Access denied")
        raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")
    except Exception as e:
        log_action("processes", "stop", str(pid), "error", str(e))
        raise HTTPException(status_code=500, detail=str(e))
