import { api } from "../api.js";
import { toast, openModal, esc } from "../ui.js";

let container = null;
let connections = [];
let ipLog = [];
let sortKey = "local_port";
let sortDir = 1;
let tab = "connections";

const STATE_CLASS = {
    LISTEN: "badge-active",
    ESTABLISHED: "badge-active",
    TIME_WAIT: "badge-sleeping",
    CLOSE_WAIT: "badge-warning",
    SYN_SENT: "badge-warning",
    FIN_WAIT1: "badge-sleeping",
    FIN_WAIT2: "badge-sleeping",
    CLOSING: "badge-sleeping",
    LAST_ACK: "badge-sleeping",
};

function stateBadge(state) {
    if (!state) return "";
    const cls = STATE_CLASS[state] || "badge-sleeping";
    return `<span class="badge ${cls}">${state}</span>`;
}

function dirBadge(dir) {
    const cls = dir === "inbound" ? "badge-warning" : "badge-active";
    return `<span class="badge ${cls}" style="font-size:10px">${dir}</span>`;
}

function renderConnections() {
    const tbody = document.getElementById("net-tbody");
    if (!tbody) return;

    const protoF = document.getElementById("net-proto-filter")?.value || "";
    const stateF = document.getElementById("net-state-filter")?.value || "";
    const portF = document.getElementById("net-port-search")?.value?.trim() || "";
    const procF = document.getElementById("net-proc-search")?.value?.toLowerCase() || "";

    let filtered = connections.filter((c) => {
        if (protoF && c.protocol !== protoF) return false;
        if (stateF && c.state !== stateF) return false;
        if (portF) {
            const port = parseInt(portF);
            if (!isNaN(port) && c.local_port !== port && c.remote_port !== port) return false;
        }
        if (procF) {
            const txt = `${c.process} ${c.pid}`.toLowerCase();
            if (!txt.includes(procF)) return false;
        }
        return true;
    });

    filtered.sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        if (!isNaN(av) && !isNaN(bv)) return (Number(av) - Number(bv)) * sortDir;
        return String(av).localeCompare(String(bv)) * sortDir;
    });

    tbody.innerHTML = filtered.map((c) => {
        const local = `${c.local_addr || "*"}:${c.local_port || ""}`;
        const remote = `${c.remote_hostname || c.remote_addr || "*"}:${c.remote_port || ""}`;
        return `
        <tr class="row" data-idx="${connections.indexOf(c)}">
            <td style="font-size:11px">${c.protocol}</td>
            <td style="font-size:11px;font-family:monospace">${esc(local)}</td>
            <td style="font-size:11px;font-family:monospace;max-width:250px;overflow:hidden;text-overflow:ellipsis" title="${esc(c.remote_addr)}:${c.remote_port}">${esc(remote)}</td>
            <td>${stateBadge(c.state)}</td>
            <td style="font-size:11px">${c.process ? `${esc(c.process)} (${c.pid})` : c.pid || ""}${c.service ? ` <a href="#" class="svc-link" data-svc="${esc(c.service)}"><span class="badge badge-sleeping" style="cursor:pointer" title="Owned by service ${esc(c.service)}">${esc(c.service)}</span></a>` : ""}</td>
            <td>${dirBadge(c.direction)}</td>
            <td><button class="btn-icon btn-explain btn-explain-item" title="Ask AI about this">&#10023;</button></td>
        </tr>`;
    }).join("");

    tbody.querySelectorAll("tr.row").forEach((row) => {
        row.addEventListener("click", () => showConnDetail(parseInt(row.dataset.idx)));
    });

    // Service badges → jump to Services section filtered
    tbody.querySelectorAll(".svc-link").forEach((a) => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.navigateSection?.("services", a.dataset.svc);
        });
    });

    // Explain button
    tbody.querySelectorAll(".btn-explain-item").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!window.AI_ENABLED) { toast("Enable AI in Settings first", "warning"); return; }
            const row = btn.closest("tr.row");
            const idx = parseInt(row?.dataset?.idx);
            if (isNaN(idx)) return;
            const c = connections[idx];
            const ctx = c ? `${c.protocol} ${c.local_addr}:${c.local_port} → ${c.remote_hostname || c.remote_addr}:${c.remote_port}\nState: ${c.state}\nProcess: ${c.process} (PID ${c.pid})\nDirection: ${c.direction}` : `Connection`;
            window.openAIPanel(ctx, "network");
        });
    });
}

let _resolveIdx = null;

function showConnDetail(idx) {
    const c = connections[idx];
    if (!c) return;
    _resolveIdx = idx;

    const hasRemote = c.remote_addr && c.remote_addr !== "0.0.0.0" && c.remote_addr !== "::";

    const html = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
            <div><strong>Protocol:</strong> ${c.protocol}</div>
            <div><strong>State:</strong> ${stateBadge(c.state)}</div>
            <div><strong>Local:</strong> <code>${esc(c.local_addr)}:${c.local_port}</code></div>
            <div><strong>Remote:</strong> <code>${esc(c.remote_addr)}:${c.remote_port}</code></div>
            <div><strong>Process:</strong> ${esc(c.process || "unknown")} (PID ${c.pid})</div>
            <div><strong>Direction:</strong> ${dirBadge(c.direction)}</div>
            ${c.local_hostname ? `<div><strong>Local Host:</strong> ${esc(c.local_hostname)}</div>` : ""}
            ${c.remote_hostname ? `<div><strong>Remote Host:</strong> ${esc(c.remote_hostname)}</div>` : ""}
        </div>
        ${hasRemote ? `
        <div style="margin-bottom:8px;">
            <button class="btn btn-sm" id="btn-resolve-conn">Resolve ${esc(c.remote_addr)}</button>
            <span id="resolve-result-${idx}" style="font-size:11px;color:var(--text-muted);margin-left:8px;"></span>
        </div>` : ""}
    `;

    openModal(`Connection Detail`, html, [
        { label: "Close", cls: "btn", onClick: () => true },
    ]);
    if (hasRemote) {
        document.getElementById("btn-resolve-conn")?.addEventListener("click", () => doResolve(idx, c.remote_addr));
    }
}

async function doResolve(idx, ip) {
    const el = document.getElementById(`resolve-result-${idx}`);
    if (!el) return;
    el.innerHTML = '<span class="spinner"></span> Resolving...';
    try {
        const res = await api.post("/network/resolve", { ip });
        el.innerHTML = "";
        let found = false;
        for (const [k, v] of Object.entries(res)) {
            if (v && k !== "ip" && k !== "private") {
                const row = document.createElement("div");
                const keyEl = document.createElement("strong");
                keyEl.textContent = k + ": ";
                row.appendChild(keyEl);
                row.appendChild(document.createTextNode(v));
                el.appendChild(row);
                found = true;
            }
        }
        if (!found) el.textContent = "No data found for this IP";
    } catch (err) {
        el.textContent = "Resolution failed: " + err.message;
    }
}

window._doResolve = doResolve; // keep for console debugging

function renderIPLog() {
    const tbody = document.getElementById("iplog-tbody");
    if (!tbody) return;

    tbody.innerHTML = ipLog.map((e) => `
        <tr class="row">
            <td style="font-family:monospace;font-size:11px">${esc(e.ip)}</td>
            <td style="font-size:11px">${esc(e.hostname || "")}</td>
            <td style="font-size:11px">${esc(e.country || "")}</td>
            <td style="font-size:11px">${esc(e.city || "")}</td>
            <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(e.org || e.isp || "")}</td>
            <td style="font-size:11px;color:var(--text-dim)">${new Date(e.first_seen * 1000).toLocaleString()}</td>
            <td style="font-size:11px;color:var(--text-dim)">${new Date(e.last_seen * 1000).toLocaleString()}</td>
            <td style="font-size:11px">${e.times_seen}</td>
        </tr>
    `).join("");
}

function setSort(key) {
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = 1; }
    renderConnections();
    document.querySelectorAll(".net-sort").forEach((el) => el.textContent = "");
    const el = document.querySelector(`.net-sort[data-key="${key}"]`);
    if (el) el.textContent = sortDir > 0 ? " \\25B2" : " \\25BC";
}

function switchTab(name) {
    tab = name;
    document.querySelectorAll(".net-tab").forEach((el) => {
        el.classList.toggle("active", el.dataset.tab === name);
    });
    document.getElementById("net-connections-panel").classList.toggle("hidden", name !== "connections");
    document.getElementById("net-iplog-panel").classList.toggle("hidden", name !== "iplog");
}

async function loadData() {
    const ctbody = document.getElementById("net-tbody");
    if (ctbody) ctbody.innerHTML = `<tr><td colspan="6"><div class="placeholder"><span class="spinner"></span> Loading connections...</div></td></tr>`;

    try {
        const [connData, logData] = await Promise.all([
            api.get("/network/connections"),
            api.get("/network/iplog"),
        ]);
        connections = connData.connections || [];
        ipLog = logData.entries || [];
        renderConnections();
        renderIPLog();
    } catch (err) {
        toast("Error loading network data: " + err.message, "error");
    }
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div style="display:flex;gap:0;margin-bottom:12px;">
            <button class="btn net-tab active" data-tab="connections" style="border-radius:6px 0 0 6px;">Connections</button>
            <button class="btn net-tab" data-tab="iplog" style="border-radius:0 6px 6px 0;">IP Log</button>
            <div style="flex:1;"></div>
            <button class="btn" id="btn-net-refresh">Refresh</button>
        </div>

        <div id="net-connections-panel">
            <div class="filters-bar">
                <select id="net-proto-filter">
                    <option value="">All Protocols</option>
                    <option value="TCP">TCP</option>
                    <option value="UDP">UDP</option>
                </select>
                <select id="net-state-filter">
                    <option value="">All States</option>
                    <option value="LISTEN">LISTEN</option>
                    <option value="ESTABLISHED">ESTABLISHED</option>
                    <option value="TIME_WAIT">TIME_WAIT</option>
                    <option value="CLOSE_WAIT">CLOSE_WAIT</option>
                    <option value="SYN_SENT">SYN_SENT</option>
                </select>
                <input type="text" id="net-port-search" placeholder="Filter by port...">
                <input type="text" id="net-proc-search" placeholder="Filter by process...">
            </div>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th class="net-sort" data-key="protocol" style="cursor:pointer">Proto</th>
                            <th>Local Address</th>
                            <th>Remote Address</th>
                            <th class="net-sort" data-key="state" style="cursor:pointer">State</th>
                            <th>Process</th>
                            <th class="net-sort" data-key="direction" style="cursor:pointer">Dir</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody id="net-tbody">
                        <tr><td colspan="7"><div class="placeholder">Loading...</div></td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div id="net-iplog-panel" class="hidden">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>IP</th>
                            <th>Hostname</th>
                            <th>Country</th>
                            <th>City</th>
                            <th>Org / ISP</th>
                            <th>First Seen</th>
                            <th>Last Seen</th>
                            <th>Times</th>
                        </tr>
                    </thead>
                    <tbody id="iplog-tbody">
                        <tr><td colspan="8"><div class="placeholder">Loading...</div></td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    `;

    // Tab switching
    document.querySelectorAll(".net-tab").forEach((btn) => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Filters
    ["net-proto-filter", "net-state-filter"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", renderConnections);
    });
    document.getElementById("net-port-search")?.addEventListener("input", renderConnections);
    document.getElementById("net-proc-search")?.addEventListener("input", renderConnections);

    // Refresh
    document.getElementById("btn-net-refresh")?.addEventListener("click", loadData);

    // Sort
    document.querySelectorAll(".net-sort").forEach((th) => {
        th.addEventListener("click", () => setSort(th.dataset.key));
    });
}

export function load() { loadData(); }
export function destroy() { container = null; connections = []; ipLog = []; }
