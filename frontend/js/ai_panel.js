import { api } from "./api.js";
import { esc, toast, confirm } from "./ui.js";

// Persistent AI chat panel: threads, long-term facts, read-only tool cards,
// proposal Approve/Reject cards. All state client-side mirrors server truth in
// data/ai_sessions — the panel survives section switches (it lives outside #section-body).

let sessionId = null;
let streaming = false;
let abortCtrl = null;
const messagesEl = () => document.getElementById("ai-messages");

// ── Markdown-lite (everything escaped; only bold/code/headings/lists) ──

function renderProse(t) {
    let h = esc(t);
    h = h.replace(/^#### (.*)$/gm, "<h5>$1</h5>");
    h = h.replace(/^### (.*)$/gm, "<h4>$1</h4>");
    h = h.replace(/^## (.*)$/gm, "<h3>$1</h3>");
    h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/`([^`]+?)`/g, "<code>$1</code>");
    h = h.replace(/^[-*] (.*)$/gm, "&bull; $1");
    return h.replace(/\n/g, "<br>");
}

function renderMarkdown(text) {
    text = text.replace(/```tool[\s\S]*?```/g, ""); // tool calls render as cards instead
    text = text.replace(/<think>[\s\S]*?<\/think>/g, "").replace(/<\/?think>/g, ""); // stray reasoning tags
    const parts = text.split("```");
    let html = "";
    for (let i = 0; i < parts.length; i++) {
        if (i % 2 === 0) {
            html += renderProse(parts[i]);
        } else {
            const lines = parts[i].split("\n");
            const first = (lines[0] || "").trim();
            const lang = first && !first.includes(" ") ? first : "code";
            const code = (lang !== "code" ? lines.slice(1) : lines).join("\n").trim();
            html += `<div class="ai-code"><div class="ai-code-head"><span>${esc(lang)}</span><button class="ai-copy">Copy</button></div><pre>${esc(code)}</pre></div>`;
        }
    }
    return html;
}

function parseProposals(text) {
    const out = [];
    for (const m of text.matchAll(/```proposal\s*(\{[\s\S]*?\})\s*```/g)) {
        try { out.push(JSON.parse(m[1])); } catch { /* ignore malformed */ }
    }
    return out;
}

// ── DOM builders ──

function addBubble(role, content, isMarkdown = false) {
    const el = messagesEl();
    if (!el) return null;
    const div = document.createElement("div");
    div.className = `card ai-msg-${role === "user" ? "user" : "assistant"}`;
    div.style.marginBottom = "8px";
    const label = document.createElement("div");
    label.style.cssText = "font-size:11px;color:var(--text-dim);margin-bottom:4px;";
    label.textContent = role === "user" ? "You" : "AI";
    div.appendChild(label);
    const body = document.createElement("div");
    body.className = "ai-content";
    body.style.cssText = "font-size:13px;white-space:pre-wrap;";
    if (isMarkdown) {
        body.style.whiteSpace = "normal";
        body.innerHTML = renderMarkdown(content);
    } else {
        body.textContent = content;
    }
    div.appendChild(body);
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
    return { div, body };
}

function addToolCard(tool, summary) {
    const el = messagesEl();
    if (!el) return;
    const div = document.createElement("div");
    div.className = "card ai-tool-card";
    div.innerHTML = `<span class="ai-tool-icon">🔍</span> <strong>${esc(tool)}</strong> <span class="ai-tool-summary">${esc(summary)}</span>`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

// ── Proposals ──

async function approveProposal(p, card) {
    try {
        if (p.type === "firewall_rule") {
            await api.post("/firewall/rules", p.rule || p);
            card.innerHTML = `<span style="color:var(--success)">✓ Rule applied</span>`;
        } else if (p.type === "service_action") {
            await api.post(`/services/${encodeURIComponent(p.name)}/${p.action}`);
            card.innerHTML = `<span style="color:var(--success)">✓ Service ${esc(p.action)} done</span>`;
        } else if (p.type === "package_action") {
            let res;
            if (p.action === "remove") {
                res = await api.delete(`/packages/${encodeURIComponent(p.manager)}/${encodeURIComponent(p.name)}`);
            } else {
                res = await api.post(`/packages/${encodeURIComponent(p.manager)}/${p.action}`, { name: p.name });
            }
            card.innerHTML = `<span style="color:var(--warning)">⏳ ${esc(p.action)} running (job ${esc(res.job_id)}) — check Packages section</span>`;
        } else if (p.type === "process_kill") {
            if (!(await confirm(`Kill process ${p.pid} (${p.name || "unknown"})? This force-terminates it.`))) return;
            await api.post(`/processes/${p.pid}/kill`);
            card.innerHTML = `<span style="color:var(--success)">✓ Process killed</span>`;
        }
    } catch (err) {
        card.innerHTML = `<span style="color:var(--danger)">✗ ${esc(err.message)}</span>`;
    }
}

async function addProposalCards(containerEl, proposals) {
    for (const raw of proposals) {
        const card = document.createElement("div");
        card.className = "card ai-proposal";
        let p = raw;
        try {
            // validate + enrich (label/command) server-side — single source of truth
            p = await api.post("/ai/propose", raw);
        } catch { /* fall through with raw */ }
        if (p.type === "error" || !p.label) {
            card.innerHTML = `<div class="ai-proposal-title" style="color:var(--danger)">✗ Invalid proposal</div><div style="font-size:12px">${esc(p.message || JSON.stringify(raw))}</div>`;
        } else {
            card.innerHTML = `
                <div class="ai-proposal-title">⚡ ${esc(p.label)}</div>
                ${p.command ? `<pre class="ai-proposal-cmd">${esc(p.command)}</pre>` : ""}
                <div style="display:flex;gap:8px;margin-top:8px;">
                    <button class="btn btn-sm btn-primary ai-approve">Approve</button>
                    <button class="btn btn-sm ai-reject">Reject</button>
                </div>`;
            card.querySelector(".ai-approve").addEventListener("click", () => approveProposal(p, card));
            card.querySelector(".ai-reject").addEventListener("click", () => { card.innerHTML = `<span style="color:var(--text-dim)">Rejected</span>`; });
        }
        containerEl.appendChild(card);
    }
    messagesEl().scrollTop = messagesEl().scrollHeight;
}

// ── Streaming send ──

function setStreamingUI(on) {
    const btn = document.getElementById("btn-ai-send");
    const stop = document.getElementById("btn-ai-stop");
    if (btn) btn.disabled = on;
    if (stop) stop.style.display = on ? "inline-block" : "none";
}

async function send(text) {
    if (streaming || !text) return;
    streaming = true;
    setStreamingUI(true);
    addBubble("user", text);

    abortCtrl = new AbortController();
    const aiDiv = addBubble("assistant", "");
    const contentEl = aiDiv.body;
    contentEl.textContent = "…";

    let fullText = "";
    let buffer = "";

    try {
        const res = await fetch("/api/ai/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${localStorage.getItem("token")}`,
            },
            signal: abortCtrl.signal,
            body: JSON.stringify({ session_id: sessionId, message: text }),
        });

        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { detail = JSON.parse(await res.text()).detail || detail; } catch { /* keep */ }
            contentEl.textContent = "Error: " + detail;
            return;
        }

        contentEl.textContent = "";
        const reader = res.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            for (const line of lines) {
                const s = line.trim();
                if (!s.startsWith("data: ")) continue;
                const payload = s.slice(6);
                if (payload === "[DONE]") continue;
                try {
                    const obj = JSON.parse(payload);
                    if (obj.session_id) sessionId = obj.session_id;
                    if (obj.error) { contentEl.innerHTML += `<span style="color:var(--danger)">${esc(obj.error)}</span>`; }
                    if (obj.tool_result) addToolCard(obj.tool_result.tool, obj.tool_result.summary || "");
                    const token = obj.choices?.[0]?.delta?.content;
                    if (token) {
                        fullText += token;
                        contentEl.innerHTML = renderMarkdown(fullText);
                        messagesEl().scrollTop = messagesEl().scrollHeight;
                    }
                } catch { /* partial json — skip */ }
            }
        }

        // render Approve cards for any proposals in the final text
        const proposals = parseProposals(fullText);
        if (proposals.length) await addProposalCards(aiDiv.div, proposals);

        if (!fullText.trim() && !contentEl.innerHTML.trim()) contentEl.textContent = "No response received.";
    } catch (err) {
        if (err.name === "AbortError") {
            contentEl.innerHTML += `<div style="color:var(--text-dim)">Stopped.</div>`;
        } else {
            contentEl.textContent = "Connection error: " + err.message;
        }
    } finally {
        streaming = false;
        setStreamingUI(false);
        abortCtrl = null;
    }
}

// ── Threads & facts ──

function swapView(name, buildFn) {
    const el = messagesEl();
    if (!el) return;
    // stash current messages DOM
    let stash = document.getElementById("ai-view-main");
    if (!stash) {
        stash = document.createElement("div");
        stash.id = "ai-view-main";
        while (el.firstChild) stash.appendChild(el.firstChild);
    }
    el.innerHTML = "";
    const view = document.createElement("div");
    view.id = `ai-view-${name}`;
    buildFn(view, () => {
        view.remove();
        el.innerHTML = "";
        el.appendChild(stash);
        stash.id = "ai-view-main";
    });
    el.appendChild(view);
}

async function showThreads() {
    swapView("threads", async (view, close) => {
        view.innerHTML = `<div class="placeholder"><span class="spinner"></span> Loading…</div>`;
        let sessions = [];
        try { sessions = (await api.get("/ai/sessions")).sessions || []; } catch { /* */ }
        view.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <strong>Chats</strong>
                <button class="btn btn-sm" id="ai-threads-close">Back</button>
            </div>`;
        sessions.forEach((s) => {
            const row = document.createElement("div");
            row.className = "card";
            row.style.cssText = "margin-bottom:6px;padding:8px 10px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;";
            row.innerHTML = `<div style="min-width:0;"><div style="font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(s.title || "Untitled")}</div>
                <div style="font-size:10px;color:var(--text-dim)">${s.message_count} messages</div></div>
                <button class="btn-icon btn-danger" title="Delete">&times;</button>`;
            row.addEventListener("click", async (e) => {
                if (e.target.closest(".btn-danger")) {
                    await api.delete(`/ai/sessions/${s.id}`);
                    if (sessionId === s.id) sessionId = null;
                    row.remove();
                    return;
                }
                close(); // restore main view first, or it wipes the loaded thread
                await loadThread(s.id);
            });
            view.appendChild(row);
        });
        view.querySelector("#ai-threads-close").addEventListener("click", close);
    });
}

async function showFacts() {
    swapView("facts", async (view, close) => {
        view.innerHTML = `<div class="placeholder"><span class="spinner"></span> Loading…</div>`;
        let facts = [];
        try { facts = (await api.get("/ai/facts")).facts || []; } catch { /* */ }
        view.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <strong>Remembered facts</strong>
                <button class="btn btn-sm" id="ai-facts-close">Back</button>
            </div>
            <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px;">The AI saves lasting notes about this machine here and reads them in every chat.</div>`;
        facts.forEach((f) => {
            const row = document.createElement("div");
            row.className = "card";
            row.style.cssText = "margin-bottom:6px;padding:8px 10px;display:flex;justify-content:space-between;align-items:center;";
            row.innerHTML = `<div style="font-size:12px;">${esc(f.text)}</div><button class="btn-icon btn-danger" title="Forget">&times;</button>`;
            row.querySelector(".btn-danger").addEventListener("click", async () => {
                await api.delete(`/ai/facts/${f.id}`);
                row.remove();
            });
            view.appendChild(row);
        });
        view.querySelector("#ai-facts-close").addEventListener("click", close);
    });
}

async function loadThread(sid) {
    try {
        const sess = await api.get(`/ai/sessions/${sid}`);
        sessionId = sid;
        const el = messagesEl();
        el.innerHTML = "";
        for (const m of sess.messages || []) {
            addBubble(m.role, m.content, m.role === "assistant");
        }
    } catch (err) {
        toast("Failed to load chat: " + err.message, "error");
    }
}

function newChat() {
    sessionId = null;
    const el = messagesEl();
    if (el) el.innerHTML = "";
    addBubble("assistant", "New chat. Ask me anything about this machine, or attach a row with the ✦ button.");
}

// ── Init ──

export function initAIPanel() {
    const sendBtn = document.getElementById("btn-ai-send");
    const input = document.getElementById("ai-chat-input");

    sendBtn?.addEventListener("click", () => {
        const text = input?.value.trim();
        if (!text) return;
        input.value = "";
        send(text);
    });
    input?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            sendBtn?.click();
        }
    });

    document.getElementById("btn-ai-stop")?.addEventListener("click", () => abortCtrl?.abort());
    document.getElementById("btn-ai-new")?.addEventListener("click", newChat);
    document.getElementById("btn-ai-threads")?.addEventListener("click", showThreads);
    document.getElementById("btn-ai-facts")?.addEventListener("click", showFacts);
    document.getElementById("btn-close-ai")?.addEventListener("click", () => {
        document.getElementById("ai-panel").classList.add("hidden");
    });

    // delegated copy buttons
    messagesEl()?.addEventListener("click", (e) => {
        const btn = e.target.closest(".ai-copy");
        if (!btn) return;
        const code = btn.closest(".ai-code")?.querySelector("pre")?.textContent || "";
        navigator.clipboard?.writeText(code).then(() => {
            btn.textContent = "Copied";
            setTimeout(() => { btn.textContent = "Copy"; }, 1200);
        });
    });
}

// Attach an item's context and ask — used by every section's ✦ button.
window.openAIPanel = function(context, section) {
    const panel = document.getElementById("ai-panel");
    if (!panel) return;
    panel.classList.remove("hidden");
    const prefix = section ? `[${section}] ` : "";
    send(`${prefix}${context}`);
};
