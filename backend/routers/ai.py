import json
import re

import httpx
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import StreamingResponse

from backend.config import load_config
from backend import ai_memory

router = APIRouter(prefix="/api/ai", tags=["ai"])

MAX_TOOL_ROUNDS = 4
MAX_CONTEXT_CHARS = 6000

SYSTEM_PROMPT = """You are a Linux system assistant embedded in a server dashboard.
The user is not necessarily a Linux expert. You explain system information in plain English.
You can suggest configuration changes, better systemd unit files, or firewall rules — but you NEVER execute anything yourself.
Keep explanations concise. Use bullet points only when listing multiple items.
When suggesting changes, always show the complete new configuration, not just the diff."""

TOOLS_PROMPT = """
You have read-only tools to inspect this machine. To call one, reply with EXACTLY one fenced block and nothing else:

```tool
{"tool": "tool_name", "args": {...}}
```

Available tools:
- search_processes {"query": "...", "limit": 10} — running processes matching query
- search_services {"query": "...", "limit": 10} — systemd services matching query
- list_connections {"query": "...", "limit": 20} — active network connections (query matches process/service/port)
- search_packages {"manager": "apt|pacman|rpm|snap|flatpak", "query": "..."} — search installable packages
- get_firewall_rules {} — current firewall status and rules
- read_journal {"unit": "nginx", "lines": 30} — recent journal logs for a service
- get_metrics_summary {} — current CPU/RAM/disk/network snapshot
- search_facts {"query": "..."} — remembered facts about this machine
- remember_fact {"text": "..."} — save a lasting fact about this machine (e.g. "jellyfin serves on :8096")

After tool results are provided (marked TOOL RESULTS), continue your answer in plain text.
You may also PROPOSE actions (they run only if the user clicks Approve in the UI). To propose, include in your ANSWER a fenced block:

```proposal
{"type": "firewall_rule"|"service_action"|"package_action"|"process_kill", ...args}
```

Proposal args:
- firewall_rule: {"action": "allow|deny|reject|limit", "direction": "in|out", "protocol": "tcp|udp", "port": "22", "from": "any|CIDR", "to": "any|CIDR", "comment": "..."}
- service_action: {"action": "start|stop|restart|enable|disable", "name": "nginx"}
- package_action: {"action": "install|remove|upgrade", "manager": "apt|pacman|rpm|snap|flatpak", "name": "htop"}
- process_kill: {"pid": 1234, "name": "badproc"}

Only propose when the user asked for a change. Everything else: just explain."""


def _get_ai_config():
    return load_config().get("ai", {})


class AIError(Exception):
    pass


def sse(obj) -> str:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n"


# ── Provider token streams (each yields plain text tokens) ──

async def _openai_tokens(url, headers, body):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode()[:300]
                raise AIError(f"HTTP {resp.status_code}: {err}")
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        obj = json.loads(payload)
                        token = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        if token:
                            yield token
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass


async def _ollama_tokens(url, body):
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, headers={"Content-Type": "application/json"}, json=body) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode()[:300]
                raise AIError(f"HTTP {resp.status_code}: {err}")
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        token = obj.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if obj.get("done"):
                            return
                    except json.JSONDecodeError:
                        pass


async def _gemini_tokens(url, body, key):
    # line-buffered; key via header so it never lands in URLs/logs
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=body, headers={"x-goog-api-key": key}) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode()[:300]
                raise AIError(f"HTTP {resp.status_code}: {err}")
            buffer = ""
            async for chunk in resp.aiter_bytes():
                buffer += chunk.decode(errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("data: "):
                        line = line[6:]
                    if not line or line == "[DONE]":
                        continue
                    try:
                        obj = json.loads(line)
                        for c in obj.get("candidates", []):
                            for p in c.get("content", {}).get("parts", []):
                                if p.get("text"):
                                    yield p["text"]
                    except json.JSONDecodeError:
                        pass


def _to_gemini_contents(messages):
    contents = []
    for msg in messages:
        if msg["role"] == "system":
            contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
            continue
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    return contents


def _provider_streamer(ai, messages):
    """Returns (streamer_callable, provider). streamer() yields plain text tokens."""
    provider = ai.get("provider", "ollama")

    if provider == "ollama":
        url = ai.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat"

        def make(msgs):
            body = {"model": ai.get("ollama_model", "llama3"), "messages": msgs, "stream": True}
            return _ollama_tokens(url, body)
        return make, provider

    if provider in ("openai", "openrouter", "custom"):
        if provider == "openai":
            key, url = ai.get("openai_key", ""), "https://api.openai.com/v1/chat/completions"
            model = ai.get("openai_model", "gpt-4o")
        elif provider == "openrouter":
            key, url = ai.get("openrouter_key", ""), "https://openrouter.ai/api/v1/chat/completions"
            model = ai.get("openrouter_model", "")
        else:
            key = ai.get("custom_key", "")
            url = ai.get("custom_url", "").rstrip("/")
            if url.endswith("/chat/completions"):
                pass  # full endpoint given
            elif url.endswith("/v1"):
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"
            model = ai.get("custom_model", "")
        if not key and provider != "custom":
            raise AIError(f"{provider} API key not set")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:7000"
            headers["X-Title"] = "Linux Dashboard"

        def make(msgs):
            body = {"model": model, "messages": msgs, "stream": True}
            return _openai_tokens(url, headers, body)
        return make, provider

    if provider == "gemini":
        key = ai.get("gemini_key", "")
        model = ai.get("gemini_model", "gemini-1.5-flash")
        if not key:
            raise AIError("Gemini API key not set")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"

        def make(msgs):
            return _gemini_tokens(url, {"contents": _to_gemini_contents(msgs)}, key)
        return make, provider

    raise AIError(f"Unknown provider: {provider}")


_TOOL_RE = re.compile(r"```tool\s*(\{.*?\})\s*```", re.DOTALL)
_PROPOSAL_RE = re.compile(r"```proposal\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_tool_calls(text):
    calls = []
    for m in _TOOL_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if obj.get("tool"):
                calls.append(obj)
        except json.JSONDecodeError:
            continue
    return calls


def _parse_proposals(text):
    proposals = []
    for m in _PROPOSAL_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if obj.get("type"):
                proposals.append(obj)
        except json.JSONDecodeError:
            continue
    return proposals


def _strip_tool_blocks(text):
    return _TOOL_RE.sub("", text)


# ── Read-only tools ──

def _tool_search_processes(args):
    import psutil
    query = str(args.get("query", "")).lower()
    limit = min(int(args.get("limit", 10)), 25)
    results = []
    for proc in psutil.process_iter():
        try:
            with proc.oneshot():
                name = proc.name() or ""
                cmd = " ".join(proc.cmdline() or [])
                if query and query not in name.lower() and query not in cmd.lower():
                    continue
                results.append({"pid": proc.pid, "name": name, "cpu": round(proc.cpu_percent() or 0, 1),
                                "rss_mb": round((proc.memory_info().rss if proc.memory_info() else 0) / 1048576, 1),
                                "user": proc.username() or ""})
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
        if len(results) >= limit:
            break
    return results or f"No processes matching '{query}'"


def _tool_search_services(args):
    from backend.routers.services import list_services
    query = str(args.get("query", "")).lower()
    limit = min(int(args.get("limit", 10)), 25)
    data = list_services()
    matches = [s for s in data["services"]
               if not query or query in s["name"].lower() or query in (s.get("description") or "").lower()]
    return matches[:limit] or f"No services matching '{query}'"


def _tool_list_connections(args):
    from backend.routers.network import _get_connections
    query = str(args.get("query", "")).lower()
    limit = min(int(args.get("limit", 20)), 50)
    conns = _get_connections()
    if query:
        conns = [c for c in conns if query in json.dumps(c).lower()]
    return conns[:limit] or "No matching connections"


def _tool_search_packages(args):
    from backend.routers.packages import _SEARCHERS
    manager = str(args.get("manager", "apt")).lower()
    query = str(args.get("query", ""))
    if manager not in _SEARCHERS:
        return f"Unknown manager '{manager}'"
    try:
        return _SEARCHERS[manager](query)[:15]
    except Exception as e:
        return f"Search failed: {e}"


def _tool_get_firewall_rules(args):
    from backend.routers.firewall import _detect_firewall, _ufw_status, _iptables_status
    fw = _detect_firewall()
    if fw == "ufw":
        return _ufw_status()
    if fw == "iptables":
        return _iptables_status()
    return "No firewall detected"


def _tool_read_journal(args):
    import subprocess
    unit = str(args.get("unit", ""))
    lines = min(int(args.get("lines", 30)), 100)
    if not unit:
        return "unit required"
    if not unit.endswith(".service"):
        unit += ".service"
    proc = subprocess.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"],
                          capture_output=True, text=True, timeout=10)
    return proc.stdout if proc.returncode == 0 else f"journalctl failed: {proc.stderr[:200]}"


def _tool_get_metrics_summary(args):
    from backend.routers.metrics import collect_and_store
    from backend.utils.distro import get_distro_info
    info = get_distro_info()
    snap = collect_and_store(f"{info.get('display_name', '')} {info.get('version', '')}".strip())
    return {
        "cpu_percent": snap["cpu"].get("percent"),
        "cpu_temp": snap["cpu"].get("temperature"),
        "ram_percent": snap["ram"].get("percent"),
        "ram_used_gb": round((snap["ram"].get("used") or 0) / 1073741824, 1),
        "swap_percent": snap["ram"].get("swap_percent"),
        "disks": [{"mount": d.get("mountpoint"), "percent": d.get("percent")} for d in snap.get("disk", []) if "error" not in d],
        "top_net": sorted(snap.get("network", []), key=lambda n: n.get("bytes_recv_per_sec", 0), reverse=True)[:3],
        "uptime_hours": round((snap["system"].get("uptime") or 0) / 3600, 1),
        "load_avg": snap["system"].get("load_avg"),
    }


def _tool_search_facts(args):
    facts = ai_memory.list_facts()
    query = str(args.get("query", "")).lower()
    if query:
        facts = [f for f in facts if query in f.get("text", "").lower()]
    return facts or "No facts remembered yet."


def _tool_remember_fact(args):
    text = str(args.get("text") or args.get("fact") or args.get("note") or args.get("content") or "").strip()
    if not text:
        return "text required - call again with {\"text\": \"the fact to remember\"}"
    fact = ai_memory.add_fact(text)
    return {"remembered": fact["text"] if fact else text}


READ_TOOLS = {
    "search_processes": _tool_search_processes,
    "search_services": _tool_search_services,
    "list_connections": _tool_list_connections,
    "search_packages": _tool_search_packages,
    "get_firewall_rules": _tool_get_firewall_rules,
    "read_journal": _tool_read_journal,
    "get_metrics_summary": _tool_get_metrics_summary,
    "search_facts": _tool_search_facts,
    "remember_fact": _tool_remember_fact,
}


def _execute_tool(call):
    """Read-only tools run. Returns (label, result). Never mutates the system."""
    name = str(call.get("tool", ""))
    args = call.get("args") or {}
    try:
        fn = READ_TOOLS.get(name)
        if not fn:
            return name, f"Unknown tool '{name}'"
        return name, fn(args)
    except Exception as e:
        return name, f"Tool error: {e}"


def _build_proposal(proposal):
    """Validate + enrich a proposal into an Approve-card payload. No execution."""
    ptype = proposal.get("type")
    out = {"type": ptype}
    try:
        if ptype == "firewall_rule":
            from backend.routers.firewall import validate_rule, build_rule_cmd, _detect_firewall
            rule = validate_rule(proposal)
            fw = _detect_firewall()
            if fw == "none":
                return {"type": "error", "message": "No firewall detected"}
            out["rule"] = {k: proposal.get(k) for k in
                           ("action", "direction", "protocol", "port", "from", "to", "comment", "interface")}
            out["command"] = " ".join(build_rule_cmd(fw, rule))
            out["firewall"] = fw
            out["label"] = f"Firewall: {out['command']}"
        elif ptype == "service_action":
            action = str(proposal.get("action", ""))
            if action not in ("start", "stop", "restart", "enable", "disable"):
                return {"type": "error", "message": f"Bad service action '{action}'"}
            out["action"] = action
            out["name"] = str(proposal.get("name", ""))[:120]
            out["label"] = f"Service: {action} {out['name']}"
        elif ptype == "package_action":
            action = str(proposal.get("action", ""))
            if action not in ("install", "remove", "upgrade"):
                return {"type": "error", "message": f"Bad package action '{action}'"}
            out["action"] = action
            out["manager"] = str(proposal.get("manager", "apt"))
            out["name"] = str(proposal.get("name", ""))[:120]
            out["label"] = f"Package: {action} {out['name']} ({out['manager']})"
        elif ptype == "process_kill":
            out["pid"] = int(proposal.get("pid", 0))
            out["name"] = str(proposal.get("name", ""))
            out["label"] = f"Kill process {out['pid']} ({out['name']})"
        else:
            return {"type": "error", "message": f"Unknown proposal type '{ptype}'"}
    except (ValueError, TypeError) as e:
        return {"type": "error", "message": str(e)}
    return out


# ── Endpoints ──

@router.get("/sessions")
async def list_sessions():
    return {"sessions": ai_memory.list_sessions()}


@router.post("/sessions")
async def create_session(body: dict = Body(None)):
    title = (body or {}).get("title", "New chat")
    return ai_memory.create_session(title)


@router.get("/sessions/{sid}")
async def get_session(sid: str):
    sess = ai_memory.get_session(sid)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@router.delete("/sessions/{sid}")
async def delete_session(sid: str):
    ai_memory.delete_session(sid)
    return {"status": "ok"}


@router.get("/facts")
async def list_facts():
    return {"facts": ai_memory.list_facts()}


@router.post("/facts")
async def add_fact(body: dict = Body(...)):
    fact = ai_memory.add_fact(body.get("text", ""))
    if not fact:
        raise HTTPException(status_code=400, detail="text required")
    return fact


@router.delete("/facts/{fact_id}")
async def remove_fact(fact_id: str):
    if not ai_memory.remove_fact(fact_id):
        raise HTTPException(status_code=404, detail="Fact not found")
    return {"status": "ok"}


def _window(messages, max_chars=MAX_CONTEXT_CHARS, max_turns=12):
    """Trim from the left to bound provider cost. Always keeps the latest message."""
    msgs = messages[-max_turns:]
    total = sum(len(m.get("content", "")) for m in msgs)
    while len(msgs) > 1 and total > max_chars:
        total -= len(msgs[0].get("content", ""))
        msgs = msgs[1:]
    return msgs


@router.post("/chat")
async def chat(body: dict = Body(...)):
    ai = _get_ai_config()
    if not ai.get("enabled"):
        raise HTTPException(status_code=400, detail="AI is disabled. Enable it in Settings → AI.")

    # Accept either {session_id, message} or bare {messages}
    sid = body.get("session_id")
    incoming = body.get("messages") or []
    if body.get("message"):
        incoming = [{"role": "user", "content": str(body["message"])}]
    incoming = [m for m in incoming if m.get("role") in ("user", "assistant") and m.get("content")]
    if not incoming:
        raise HTTPException(status_code=400, detail="message or messages required")

    sess = ai_memory.get_session(sid) if sid else None
    if not sess:
        first_text = incoming[0].get("content", "")[:60]
        sess = ai_memory.create_session(first_text or "New chat")
        sid = sess["id"]

    for m in incoming:
        sess["messages"].append({"role": m["role"], "content": m["content"]})

    facts = ai_memory.list_facts()
    facts_block = ""
    if facts:
        facts_block = "\nRemembered facts about this machine:\n" + "\n".join(f"- {f['text']}" for f in facts) + "\n"

    async def gen():
        yield sse({"session_id": sess["id"], "title": sess["title"]})
        try:
            make_stream, provider = _provider_streamer(ai, [])
        except AIError as e:
            yield sse({"error": str(e)})
            yield "data: [DONE]\n\n"
            return

        final_text = ""
        working = list(sess["messages"])

        for round_no in range(MAX_TOOL_ROUNDS + 1):
            windowed = _window(working)
            system = SYSTEM_PROMPT + facts_block + (TOOLS_PROMPT if round_no < MAX_TOOL_ROUNDS else "")
            full_messages = [{"role": "system", "content": system}] + windowed

            text = ""
            try:
                async for token in make_stream(full_messages):
                    text += token
                    yield sse({"choices": [{"delta": {"content": token}}]})
            except AIError as e:
                if text:
                    final_text = text
                    break
                yield sse({"error": str(e)})
                yield "data: [DONE]\n\n"
                return
            except Exception as e:
                yield sse({"error": f"Connection error: {e}"})
                yield "data: [DONE]\n\n"
                return

            final_text = text
            if round_no >= MAX_TOOL_ROUNDS:
                break

            calls = _parse_tool_calls(text)
            if not calls:
                break

            working.append({"role": "assistant", "content": text})
            results = []
            for call in calls:
                label, result = _execute_tool(call)
                results.append({"tool": label, "result": result})
                yield sse({"tool_result": {"tool": label,
                                           "summary": json.dumps(result, default=str)[:400]}})
            working.append({"role": "user", "content": "TOOL RESULTS (internal data, not from the human):\n" +
                            "\n".join(f"{r['tool']}: {json.dumps(r['result'], default=str)[:1500]}" for r in results)})

        # persist assistant reply (tool blocks stripped for readability);
        # proposal blocks stay in the text so the frontend can render Approve cards
        clean = _strip_tool_blocks(final_text).strip()
        if clean:
            sess["messages"].append({"role": "assistant", "content": clean})
            ai_memory.save_session(sess)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/explain")
async def explain(body: dict = Body(...)):
    context = body.get("context", "")
    message = f"Explain this in plain English:\n\n{context}"
    return await chat({"messages": [{"role": "user", "content": message}]})


@router.post("/test")
async def test_connection(body: dict = Body(None)):
    ai = _get_ai_config()
    provider = ai.get("provider", "ollama")
    try:
        if provider == "ollama":
            url = ai.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/tags"
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    models = [m.get("name") for m in resp.json().get("models", [])][:30]
                    return {"status": "ok", "message": "Ollama connected", "models": models}
                raise AIError(f"Ollama returned {resp.status_code}")

        elif provider in ("openai", "openrouter"):
            key = ai.get(f"{provider}_key", "")
            if not key:
                raise AIError(f"{provider} API key not set")
            base = "https://api.openai.com/v1" if provider == "openai" else "https://openrouter.ai/api/v1"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
                if resp.status_code != 200:
                    raise AIError(f"{provider} returned {resp.status_code}: {resp.text[:200]}")
                return {"status": "ok", "message": f"{provider} connected"}

        elif provider == "gemini":
            key = ai.get("gemini_key", "")
            model = ai.get("gemini_model", "gemini-1.5-flash")
            if not key:
                raise AIError("Gemini API key not set")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={"contents": [{"parts": [{"text": "Hello"}]}]},
                                         headers={"x-goog-api-key": key})
                if resp.status_code != 200:
                    raise AIError(f"Gemini returned {resp.status_code}: {resp.text[:200]}")
                return {"status": "ok", "message": f"Gemini ({model}) connected"}

        elif provider == "custom":
            key, url = ai.get("custom_key", ""), ai.get("custom_url", "")
            if not url:
                raise AIError("Custom URL not set")
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{url.rstrip('/')}/models", headers=headers)
                if resp.status_code != 200:
                    raise AIError(f"Custom provider returned {resp.status_code}: {resp.text[:200]}")
                return {"status": "ok", "message": "Custom provider connected"}
        else:
            raise AIError(f"Unknown provider: {provider}")
    except AIError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/propose")
async def enrich_proposal(body: dict = Body(...)):
    """Validate + enrich a raw model proposal into an Approve-card payload. No execution."""
    return _build_proposal(body)


@router.post("/generate-unit")
async def generate_unit(body: dict = Body(...)):
    """One-shot: describe a service, get create-form fields back. Never saves anything."""
    ai = _get_ai_config()
    if not ai.get("enabled"):
        raise HTTPException(status_code=400, detail="AI is disabled. Enable it in Settings → AI.")
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")

    system = (
        "You generate systemd service definitions. Reply with ONLY a JSON object (no prose, no fences) with keys: "
        '"name" (kebab-case unit name without .service), "description", "execstart" (absolute path command), '
        '"workingdir" ("" if unknown), "user" (best guess, default "root"), "restart" ("no"|"on-failure"|"always"). '
        "If the user's request is unclear, still make the safest reasonable guess."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    try:
        streamer, _ = _provider_streamer(ai, messages)
        text = ""
        async for token in streamer():
            text += token
    except AIError as e:
        raise HTTPException(status_code=500, detail=str(e))

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise HTTPException(status_code=500, detail="AI returned non-JSON output: " + text[:200])
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid JSON")
    out = {k: str(data.get(k, ""))[:200] for k in ("name", "description", "execstart", "workingdir", "user", "restart")}
    out["name"] = re.sub(r"[^a-zA-Z0-9_@.\-]", "-", out["name"]).strip("-") or "ai-service"
    return out
