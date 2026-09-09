import os
import sys
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from backend.config import load_config, save_config
from backend.utils.distro import detect_all, ensure_required_tools, get_distro_info
from backend.utils.logger import get_log, clear_log
from backend.utils import metricstore
from backend.auth import (authenticate, verify_token, ensure_default_auth, change_password,
                          is_default_password)
from backend.routers.metrics import router as metrics_router, collect_and_store, get_history
from backend.routers.processes import router as processes_router
from backend.routers.services import router as services_router
from backend.routers.network import router as network_router
from backend.routers.packages import router as packages_router
from backend.routers.firewall import router as firewall_router
from backend.routers.ai import router as ai_router
from backend.utils.jobs import router as jobs_router

config = load_config()


_ws_clients = set()
_broadcaster_task = None
_BCAST_INTERVAL = 5


_bcast_ticks = 0


async def _metrics_broadcaster(distro_name=""):
    """Background task: collect and broadcast metrics every 5 seconds."""
    global _bcast_ticks
    while True:
        await asyncio.sleep(_BCAST_INTERVAL)
        snapshot = collect_and_store(distro_name)
        try:
            metricstore.insert(snapshot, minute=(_bcast_ticks % 12 == 0))
            _bcast_ticks += 1
            if _bcast_ticks % 720 == 0:  # ~hourly
                metricstore.prune()
        except Exception as e:
            print(f"[main] metrics store error: {e}")
        disconnected = []
        for ws in list(_ws_clients):
            try:
                await ws.send_json(snapshot)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            _ws_clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _broadcaster_task

    print("[main] Detecting distro...")
    distro_info = detect_all()

    print(f"[main] Distro: {distro_info.get('display_name')} {distro_info.get('version')}")
    print(f"[main] Package managers: {distro_info.get('pkg_manager')}")
    print(f"[main] Firewall: {distro_info.get('firewall') or 'none'}")

    print("[main] Checking required tools...")
    missing = ensure_required_tools()
    if missing:
        print(f"[main] WARNING: Required tools still missing: {missing}")
    else:
        print("[main] All required tools available.")

    # Sets a default password hash if missing, BEFORE any save_config could clobber it
    ensure_default_auth()

    # Reload from disk so in-memory config includes the (possibly just-written) hash
    fresh = load_config()
    config.clear()
    config.update(fresh)
    config["distro_info"] = distro_info

    # Warm up psutil CPU counters so the first snapshot isn't all zeros
    import psutil
    psutil.cpu_percent(percpu=True)

    distro_name = f"{distro_info.get('display_name', '')} {distro_info.get('version', '')}".strip()

    # Start metrics collection immediately and the broadcaster
    collect_and_store(distro_name)
    _broadcaster_task = asyncio.create_task(_metrics_broadcaster(distro_name))
    print(f"[main] Metrics broadcaster started (every {_BCAST_INTERVAL}s)")

    yield

    if _broadcaster_task:
        _broadcaster_task.cancel()


app = FastAPI(title="Linux Control Dashboard", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path

    # Public routes (no auth needed)
    PUBLIC_PREFIXES = ("/css/", "/js/", "/ws/", "/favicon")
    PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status", "/api/health", "/"}
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)

    # Only protect /api/ routes
    if not path.startswith("/api/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    token = auth_header[7:]
    if not verify_token(token):
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

    return await call_next(request)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app.include_router(metrics_router)
app.include_router(processes_router)
app.include_router(services_router)
app.include_router(network_router)
app.include_router(packages_router)
app.include_router(firewall_router)
app.include_router(ai_router)
app.include_router(jobs_router)


security = HTTPBearer(auto_error=False)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    username = verify_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return username


@app.post("/api/auth/login")
async def login(request: Request, body: dict = Body(...)):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    ip = request.client.host if request.client else "unknown"
    token, lock_remaining = authenticate(ip, username, password)
    if not token:
        if lock_remaining > 0:
            raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in {lock_remaining}s")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": token, "username": username, "default_password": is_default_password()}


@app.get("/api/auth/status")
async def auth_status():
    return {"default_password": is_default_password()}


@app.post("/api/auth/change-password")
async def change_pwd(body: dict = Body(...), user: str = Depends(get_current_user)):
    new_password = body.get("new_password", "")
    ok, msg = change_password(user, new_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


# Protected routes
@app.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket):
    await ws.accept()

    # Auth: first message must be {"type": "auth", "token": "..."}
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
        auth_msg = json.loads(first)
    except Exception:
        await ws.close(code=4401)
        return
    if auth_msg.get("type") != "auth" or not verify_token(auth_msg.get("token", "")):
        await ws.close(code=4401)
        return

    _ws_clients.add(ws)

    history_list = get_history()
    try:
        await ws.send_json({"type": "history", "data": history_list})
    except Exception:
        _ws_clients.discard(ws)
        return

    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _ws_clients.discard(ws)


@app.get("/api/config/sections")
async def get_sections():
    return {"sections": config.get("sections", {})}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "distro": get_distro_info(),
    }


AI_KEY_FIELDS = ("openai_key", "openrouter_key", "gemini_key", "custom_key")
_KEY_MASK_PREFIX = "••••"


def _mask_keys(ai: dict) -> dict:
    """Mask API keys for GET /api/settings."""
    out = dict(ai)
    for k in AI_KEY_FIELDS:
        val = out.get(k, "")
        out[k] = (_KEY_MASK_PREFIX + val[-4:]) if val else ""
    return out


def _merge_keys(ai_config: dict, incoming: dict):
    """Only overwrite stored API keys with non-empty, unmasked values."""
    for k in AI_KEY_FIELDS:
        new = incoming.get(k, "")
        if new and not new.startswith(_KEY_MASK_PREFIX):
            ai_config[k] = new


@app.get("/api/settings")
async def get_settings():
    safe = {k: v for k, v in config.items() if k != "distro_info"}
    if "ai" in safe:
        safe["ai"] = _mask_keys(safe["ai"])
    if "auth" in safe and "password_hash" in safe.get("auth", {}):
        safe["auth"] = {**safe["auth"], "password_hash": "***"}
    return safe


@app.put("/api/settings")
async def save_settings(body: dict = Body(...)):
    import ipaddress as _ip

    if "bind" in body:
        try:
            _ip.ip_address(body["bind"])
            config["bind"] = body["bind"]
        except ValueError:
            raise HTTPException(status_code=400, detail="bind must be an IP address")
    if "port" in body:
        try:
            port = int(body["port"])
            assert 1 <= port <= 65535
        except (ValueError, AssertionError, TypeError):
            raise HTTPException(status_code=400, detail="port must be 1-65535")
        config["port"] = port
    if "sections" in body and isinstance(body["sections"], dict):
        config["sections"] = {k: bool(v) for k, v in body["sections"].items()}
    if "ai" in body and isinstance(body["ai"], dict):
        incoming_ai = dict(body["ai"])
        # Keys survive saves unless a genuinely new value is sent — no more wiping
        # every provider's key when switching providers.
        for key_field in AI_KEY_FIELDS:
            incoming_ai.setdefault(key_field, config.get("ai", {}).get(key_field, ""))
        _merge_keys(incoming_ai, body["ai"])
        config["ai"] = incoming_ai
    if "auth" in body and isinstance(body["auth"], dict):
        # Username may be changed here; passwords only via /api/auth/change-password.
        if body["auth"].get("username", "").strip():
            config.setdefault("auth", {})["username"] = body["auth"]["username"].strip()
    save_config(config)
    return {"status": "ok", "message": "Settings saved"}


@app.get("/api/audit")
async def get_audit():
    entries = get_log()
    entries.reverse()
    return {"entries": entries}


@app.delete("/api/audit")
async def clear_audit():
    clear_log()
    return {"status": "ok", "message": "Audit log cleared"}


@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


app.mount("/css", StaticFiles(directory=os.path.join(STATIC_DIR, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(STATIC_DIR, "js")), name="js")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config["bind"], port=config["port"])
