"""Fire-and-forget background command jobs (package installs can outlive request timeouts)."""
import subprocess
import threading
import time
import uuid
from collections import deque

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_jobs = {}
_lock = threading.Lock()
_OUTPUT_LINES = 200


def start_command(cmd, env=None, timeout=1800):
    """Run command(s) in a background thread. Returns job id.
    cmd is a single argv list ["apt","install"] or a sequence of argv lists
    (run in order, stopping on first failure). No shell."""
    commands = [cmd] if cmd and isinstance(cmd[0], str) else list(cmd)
    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "output": deque(maxlen=_OUTPUT_LINES), "cmd": commands, "started": time.time()}
    with _lock:
        _jobs[job_id] = job

    def run():
        returncode = None
        try:
            for argv in commands:
                proc = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env,
                )
                for line in proc.stdout:
                    job["output"].append(line.rstrip("\n"))
                proc.wait(timeout=timeout)
                returncode = proc.returncode
                if returncode != 0:
                    break
            job["status"] = "done" if returncode == 0 else "error"
            job["returncode"] = returncode
            if returncode != 0:
                job["output"].append(f"Exited with code {returncode}")
        except Exception as e:
            job["status"] = "error"
            job["output"].append(str(e))
        job["finished"] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return job_id


@router.get("/{job_id}")
async def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job_id,
        "status": job["status"],
        "returncode": job.get("returncode"),
        "output": list(job["output"]),
    }
