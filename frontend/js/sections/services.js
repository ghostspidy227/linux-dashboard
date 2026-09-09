import { api } from "../api.js";
import { toast, confirm, openModal, closeModal, esc } from "../ui.js";

let container = null;
let services = [];
let sortKey = "name";
let sortDir = 1;

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

const STATE_MAP = {
    active: { cls: "badge-active", label: "active" },
    inactive: { cls: "badge-inactive", label: "inactive" },
    failed: { cls: "badge-error", label: "failed" },
    masked: { cls: "badge-warning", label: "masked" },
    activating: { cls: "badge-warning", label: "activating" },
    deactivating: { cls: "badge-warning", label: "deactivating" },
    reloading: { cls: "badge-warning", label: "reloading" },
};

const ENABLED_MAP = {
    enabled: { cls: "badge-active", label: "enabled" },
    disabled: { cls: "badge-inactive", label: "disabled" },
    static: { cls: "badge-sleeping", label: "static" },
    indirect: { cls: "badge-sleeping", label: "indirect" },
    generated: { cls: "badge-sleeping", label: "generated" },
    masked: { cls: "badge-warning", label: "masked" },
    "enabled-runtime": { cls: "badge-active", label: "runtime" },
};

function stateBadge(state) {
    const info = STATE_MAP[state] || { cls: "badge-sleeping", label: state };
    return `<span class="badge ${info.cls}">${info.label}</span>`;
}

function enabledBadge(state) {
    const info = ENABLED_MAP[state] || { cls: "badge-sleeping", label: state || "unknown" };
    return `<span class="badge ${info.cls}">${info.label}</span>`;
}

function render() {
    const tbody = document.getElementById("svc-tbody");
    if (!tbody) return;

    const stateF = document.getElementById("svc-state-filter")?.value || "";
    const enabledF = document.getElementById("svc-enabled-filter")?.value || "";
    const typeF = document.getElementById("svc-type-filter")?.value || "";
    const searchF = document.getElementById("svc-search")?.value?.toLowerCase() || "";

    let filtered = services.filter((s) => {
        if (stateF && s.active !== stateF) return false;
        if (enabledF && s.enabled !== enabledF) return false;
        if (typeF && s.type !== typeF) return false;
        if (searchF) {
            const txt = `${s.name} ${s.description}`.toLowerCase();
            if (!txt.includes(searchF)) return false;
        }
        return true;
    });

    filtered.sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        if (!isNaN(av) && !isNaN(bv)) return (Number(av) - Number(bv)) * sortDir;
        return String(av).localeCompare(String(bv)) * sortDir;
    });

    const uniqueTypes = [...new Set(services.map((s) => s.type).filter(Boolean))].sort();
    const typeSel = document.getElementById("svc-type-filter");
    if (typeSel && typeSel.options.length <= 2) {
        uniqueTypes.forEach((t) => {
            const opt = document.createElement("option");
            opt.value = t;
            opt.textContent = t;
            typeSel.appendChild(opt);
        });
    }

    tbody.innerHTML = filtered.map((s) => `
        <tr class="row" data-name="${esc(s.name)}">
            <td>${esc(s.name)}</td>
            <td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--text-dim)" title="${esc(s.description)}">${esc(s.description)}</td>
            <td>${stateBadge(s.active)}</td>
            <td>${enabledBadge(s.enabled)}</td>
            <td style="font-size:11px;color:var(--text-dim)">${s.type || ""}</td>
            <td class="actions">
                <button class="btn-icon btn-explain btn-explain-item" title="Ask AI about this">&#10023;</button>
                ${s.active === "active" ? `
                    <button class="btn-icon btn-danger btn-svc-stop" data-name="${s.name}" title="Stop">&#9632;</button>
                    <button class="btn-icon btn-svc-restart" data-name="${s.name}" title="Restart" style="color:var(--warning)">&#8635;</button>
                ` : (s.active === "failed" || s.active === "inactive") ? `
                    <button class="btn-icon btn-start btn-svc-start" data-name="${s.name}" title="Start">&#9654;</button>
                ` : ""}
                ${s.enabled === "enabled" || s.enabled === "enabled-runtime" ? `
                    <button class="btn-icon btn-danger btn-svc-disable" data-name="${s.name}" title="Disable" style="font-size:12px">&#9746;</button>
                ` : (s.enabled === "disabled" || s.enabled === "static") ? `
                    <button class="btn-icon btn-svc-enable" data-name="${s.name}" title="Enable" style="color:var(--success);font-size:12px">&#9745;</button>
                ` : ""}
                <button class="btn-icon btn-svc-delete" data-name="${s.name}" title="Delete" style="color:var(--danger);font-size:12px">&#128465;</button>
            </td>
        </tr>
    `).join("");

    // Row click → detail
    tbody.querySelectorAll("tr.row").forEach((row) => {
        row.addEventListener("click", () => showDetail(row.dataset.name));
    });

    // Action handlers
    const actions = [
        ["btn-svc-start", doStart],
        ["btn-svc-stop", doStop],
        ["btn-svc-restart", doRestart],
        ["btn-svc-enable", doEnable],
        ["btn-svc-disable", doDisable],
        ["btn-svc-delete", doDelete],
    ];
    for (const [cls, fn] of actions) {
        tbody.querySelectorAll(`.${cls}`).forEach((btn) => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                fn(btn.dataset.name);
            });
        });
    }

    // Explain button
    tbody.querySelectorAll(".btn-explain-item").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!window.AI_ENABLED) { toast("Enable AI in Settings first", "warning"); return; }
            const row = btn.closest("tr.row");
            const name = row?.dataset?.name;
            const s = services.find(x => x.name === name);
            const ctx = s ? `Service: ${s.name}\nState: ${s.active} (${s.sub})\nEnabled: ${s.enabled}\nType: ${s.type}\nDescription: ${s.description}` : `Service: ${name}`;
            window.openAIPanel(ctx, "services");
        });
    });
}

function setSort(key) {
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = key === "name" ? 1 : -1; }
    render();
    document.querySelectorAll(".svc-sort").forEach((el) => el.textContent = "");
    const el = document.querySelector(`.svc-sort[data-key="${key}"]`);
    if (el) el.textContent = sortDir > 0 ? " \\25B2" : " \\25BC";
}

async function loadServices() {
    const tbody = document.getElementById("svc-tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="6"><div class="placeholder"><span class="spinner"></span> Loading services...</div></td></tr>`;
    try {
        const data = await api.get("/services");
        services = data.services || [];
        render();
    } catch (err) {
        toast("Error loading services: " + err.message, "error");
        if (tbody) tbody.innerHTML = `<tr><td colspan="6"><div class="placeholder">Error: ${err.message}</div></td></tr>`;
    }
}

async function showDetail(name) {
    try {
        const data = await api.get(`/services/${name}`);
        const detail = await api.get(`/services/${name}/logs`);
        const logs = detail.logs || [];

        const deps = data.dependencies || {};
        const depHtml = Object.entries(deps).map(([k, v]) => {
            if (!v || v.length === 0) return "";
            return `<div style="margin-bottom:4px;"><strong>${esc(k)}:</strong> ${v.map(esc).join(", ")}</div>`;
        }).join("");

        const runtime = data.runtime || {};
        const runtimeHtml = Object.entries(runtime)
            .filter(([_, v]) => v)
            .map(([k, v]) => `<div><strong>${esc(k)}:</strong> ${esc(v)}</div>`)
            .join("");

        const procs = data.processes || [];
        const procRows = procs.map((p) => `
            <tr>
                <td>${p.pid}</td>
                <td>${esc(p.name)}</td>
                <td style="color:var(--accent)">${p.cpu}%</td>
                <td>${formatBytes(p.rss)}</td>
                <td class="actions"><button class="btn btn-sm btn-svc-proc" data-pid="${p.pid}">Details</button></td>
            </tr>
        `).join("");
        const procHtml = procs.length ? `
            <div style="margin-bottom:12px;">
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">Processes (${procs.length})</h4>
                <table><thead><tr><th>PID</th><th>Name</th><th>CPU</th><th>RAM</th><th></th></tr></thead><tbody>${procRows}</tbody></table>
            </div>` : "";

        // Build the detail modal with editable unit file and journal logs
        const detailHtml = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;font-size:13px;">
                ${runtimeHtml}
            </div>
            ${procHtml}
            ${depHtml ? `
            <div style="margin-bottom:12px;">
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">Dependencies</h4>
                <div class="card" style="padding:10px;font-size:12px;">${depHtml}</div>
            </div>` : ""}
            ${data.status_text ? `
            <details style="margin-bottom:12px;">
                <summary style="color:var(--text-muted);font-size:12px;cursor:pointer;">systemctl status</summary>
                <pre class="card" style="padding:10px;font-size:11px;overflow-x:auto;white-space:pre;max-height:200px;overflow-y:auto;margin-top:4px;">${esc(data.status_text)}</pre>
            </details>` : ""}
            <div style="margin-bottom:12px;">
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">Unit File</h4>
                <textarea id="svc-unit-editor-${esc(name)}" style="width:100%;height:200px;font-family:monospace;font-size:12px;background:var(--bg);color:var(--text);border:1px solid var(--card-border);border-radius:var(--radius);padding:10px;resize:vertical;">${esc(data.unit_content || "")}</textarea>
                <div id="svc-verify-errors" class="card" style="padding:8px;font-size:11px;margin-top:6px;display:${data.unit_content ? "block" : "none"};"></div>
            </div>
            <details style="margin-bottom:12px;">
                <summary style="color:var(--text-muted);font-size:12px;cursor:pointer;">Journal Log (last ${logs.length})</summary>
                <pre class="card" style="padding:10px;font-size:10px;max-height:250px;overflow:auto;white-space:pre;font-family:monospace;margin-top:4px;">${logs.map((l) => esc(l)).join("\n")}</pre>
            </details>
        `;

        const modalEl = openModal(`Service: ${name} — ${data.properties?.ActiveState || "unknown"}`, detailHtml, [
            {
                label: "Save Unit File",
                cls: "btn btn-primary",
                onClick: async () => {
                    const textarea = document.getElementById(`svc-unit-editor-${name}`);
                    if (!textarea) return false;
                    const errBox = document.getElementById("svc-verify-errors");
                    if (errBox) errBox.innerHTML = "";
                    try {
                        const res = await api.put(`/services/${name}/unit`, { content: textarea.value });
                        if (res.status === "verify_failed") {
                            if (errBox) {
                                errBox.innerHTML = `<strong>systemd-analyze:</strong>` + res.errors.map((e2) =>
                                    `<div style="color:var(--danger)">${e2.line ? "line " + e2.line + ": " : ""}${esc(e2.message)}</div>`).join("") +
                                    `<button class="btn btn-sm" id="svc-save-force" style="margin-top:6px;">Save Anyway</button>`;
                                document.getElementById("svc-save-force")?.addEventListener("click", async () => {
                                    try {
                                        await api.put(`/services/${name}/unit`, { content: textarea.value, force: true });
                                        toast("Unit file saved (forced)", "warning");
                                        errBox.innerHTML = "";
                                    } catch (e3) { toast("Failed: " + e3.message, "error"); }
                                });
                            } else {
                                toast(res.errors.map((e2) => e2.message).join("; "), "error", 8000);
                            }
                        } else {
                            toast("Unit file saved", "success");
                        }
                    } catch (err) {
                        toast("Failed to save: " + err.message, "error");
                    }
                    return false;
                },
            },
            { label: "Close", cls: "btn", onClick: () => true },
        ]);

        modalEl.querySelectorAll(".btn-svc-proc").forEach((btn) => {
            btn.addEventListener("click", () => {
                window.navigateSection?.("processes", btn.dataset.pid);
            });
        });
    } catch (err) {
        toast("Error loading service detail: " + err.message, "error");
    }
}

async function doStart(name) {
    try {
        const res = await api.post(`/services/${name}/start`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}
async function doStop(name) {
    if (!(await confirm(`Stop service ${name}?`))) return;
    try {
        const res = await api.post(`/services/${name}/stop`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}
async function doRestart(name) {
    if (!(await confirm(`Restart service ${name}?`))) return;
    try {
        const res = await api.post(`/services/${name}/restart`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}
async function doEnable(name) {
    try {
        const res = await api.post(`/services/${name}/enable`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}
async function doDisable(name) {
    if (!(await confirm(`Disable service ${name}?`))) return;
    try {
        const res = await api.post(`/services/${name}/disable`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}
async function doDelete(name) {
    if (!(await confirm(`Delete service ${name}? This will stop, disable, and remove the unit file.`))) return;
    try {
        const res = await api.delete(`/services/${name}`);
        toast(res.message, "success");
        loadServices();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}

const TEMPLATES = {
    "daemon": { restart: "on-failure", label: "Simple daemon (restarts on failure)" },
    "oneshot": { restart: "no", label: "One-shot script (runs once at boot)" },
    "always": { restart: "always", label: "Always-running daemon" },
};

function showCreateWizard() {
    const html = `
        <div style="display:grid;gap:12px;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div><label style="font-size:12px;color:var(--text-muted)">Service Name *</label><input type="text" id="cr-name" style="width:100%" placeholder="my-service"></div>
                <div><label style="font-size:12px;color:var(--text-muted)">Template</label>
                    <select id="cr-template" style="width:100%">
                        ${Object.entries(TEMPLATES).map(([k, t]) => `<option value="${k}">${t.label}</option>`).join("")}
                    </select>
                </div>
            </div>
            <div><label style="font-size:12px;color:var(--text-muted)">Description</label><input type="text" id="cr-desc" style="width:100%" placeholder="My custom service"></div>
            <div><label style="font-size:12px;color:var(--text-muted)">Command (ExecStart) *</label><input type="text" id="cr-exec" style="width:100%" placeholder="/usr/bin/my-daemon --flag"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div><label style="font-size:12px;color:var(--text-muted)">Working Directory</label><input type="text" id="cr-cwd" style="width:100%" placeholder="/opt/myapp"></div>
                <div><label style="font-size:12px;color:var(--text-muted)">Run as user</label><input type="text" id="cr-user" style="width:100%" value="root"></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div><label style="font-size:12px;color:var(--text-muted)">Restart policy</label>
                    <select id="cr-restart" style="width:100%">
                        <option value="no">No restart</option>
                        <option value="on-failure">On failure</option>
                        <option value="always">Always</option>
                        <option value="unless-stopped">Unless stopped</option>
                    </select>
                </div>
                <div style="display:flex;align-items:flex-end;">
                    <label style="font-size:13px"><input type="checkbox" id="cr-enabled" style="margin-right:6px">Start on boot</label>
                </div>
            </div>
            ${window.AI_ENABLED ? `
            <div style="display:flex;gap:8px;align-items:center;">
                <button class="btn" id="cr-ai-generate" style="color:#c084fc;border-color:#c084fc;">✦ Generate with AI</button>
                <input type="text" id="cr-ai-prompt" style="flex:1" placeholder="Describe what this service should do…">
            </div>` : ""}
            <div><label style="font-size:12px;color:var(--text-muted)">Preview</label>
                <pre id="cr-preview" style="background:var(--bg);padding:10px;border-radius:var(--radius);font-size:11px;font-family:monospace;max-height:160px;overflow:auto;border:1px solid var(--card-border);"></pre>
            </div>
            <div id="cr-errors" style="font-size:12px;color:var(--danger);"></div>
        </div>
    `;

    function buildUnit() {
        const name = document.getElementById("cr-name")?.value || "my-service";
        const desc = document.getElementById("cr-desc")?.value || name;
        const exec = document.getElementById("cr-exec")?.value || "/usr/bin/example";
        const cwd = document.getElementById("cr-cwd")?.value || "";
        const user = document.getElementById("cr-user")?.value || "root";
        const restart = document.getElementById("cr-restart")?.value || "no";
        const enabled = document.getElementById("cr-enabled")?.checked || false;

        let prev = `[Unit]\nDescription=${desc}\nAfter=network.target\n\n[Service]\nExecStart=${exec}\n`;
        if (cwd) prev += `WorkingDirectory=${cwd}\n`;
        prev += `User=${user}\n`;
        if (restart && restart !== "no") prev += `Restart=${restart}\n`;
        prev += `\n[Install]\nWantedBy=multi-user.target\n`;
        if (enabled) prev += `\n(Will be enabled and started on creation)\n`;
        return prev;
    }

    function updatePreview() {
        const el = document.getElementById("cr-preview");
        if (el) el.textContent = buildUnit();
    }

    const modalEl = openModal("Create Service", html, [
        {
            label: "Create",
            cls: "btn btn-primary",
            onClick: async () => {
                const body = {
                    name: document.getElementById("cr-name")?.value || "",
                    description: document.getElementById("cr-desc")?.value || "",
                    execstart: document.getElementById("cr-exec")?.value || "",
                    workingdir: document.getElementById("cr-cwd")?.value || "",
                    user: document.getElementById("cr-user")?.value || "root",
                    restart: document.getElementById("cr-restart")?.value || "no",
                    enabled: document.getElementById("cr-enabled")?.checked || false,
                };
                if (!body.name || !body.execstart) {
                    toast("Name and Command are required", "error");
                    return false;
                }
                try {
                    const res = await api.post("/services", body);
                    if (res.status === "verify_failed") {
                        const errBox = document.getElementById("cr-errors");
                        if (errBox) {
                            errBox.innerHTML = res.errors.map((e2) =>
                                `<div>${e2.line ? "line " + e2.line + ": " : ""}${esc(e2.message)}</div>`).join("");
                        }
                        toast("Verification failed — fix the issues or adjust the fields", "error", 6000);
                        return false;
                    }
                    toast(res.message, "success");
                    loadServices();
                    closeModal();
                } catch (err) {
                    toast("Failed: " + err.message, "error");
                    return false;
                }
                return true;
            },
        },
        { label: "Cancel", cls: "btn", onClick: () => true },
    ]);

    modalEl.querySelector("#cr-template")?.addEventListener("change", (e) => {
        const t = TEMPLATES[e.target.value];
        if (!t) return;
        const restartSel = modalEl.querySelector("#cr-restart");
        if (t.restart === "on-failure" || t.restart === "always") restartSel.value = t.restart;
        updatePreview();
    });

    modalEl.querySelector("#cr-ai-generate")?.addEventListener("click", async () => {
        const promptEl = modalEl.querySelector("#cr-ai-prompt");
        const btn = modalEl.querySelector("#cr-ai-generate");
        const prompt = promptEl?.value?.trim();
        if (!prompt) { toast("Describe what the service should do first", "warning"); return; }
        btn.disabled = true;
        btn.textContent = "✦ Generating…";
        try {
            const res = await api.post("/ai/generate-unit", { prompt });
            if (res.name) modalEl.querySelector("#cr-name").value = res.name;
            if (res.description) modalEl.querySelector("#cr-desc").value = res.description;
            if (res.execstart) modalEl.querySelector("#cr-exec").value = res.execstart;
            if (res.workingdir) modalEl.querySelector("#cr-cwd").value = res.workingdir;
            if (res.user) modalEl.querySelector("#cr-user").value = res.user;
            if (res.restart) modalEl.querySelector("#cr-restart").value = res.restart;
            updatePreview();
            toast("AI filled the form — review and create", "success");
        } catch (err) {
            toast("AI generate failed: " + err.message, "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "✦ Generate with AI";
        }
    });

    const inputs = ["cr-name", "cr-desc", "cr-exec", "cr-cwd", "cr-user", "cr-restart"];
    inputs.forEach((id) => {
        modalEl.querySelector(`#${id}`)?.addEventListener("input", updatePreview);
    });
    modalEl.querySelector("#cr-enabled")?.addEventListener("change", updatePreview);
    updatePreview();
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div class="filters-bar" style="flex:1;margin-bottom:0;">
                <select id="svc-state-filter">
                    <option value="">All States</option>
                    <option value="active">Active</option>
                    <option value="inactive">Inactive</option>
                    <option value="failed">Failed</option>
                    <option value="masked">Masked</option>
                    <option value="activating">Activating</option>
                    <option value="deactivating">Deactivating</option>
                </select>
                <select id="svc-enabled-filter">
                    <option value="">All Enabled</option>
                    <option value="enabled">Enabled</option>
                    <option value="disabled">Disabled</option>
                    <option value="static">Static</option>
                    <option value="indirect">Indirect</option>
                </select>
                <select id="svc-type-filter">
                    <option value="">All Types</option>
                </select>
                <input type="text" id="svc-search" placeholder="Search by name or description...">
                <button class="btn" id="btn-svc-refresh">Refresh</button>
            </div>
            <button class="btn btn-primary" id="btn-svc-create" style="margin-left:12px;">+ Create Service</button>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th class="svc-sort" data-key="name" style="cursor:pointer">Service</th>
                        <th class="svc-sort" data-key="description" style="cursor:pointer">Description</th>
                        <th class="svc-sort" data-key="active" style="cursor:pointer">State</th>
                        <th class="svc-sort" data-key="enabled" style="cursor:pointer">Enabled</th>
                        <th class="svc-sort" data-key="type" style="cursor:pointer">Type</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="svc-tbody">
                    <tr><td colspan="6"><div class="placeholder">Loading...</div></td></tr>
                </tbody>
            </table>
        </div>
    `;

    ["svc-state-filter", "svc-enabled-filter", "svc-type-filter"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", render);
    });
    document.getElementById("svc-search")?.addEventListener("input", render);
    document.getElementById("btn-svc-refresh")?.addEventListener("click", loadServices);
    document.getElementById("btn-svc-create")?.addEventListener("click", showCreateWizard);

    document.querySelectorAll(".svc-sort").forEach((th) => {
        th.addEventListener("click", () => setSort(th.dataset.key));
    });
}

export function load() { loadServices(); }
export function destroy() { container = null; services = []; }
