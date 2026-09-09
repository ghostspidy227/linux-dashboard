import { api } from "../api.js";
import { toast, confirm, openModal, esc } from "../ui.js";

let container = null;
let processes = [];
let sortKey = "cpu";
let sortDir = -1;

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function formatTime(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleString();
}

function statusBadge(status) {
    const map = {
        running: "badge-active",
        sleeping: "badge-sleeping",
        zombie: "badge-error",
        stopped: "badge-warning",
        disk_sleep: "badge-sleeping",
        dead: "badge-error",
    };
    const cls = map[status] || "badge-sleeping";
    return `<span class="badge ${cls}">${status}</span>`;
}

function render() {
    const filtersEl = document.getElementById("proc-filters")?.value || "";
    const searchEl = document.getElementById("proc-search")?.value?.toLowerCase() || "";
    const statusEl = document.getElementById("proc-status-filter")?.value || "";
    const userEl = document.getElementById("proc-user-filter")?.value?.toLowerCase() || "";

    let filtered = processes.filter((p) => {
        if (statusEl && p.status !== statusEl) return false;
        if (userEl && (p.user || "").toLowerCase() !== userEl) return false;
        if (searchEl) {
            const s = `${p.name} ${p.command} ${p.pid}`.toLowerCase();
            if (!s.includes(searchEl)) return false;
        }
        return true;
    });

    filtered.sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
        return String(av).localeCompare(String(bv)) * sortDir;
    });

    const tbody = document.getElementById("proc-tbody");
    if (!tbody) return;

    const uniqueUsers = [...new Set(processes.map((p) => p.user).filter(Boolean))].sort();
    const userSel = document.getElementById("proc-user-filter");
    if (userSel && userSel.options.length <= 2) {
        uniqueUsers.forEach((u) => {
            const opt = document.createElement("option");
            opt.value = u;
            opt.textContent = u;
            userSel.appendChild(opt);
        });
    }

    tbody.innerHTML = filtered.map((p) => `
        <tr class="row" data-pid="${p.pid}">
            <td class="nw">${p.pid}</td>
            <td>${esc(p.name)}${p.service ? ` <a href="#" class="svc-link" data-svc="${esc(p.service)}" title="Owned by service ${esc(p.service)}"><span class="badge badge-sleeping" style="cursor:pointer">${esc(p.service)}</span></a>` : ""}</td>
            <td class="nw">${esc(p.user)}</td>
            <td class="nw" style="color:var(--accent)">${p.cpu?.toFixed(1) ?? "0"}%</td>
            <td class="nw">${formatBytes(p.rss)}</td>
            <td>${statusBadge(p.status)}</td>
            <td style="font-size:11px;color:var(--text-dim)">${formatTime(p.started)}</td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--text-dim)" title="${esc(p.command)}">${esc(p.command)}</td>
            <td class="actions">
                <button class="btn-icon btn-explain btn-explain-item" data-section="processes" title="Ask AI about this">&#10023;</button>
                <button class="btn-icon btn-danger btn-proc-kill" data-pid="${p.pid}" title="Kill (SIGKILL)">
                    &#10007;
                </button>
                <button class="btn-icon btn-stop" data-pid="${p.pid}" title="Stop (SIGTERM)" style="color:var(--warning)">
                    &#9632;
                </button>
            </td>
        </tr>
    `).join("");

    // Row click → detail
    tbody.querySelectorAll("tr.row").forEach((row) => {
        row.addEventListener("click", () => showDetail(parseInt(row.dataset.pid)));
    });

    // Service badges → jump to Services section filtered
    tbody.querySelectorAll(".svc-link").forEach((a) => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.navigateSection?.("services", a.dataset.svc);
        });
    });

    // Kill button
    tbody.querySelectorAll(".btn-proc-kill").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const pid = parseInt(btn.dataset.pid);
            doKill(pid);
        });
    });

    // Stop button
    tbody.querySelectorAll(".btn-stop").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const pid = parseInt(btn.dataset.pid);
            doStop(pid);
        });
    });

    // Explain button
    tbody.querySelectorAll(".btn-explain-item").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!window.AI_ENABLED) { toast("Enable AI in Settings first", "warning"); return; }
            const row = btn.closest("tr.row");
            const pid = parseInt(row?.dataset?.pid);
            const p = processes.find(x => x.pid === pid);
            const ctx = p ? `Process: ${p.name} (PID ${p.pid})\nUser: ${p.user}\nCPU: ${p.cpu}%\nRAM: ${formatBytes(p.rss)}\nStatus: ${p.status}\nStarted: ${formatTime(p.started)}\nCommand: ${p.command}` : `Process PID ${pid}`;
            window.openAIPanel(ctx, "processes");
        });
    });
}

function setSort(key) {
    if (sortKey === key) {
        sortDir *= -1;
    } else {
        sortKey = key;
        sortDir = key === "pid" ? 1 : -1;
    }
    render();
    document.querySelectorAll(".proc-sort").forEach((el) => el.textContent = "");
    const el = document.querySelector(`.proc-sort[data-key="${key}"]`);
    if (el) el.textContent = sortDir > 0 ? " \\25B2" : " \\25BC";
}

async function loadProcesses() {
    const tbody = document.getElementById("proc-tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="9"><div class="placeholder"><span class="spinner"></span> Loading processes...</div></td></tr>`;
    try {
        const data = await api.get("/processes");
        processes = data.processes || [];
        render();
    } catch (err) {
        toast("Error loading processes: " + err.message, "error");
        if (tbody) tbody.innerHTML = `<tr><td colspan="9"><div class="placeholder">Error: ${err.message}</div></td></tr>`;
    }
}

async function showDetail(pid) {
    try {
        const data = await api.get(`/processes/${pid}`);
        const connRows = (data.connections || []).map((c) =>
            `<tr><td>${esc(c.fd)}</td><td>${esc(c.type)}</td><td>${esc(c.laddr)}</td><td>${esc(c.raddr)}</td><td>${esc(c.status)}</td></tr>`
        ).join("");

        const envKeys = Object.keys(data.environ || {}).sort();
        const envRows = envKeys.map((k) =>
            `<tr><td style="color:var(--accent)">${esc(k)}</td><td style="word-break:break-all;font-size:11px">${esc(data.environ[k])}</td></tr>`
        ).join("");

        const html = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
                <div><strong>PID:</strong> ${data.pid}</div>
                <div><strong>Name:</strong> ${esc(data.name)}</div>
                <div><strong>User:</strong> ${esc(data.user)}</div>
                <div><strong>Status:</strong> ${statusBadge(data.status)}</div>
                <div><strong>CPU:</strong> ${data.cpu?.toFixed(1)}%</div>
                <div><strong>RSS:</strong> ${formatBytes(data.rss)}</div>
                <div><strong>VMS:</strong> ${formatBytes(data.vms)}</div>
                <div><strong>Threads:</strong> ${data.num_threads}</div>
                <div><strong>Nice:</strong> ${data.nice}</div>
                <div><strong>Started:</strong> ${formatTime(data.started)}</div>
                <div><strong>Parent:</strong> ${data.ppid} (${esc(data.ppid_name || "?")})</div>
                <div><strong>Open files:</strong> ${data.open_files}</div>
                <div><strong>CWD:</strong> <code style="font-size:11px">${esc(data.cwd || "")}</code></div>
                <div><strong>Exe:</strong> <code style="font-size:11px">${esc(data.exe || "")}</code></div>
                ${data.service ? `<div><strong>Service:</strong> <a href="#" id="proc-detail-svc" style="color:var(--accent);cursor:pointer">${esc(data.service)}</a></div>` : ""}
            </div>
            <div style="margin-bottom:12px;">
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">Command Line</h4>
                <div class="card" style="padding:10px;font-size:12px;word-break:break-all;font-family:monospace">${esc(data.command)}</div>
            </div>
            ${connRows ? `
            <div style="margin-bottom:12px;">
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">Network Connections</h4>
                <table><thead><tr><th>FD</th><th>Type</th><th>Local</th><th>Remote</th><th>State</th></tr></thead><tbody>${connRows}</tbody></table>
            </div>` : ""}
            ${envRows ? `
            <details style="margin-bottom:12px;">
                <summary style="color:var(--text-muted);font-size:12px;cursor:pointer;">Environment Variables (${envKeys.length})</summary>
                <table style="margin-top:8px"><tbody>${envRows}</tbody></table>
            </details>` : ""}
            ${data.io_counters && data.io_counters.read_bytes != null ? `
            <div>
                <h4 style="color:var(--text-muted);font-size:12px;margin-bottom:4px;">I/O Counters</h4>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:12px;">
                    <div>Read: ${formatBytes(data.io_counters.read_bytes)}</div>
                    <div>Write: ${formatBytes(data.io_counters.write_bytes)}</div>
                    <div>Read ops: ${data.io_counters.read_count}</div>
                    <div>Write ops: ${data.io_counters.write_count}</div>
                </div>
            </div>` : ""}
        `;

        openModal(`Process ${data.pid} — ${esc(data.name)}`, html, [
            {
                label: "Close",
                cls: "btn",
                onClick: () => true,
            },
        ]);
        document.getElementById("proc-detail-svc")?.addEventListener("click", (e) => {
            e.preventDefault();
            window.navigateSection?.("services", data.service);
        });
    } catch (err) {
        toast("Error loading process detail: " + err.message, "error");
    }
}

async function doKill(pid) {
    const ok = await confirm(`Kill process ${pid}? This sends SIGKILL and will force-terminate the process immediately.`);
    if (!ok) return;
    try {
        const res = await api.post(`/processes/${pid}/kill`);
        toast(res.message || "Process killed", "success");
        loadProcesses();
    } catch (err) {
        toast("Failed to kill process: " + err.message, "error");
    }
}

async function doStop(pid) {
    const ok = await confirm(`Stop process ${pid}? This sends SIGTERM and allows the process to shut down gracefully.`);
    if (!ok) return;
    try {
        const res = await api.post(`/processes/${pid}/stop`);
        toast(res.message || "Process stopped", "success");
        loadProcesses();
    } catch (err) {
        toast("Failed to stop process: " + err.message, "error");
    }
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div class="filters-bar">
            <select id="proc-status-filter">
                <option value="">All Statuses</option>
                <option value="running">Running</option>
                <option value="sleeping">Sleeping</option>
                <option value="stopped">Stopped</option>
                <option value="zombie">Zombie</option>
                <option value="disk_sleep">Disk Sleep</option>
                <option value="dead">Dead</option>
            </select>
            <select id="proc-user-filter">
                <option value="">All Users</option>
            </select>
            <input type="text" id="proc-search" placeholder="Search by name or command...">
            <button class="btn" id="btn-proc-refresh">Refresh</button>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th class="proc-sort" data-key="pid" style="cursor:pointer">PID</th>
                        <th class="proc-sort" data-key="name" style="cursor:pointer">Name</th>
                        <th class="proc-sort" data-key="user" style="cursor:pointer">User</th>
                        <th class="proc-sort" data-key="cpu" style="cursor:pointer">CPU</th>
                        <th class="proc-sort" data-key="rss" style="cursor:pointer">RAM</th>
                        <th class="proc-sort" data-key="status" style="cursor:pointer">Status</th>
                        <th class="proc-sort" data-key="started" style="cursor:pointer">Started</th>
                        <th>Command</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="proc-tbody">
                    <tr><td colspan="9"><div class="placeholder">Loading...</div></td></tr>
                </tbody>
            </table>
        </div>
    `;

    // Filter events
    ["proc-status-filter", "proc-user-filter"].forEach((id) => {
        document.getElementById(id)?.addEventListener("change", render);
    });
    document.getElementById("proc-search")?.addEventListener("input", render);

    // Refresh button
    document.getElementById("btn-proc-refresh")?.addEventListener("click", loadProcesses);

    // Sortable column headers
    document.querySelectorAll(".proc-sort").forEach((th) => {
        th.addEventListener("click", () => setSort(th.dataset.key));
    });
}

export function load() {
    loadProcesses();
}

export function destroy() {
    container = null;
    processes = [];
}
