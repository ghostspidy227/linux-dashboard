import { api } from "../api.js";
import { toast, confirm, esc } from "../ui.js";

let container = null;
let settings = null;
let currentTab = "general";

function render() {
    if (!container || !settings) return;
    const body = document.getElementById("settings-body");
    if (!body) return;

    if (currentTab === "general") renderGeneral(body);
    else if (currentTab === "ai") renderAI(body);
    else if (currentTab === "audit") renderAudit(body);
    else if (currentTab === "about") renderAbout(body);
}

function renderGeneral(body) {
    const sections = settings.sections || {};
    const secKeys = ["metrics", "network", "services", "processes", "packages", "firewall"];

    body.innerHTML = `
        <div class="card">
            <div class="card-header"><span class="card-title">Bind Address</span></div>
            <div style="margin-top:8px;">
                <label style="margin-right:16px;"><input type="radio" name="bind" value="127.0.0.1" ${settings.bind === "127.0.0.1" ? "checked" : ""}> Localhost (127.0.0.1)</label>
                <label><input type="radio" name="bind" value="0.0.0.0" ${settings.bind === "0.0.0.0" ? "checked" : ""}> LAN (0.0.0.0)</label>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span class="card-title">Port</span></div>
            <div style="margin-top:8px;">
                <input type="number" id="setting-port" value="${settings.port || 7000}" style="width:120px;">
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span class="card-title">Password</span></div>
            <div style="margin-top:8px;display:grid;gap:8px;max-width:400px;">
                <input type="password" id="setting-new-pass" placeholder="New password" style="width:100%">
                <button class="btn btn-primary" id="btn-change-pass">Change Password</button>
                <span id="change-pass-result" style="font-size:12px;"></span>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span class="card-title">Dashboard Sections</span></div>
            <div style="margin-top:8px;display:grid;grid-template-columns:1fr 1fr;gap:8px;">
                ${secKeys.map((k) => `
                    <label><input type="checkbox" class="setting-section" data-key="${k}" ${sections[k] ? "checked" : ""}> ${k.charAt(0).toUpperCase() + k.slice(1)}</label>
                `).join("")}
            </div>
        </div>
        <button class="btn btn-primary" id="btn-save-general">Save General Settings</button>
    `;

    document.getElementById("btn-save-general")?.addEventListener("click", async () => {
        const bind = body.querySelector('input[name="bind"]:checked')?.value || "127.0.0.1";
        const port = parseInt(document.getElementById("setting-port")?.value || "7000");
        const newSections = {};
        body.querySelectorAll(".setting-section").forEach((cb) => {
            newSections[cb.dataset.key] = cb.checked;
        });
        await save({ bind, port, sections: newSections });
    });

    document.getElementById("btn-change-pass")?.addEventListener("click", async () => {
        const el = document.getElementById("change-pass-result");
        const pass = document.getElementById("setting-new-pass")?.value || "";
        if (!el) return;
        if (!pass) { el.textContent = "Password cannot be empty"; el.style.color = "var(--danger)"; return; }
        el.textContent = "Changing...";
        try {
            await api.post("/auth/change-password", { new_password: pass });
            el.textContent = "Password changed successfully";
            el.style.color = "var(--success)";
            document.getElementById("setting-new-pass").value = "";
        } catch (err) {
            el.textContent = "Failed: " + err.message;
            el.style.color = "var(--danger)";
        }
    });
}

function renderAI(body) {
    const ai = settings.ai || {};

    body.innerHTML = `
        <div class="card">
            <div class="card-header" style="justify-content:space-between;">
                <span class="card-title">AI Integration</span>
                <label><input type="checkbox" id="ai-enabled" ${ai.enabled ? "checked" : ""}> Enabled</label>
            </div>
            <div id="ai-settings" style="margin-top:12px;display:${ai.enabled ? "grid" : "none"};gap:12px;">
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Provider</label>
                    <select id="ai-provider" style="width:100%">
                        <option value="ollama" ${ai.provider === "ollama" ? "selected" : ""}>Ollama (local)</option>
                        <option value="openai" ${ai.provider === "openai" ? "selected" : ""}>OpenAI</option>
                        <option value="openrouter" ${ai.provider === "openrouter" ? "selected" : ""}>OpenRouter</option>
                        <option value="gemini" ${ai.provider === "gemini" ? "selected" : ""}>Gemini</option>
                        <option value="custom" ${ai.provider === "custom" ? "selected" : ""}>Custom OpenAI-compatible</option>
                    </select>
                </div>
                <div id="ai-ollama-url" class="ai-provider-field" style="display:${ai.provider === "ollama" ? "block" : "none"}">
                    <label style="font-size:12px;color:var(--text-muted)">Ollama URL</label>
                    <input type="text" id="ai-ollama-url-val" value="${esc(ai.ollama_url || "http://localhost:11434")}" style="width:100%">
                </div>
                <div id="ai-custom-url" class="ai-provider-field" style="display:${ai.provider === "custom" ? "block" : "none"}">
                    <label style="font-size:12px;color:var(--text-muted)">Base URL (OpenAI-compatible)</label>
                    <input type="text" id="ai-custom-url-val" value="${esc(ai.custom_url || "")}" placeholder="http://localhost:1234/v1" style="width:100%">
                </div>
                <div class="ai-provider-field" style="display:${ai.provider !== "ollama" ? "block" : "none"}">
                    <label style="font-size:12px;color:var(--text-muted)">API Key ${ai.provider === "custom" ? "(optional)" : ""}</label>
                    <input type="password" id="ai-key" value="${esc(getKeyForProvider(ai))}" placeholder="${ai.provider === "custom" ? "optional" : "Enter API key"}" style="width:100%">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Model</label>
                    <input type="text" id="ai-model" list="ai-model-list" value="${esc(ai[`${ai.provider}_model`] || "")}" style="width:100%">
                    <datalist id="ai-model-list"></datalist>
                </div>
                <button class="btn" id="btn-ai-test">Test Connection</button>
                <span id="ai-test-result" style="font-size:12px;margin-left:8px;"></span>
            </div>
        </div>
        <button class="btn btn-primary" id="btn-save-ai">Save AI Settings</button>
    `;

    document.getElementById("ai-enabled")?.addEventListener("change", (e) => {
        document.getElementById("ai-settings").style.display = e.target.checked ? "grid" : "none";
    });

    document.getElementById("ai-provider")?.addEventListener("change", (e) => {
        const p = e.target.value;
        document.getElementById("ai-ollama-url").style.display = p === "ollama" ? "block" : "none";
        document.getElementById("ai-custom-url").style.display = p === "custom" ? "block" : "none";
        document.querySelectorAll(".ai-provider-field").forEach((el) => {
            if (el.id !== "ai-ollama-url" && el.id !== "ai-custom-url") el.style.display = p !== "ollama" ? "block" : "none";
        });
        document.getElementById("ai-model").value = settings.ai?.[`${p}_model`] || "";
        document.getElementById("ai-key").value = getKeyForProvider({...settings.ai, provider: p});
    });

    document.getElementById("btn-ai-test")?.addEventListener("click", async () => {
        const el = document.getElementById("ai-test-result");
        if (!el) return;
        await saveAI({ silent: true }); // test the saved config
        el.innerHTML = '<span class="spinner"></span> Testing...';
        try {
            const res = await api.post("/ai/test", {});
            el.textContent = res.message || "Connected OK!";
            el.style.color = "var(--success)";
            const list = document.getElementById("ai-model-list");
            if (list && res.models) {
                list.innerHTML = res.models.map((m) => `<option value="${esc(m)}">`).join("");
            }
        } catch (err) {
            el.textContent = "Failed: " + err.message;
            el.style.color = "var(--danger)";
        }
    });

    document.getElementById("btn-save-ai")?.addEventListener("click", () => saveAI({}));
}

async function saveAI({ silent = false } = {}) {
    const ai = settings.ai || {};
    const provider = document.getElementById("ai-provider")?.value || "ollama";
    // Start from the server view (masked keys preserved), overlay only what the form edits.
    const newAi = {
        ...ai,
        enabled: document.getElementById("ai-enabled")?.checked || false,
        provider,
        ollama_url: document.getElementById("ai-ollama-url-val")?.value || ai.ollama_url || "http://localhost:11434",
        custom_url: document.getElementById("ai-custom-url-val")?.value ?? (ai.custom_url || ""),
    };
    const modelVal = document.getElementById("ai-model")?.value || "";
    newAi[`${provider}_model`] = modelVal;
    const keyVal = document.getElementById("ai-key")?.value || "";
    if (provider !== "ollama") {
        newAi[`${provider}_key`] = keyVal;
    }
    try {
        await api.put("/settings", { ai: newAi });
        settings.ai = { ...newAi };
        window.AI_ENABLED = !!newAi.enabled;
        if (!silent) toast("AI settings saved", "success");
    } catch (err) {
        if (!silent) toast("Failed to save: " + err.message, "error");
    }
}

function getKeyForProvider(ai) {
    if (ai.provider === "openai") return ai.openai_key || "";
    if (ai.provider === "openrouter") return ai.openrouter_key || "";
    if (ai.provider === "gemini") return ai.gemini_key || "";
    return "";
}

async function renderAudit(body) {
    body.innerHTML = '<div class="placeholder"><span class="spinner"></span> Loading audit log...</div>';
    try {
        const data = await api.get("/audit");
        const entries = data.entries || [];
        const rows = entries.slice(0, 500).map((e) => `
            <tr>
                <td style="font-size:11px;color:var(--text-dim)">${new Date(e.timestamp).toLocaleString()}</td>
                <td style="font-size:11px">${esc(e.section)}</td>
                <td style="font-size:11px">${esc(e.action)}</td>
                <td style="font-size:11px">${esc(e.target)}</td>
                <td>${e.result === "success" ? '<span class="badge badge-active">success</span>' : '<span class="badge badge-error">error</span>'}</td>
                <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;color:var(--text-dim)" title="${esc(e.details || "")}">${esc(e.details || "")}</td>
            </tr>
        `).join("");

        body.innerHTML = `
            <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;">
                <span style="color:var(--text-muted)">${entries.length} entries</span>
                <button class="btn btn-danger btn-sm" id="btn-clear-audit">Clear Log</button>
            </div>
            <div class="table-container">
                <table>
                    <thead><tr><th>Timestamp</th><th>Section</th><th>Action</th><th>Target</th><th>Result</th><th>Details</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="6"><div class="placeholder">No audit entries.</div></td></tr>'}</tbody>
                </table>
            </div>
        `;

        document.getElementById("btn-clear-audit")?.addEventListener("click", async () => {
            if (!(await confirm("Clear the entire audit log?"))) return;
            try {
                await api.delete("/audit");
                toast("Audit log cleared", "success");
                renderAudit(body);
            } catch (err) { toast("Failed: " + err.message, "error"); }
        });
    } catch (err) {
        body.innerHTML = `<div class="placeholder">Error: ${err.message}</div>`;
    }
}

function renderAbout(body) {
    body.innerHTML = `
        <div class="card">
            <div class="card-header"><span class="card-title">About</span></div>
            <div style="margin-top:8px;font-size:13px;line-height:1.8;">
                <div><strong>Linux Control Dashboard</strong></div>
                <div style="color:var(--text-dim)">Version 1.0.0</div>
                <div style="margin-top:8px;">A self-hosted web dashboard for full visibility and control over your Linux machine.</div>
                <div style="margin-top:12px;color:var(--text-muted);font-size:12px;">
                    <strong>Remote Access:</strong> For secure remote access, use a VPN like Tailscale, WireGuard, or ZeroTier to connect to your machine, then access the dashboard through the VPN IP.
                </div>
                <div style="margin-top:8px;color:var(--text-muted);font-size:12px;">
                    <strong>GitHub:</strong> <a href="https://github.com/ghostspidy227/linux-dashboard" target="_blank" rel="noopener">github.com/ghostspidy227/linux-dashboard</a>
                </div>
            </div>
        </div>
    `;
}

async function save(data) {
    try {
        await api.put("/settings", data);
        toast("Settings saved. Restart the server for bind/port changes to take effect.", "success");
    } catch (err) {
        toast("Failed to save: " + err.message, "error");
    }
}

function switchTab(name) {
    currentTab = name;
    document.querySelectorAll(".settings-tab").forEach((el) => {
        el.classList.toggle("active", el.dataset.tab === name);
    });
    render();
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div style="margin-bottom:16px;display:flex;gap:4px;">
            <button class="btn settings-tab active" data-tab="general">General</button>
            <button class="btn settings-tab" data-tab="ai">AI</button>
            <button class="btn settings-tab" data-tab="audit">Audit Log</button>
            <button class="btn settings-tab" data-tab="about">About</button>
        </div>
        <div id="settings-body"></div>
    `;

    document.querySelectorAll(".settings-tab").forEach((btn) => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
}

export async function load() {
    try {
        settings = await api.get("/settings");
        render();
    } catch (err) {
        toast("Error loading settings: " + err.message, "error");
    }
}

export function destroy() {
    container = null;
    settings = null;
}
