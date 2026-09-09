import { api } from "../api.js";
import { toast, confirm, openModal, closeModal, esc } from "../ui.js";

let container = null;
let managers = [];
let currentManager = "";
let packages = [];
let searchResults = [];
let updates = [];
let sortKey = "name";
let sortDir = 1;
let view = "installed"; // installed | search | updates

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

// ── Job watching ──

function watchJob(jobId, label) {
    const timer = setInterval(async () => {
        try {
            const job = await api.get(`/jobs/${jobId}`);
            if (job.status === "running") return;
            clearInterval(timer);
            if (job.status === "done") {
                toast(`${label} completed`, "success");
            } else {
                const tail = (job.output || []).slice(-3).join("\n");
                toast(`${label} failed: ${tail || "unknown error"}`, "error", 8000);
            }
            if (view === "updates") loadUpdates();
            else if (view === "installed") loadPackages();
        } catch (err) {
            clearInterval(timer);
        }
    }, 2000);
}

async function doAction(action, name) {
    const verb = action === "install" ? "Install" : action === "remove" ? "Remove" : "Upgrade";
    const ok = await confirm(`${verb} ${name} via ${currentManager}?`);
    if (!ok) return;
    try {
        let res;
        if (action === "install") res = await api.post(`/packages/${currentManager}/install`, { name });
        else if (action === "remove") res = await api.delete(`/packages/${currentManager}/${encodeURIComponent(name)}`);
        else res = await api.post(`/packages/${currentManager}/upgrade`, { name });
        toast(`${verb} started…`, "success");
        watchJob(res.job_id, `${verb} ${name}`);
    } catch (err) {
        toast("Failed: " + err.message, "error");
    }
}

async function upgradeAll() {
    if (!(await confirm(`Upgrade ALL packages via ${currentManager}?`))) return;
    try {
        const res = await api.post(`/packages/${currentManager}/upgrade`, {});
        toast("Upgrade started…", "success");
        watchJob(res.job_id, "Upgrade all");
    } catch (err) {
        toast("Failed: " + err.message, "error");
    }
}

// ── Installed view ──

function renderInstalled() {
    const tbody = document.getElementById("pkg-tbody");
    if (!tbody) return;

    const search = document.getElementById("pkg-search")?.value?.toLowerCase() || "";

    let filtered = packages.filter((p) => {
        if (search) {
            const txt = `${p.name} ${p.description}`.toLowerCase();
            if (!txt.includes(search)) return false;
        }
        return true;
    });

    filtered.sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        if (!isNaN(av) && !isNaN(bv)) return (Number(av) - Number(bv)) * sortDir;
        return String(av).localeCompare(String(bv)) * sortDir;
    });

    tbody.innerHTML = filtered.map((p) => `
        <tr class="row" data-name="${esc(p.name)}">
            <td style="color:var(--accent)">${esc(p.name)}</td>
            <td style="font-size:11px">${esc(p.version || "")}</td>
            <td style="font-size:11px;color:var(--text-dim)">${formatBytes(p.size)}</td>
            <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--text-dim)" title="${esc(p.description || "")}">${esc(p.description || "")}</td>
            <td class="actions">
                <button class="btn-icon btn-explain btn-pkg-ask" data-name="${esc(p.name)}" title="Ask AI about this">&#10023;</button>
                <button class="btn-icon btn-danger btn-pkg-remove" data-name="${esc(p.name)}" title="Remove package">&#10007;</button>
            </td>
        </tr>
    `).join("");

    tbody.querySelectorAll("tr.row").forEach((row) => {
        row.addEventListener("click", (e) => {
            if (e.target.closest(".btn-pkg-remove") || e.target.closest(".btn-pkg-ask")) return;
            showDetail(row.dataset.name);
        });
    });
    tbody.querySelectorAll(".btn-pkg-remove").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            doAction("remove", btn.dataset.name);
        });
    });
    tbody.querySelectorAll(".btn-pkg-ask").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (!window.AI_ENABLED) { toast("Enable AI in Settings first", "warning"); return; }
            const p = packages.find((x) => x.name === btn.dataset.name);
            window.openAIPanel(`Package: ${p?.name || btn.dataset.name} ${p?.version || ""}\nDescription: ${p?.description || "unknown"}`, "packages");
        });
    });
}

async function showDetail(name) {
    try {
        const data = await api.get(`/packages/${currentManager}/${encodeURIComponent(name)}`);

        const depList = (data.dependencies || []).map((d) =>
            `<a href="#" class="pkg-dep-link" data-dep="${esc(d)}">${esc(d)}</a>`
        ).join(", ") || "none";

        const revdepList = (data.reverse_deps || []).map((d) =>
            `<a href="#" class="pkg-dep-link" data-dep="${esc(d)}">${esc(d)}</a>`
        ).join(", ") || "none";

        const filesList = (data.files || []).slice(0, 200).map((f) =>
            `<div style="font-size:11px;font-family:monospace;color:var(--text-dim)">${esc(f)}</div>`
        ).join("");

        const html = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;font-size:13px;">
                <div><strong>Name:</strong> ${esc(data.name)}</div>
                <div><strong>Version:</strong> ${esc(data.version || "")}</div>
                <div><strong>Size:</strong> ${formatBytes(data.size)}</div>
                ${data.section ? `<div><strong>Section:</strong> ${esc(data.section)}</div>` : ""}
                ${data.maintainer ? `<div><strong>Maintainer:</strong> ${esc(data.maintainer)}</div>` : ""}
                ${data.publisher ? `<div><strong>Publisher:</strong> ${esc(data.publisher)}</div>` : ""}
                ${data.install_date ? `<div><strong>Installed:</strong> ${esc(data.install_date)}</div>` : ""}
                ${data.channel ? `<div><strong>Channel:</strong> ${esc(data.channel)}</div>` : ""}
            </div>
            ${data.description ? `<div style="margin-bottom:12px;"><strong>Description:</strong><p style="font-size:13px;margin-top:4px;color:var(--text-muted)">${esc(data.description)}</p></div>` : ""}
            ${data.homepage ? `<div style="margin-bottom:12px;"><strong>Homepage:</strong> <a href="${esc(data.homepage)}" target="_blank" rel="noopener">${esc(data.homepage)}</a></div>` : ""}
            <div style="margin-bottom:12px;"><strong>Dependencies:</strong> <span>${depList}</span></div>
            <div style="margin-bottom:12px;"><strong>Reverse Dependencies:</strong> <span>${revdepList}</span></div>
            ${filesList ? `
            <details>
                <summary style="cursor:pointer;font-size:13px;margin-bottom:8px;"><strong>Installed Files</strong> (${data.files.length} files, showing first 200)</summary>
                <div class="card" style="padding:8px;max-height:300px;overflow:auto;">${filesList}</div>
            </details>` : ""}
        `;

        const modal = openModal(`Package: ${esc(name)}`, html, [
            { label: "Close", cls: "btn", onClick: () => true },
        ]);

        modal.querySelectorAll(".pkg-dep-link").forEach((link) => {
            link.addEventListener("click", (e) => {
                e.preventDefault();
                const dep = link.dataset.dep;
                closeModal();
                showDetail(dep);
            });
        });
    } catch (err) {
        toast("Error: " + err.message, "error");
    }
}

// ── Search view ──

function renderSearch() {
    const tbody = document.getElementById("pkg-tbody");
    if (!tbody) return;

    if (!searchResults.length) {
        tbody.innerHTML = `<tr><td colspan="4"><div class="placeholder">No results.</div></td></tr>`;
        return;
    }
    tbody.innerHTML = searchResults.map((p) => `
        <tr>
            <td style="color:var(--accent)">${esc(p.name)}</td>
            <td style="font-size:11px">${esc(p.version || "")}</td>
            <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--text-dim)">${esc(p.description || "")}</td>
            <td class="actions"><button class="btn btn-sm btn-pkg-install" data-name="${esc(p.name)}">Install</button></td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".btn-pkg-install").forEach((btn) => {
        btn.addEventListener("click", () => doAction("install", btn.dataset.name));
    });
}

async function doSearch() {
    const q = document.getElementById("pkg-search")?.value?.trim() || "";
    if (!q) return;
    const tbody = document.getElementById("pkg-tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="4"><div class="placeholder"><span class="spinner"></span> Searching…</div></td></tr>`;
    try {
        const data = await api.get(`/packages/${currentManager}/search?q=${encodeURIComponent(q)}`);
        searchResults = data.results || [];
    } catch (err) {
        searchResults = [];
        toast("Search failed: " + err.message, "error");
    }
    renderSearch();
}

// ── Updates view ──

function renderUpdates() {
    const tbody = document.getElementById("pkg-tbody");
    if (!tbody) return;

    if (!updates.length) {
        tbody.innerHTML = `<tr><td colspan="4"><div class="placeholder">Everything is up to date.</div></td></tr>`;
        return;
    }
    tbody.innerHTML = updates.map((u) => `
        <tr>
            <td style="color:var(--accent)">${esc(u.name)}</td>
            <td style="font-size:11px">${esc(u.version || "")}</td>
            <td></td>
            <td class="actions"><button class="btn btn-sm btn-pkg-upgrade" data-name="${esc(u.name)}">Upgrade</button></td>
        </tr>
    `).join("");

    tbody.querySelectorAll(".btn-pkg-upgrade").forEach((btn) => {
        btn.addEventListener("click", () => doAction("upgrade", btn.dataset.name));
    });
}

async function loadUpdates() {
    const tbody = document.getElementById("pkg-tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="4"><div class="placeholder"><span class="spinner"></span> Checking updates…</div></td></tr>`;
    try {
        const data = await api.get(`/packages/${currentManager}/updates`);
        updates = data.updates || [];
    } catch (err) {
        updates = [];
        toast("Update check failed: " + err.message, "error");
    }
    renderUpdates();
}

// ── Shared chrome ──

function renderBody() {
    const tbody = document.getElementById("pkg-tbody");
    const searchBox = document.getElementById("pkg-search");
    const upgradeAllBtn = document.getElementById("btn-pkg-upgrade-all");

    if (view === "search") {
        if (searchBox) searchBox.placeholder = "Search " + currentManager + " packages (press Enter)…";
        if (upgradeAllBtn) upgradeAllBtn.classList.add("hidden");
        if (tbody) tbody.innerHTML = `<tr><td colspan="4"><div class="placeholder">Type to search in ${esc(currentManager)} repositories.</div></td></tr>`;
    } else if (view === "updates") {
        if (searchBox) searchBox.placeholder = "Search (installed view only)";
        if (upgradeAllBtn) upgradeAllBtn.classList.remove("hidden");
        loadUpdates();
    } else {
        if (searchBox) searchBox.placeholder = "Search installed packages…";
        if (upgradeAllBtn) upgradeAllBtn.classList.add("hidden");
        loadPackages();
    }
}

function renderTabs() {
    const tabsEl = document.getElementById("pkg-tabs");
    if (!tabsEl) return;
    tabsEl.innerHTML = managers.map((m) =>
        `<button class="btn pkg-tab${m === currentManager ? " btn-primary" : ""}" data-mgr="${esc(m)}">${esc(m)}</button>`
    ).join("");

    tabsEl.querySelectorAll(".pkg-tab").forEach((btn) => {
        btn.addEventListener("click", () => switchManager(btn.dataset.mgr));
    });
}

function renderViewTabs() {
    document.querySelectorAll(".pkg-view-tab").forEach((btn) => {
        btn.classList.toggle("btn-primary", btn.dataset.view === view);
    });
}

async function switchManager(mgr) {
    currentManager = mgr;
    renderTabs();
    packages = [];
    searchResults = [];
    updates = [];
    renderBody();
}

async function loadPackages() {
    const tbody = document.getElementById("pkg-tbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="5"><div class="placeholder"><span class="spinner"></span> Loading ${esc(currentManager)} packages…</div></td></tr>`;
    try {
        const data = await api.get(`/packages/${currentManager}`);
        packages = data.packages || [];
        renderInstalled();
    } catch (err) {
        toast("Error: " + err.message, "error");
        if (tbody) tbody.innerHTML = `<tr><td colspan="5"><div class="placeholder">Error: ${esc(err.message)}</div></td></tr>`;
    }
}

async function initManagers() {
    try {
        const data = await api.get("/packages");
        managers = data.managers || [];
        if (managers.length > 0) {
            currentManager = managers[0];
            renderTabs();
            renderBody();
        }
    } catch (err) {
        toast("Error detecting managers: " + err.message, "error");
    }
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
            <div style="display:flex;gap:4px;">
                <button class="btn pkg-view-tab btn-primary" data-view="installed">Installed</button>
                <button class="btn pkg-view-tab" data-view="search">Search</button>
                <button class="btn pkg-view-tab" data-view="updates">Updates</button>
            </div>
            <div id="pkg-tabs" style="display:flex;gap:4px;">Loading…</div>
            <div style="flex:1;"></div>
            <input type="text" id="pkg-search" placeholder="Search installed packages…" style="width:250px;">
            <button class="btn hidden" id="btn-pkg-upgrade-all">Upgrade All</button>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th class="pkg-sort" data-key="name" style="cursor:pointer">Package</th>
                        <th class="pkg-sort" data-key="version" style="cursor:pointer">Version</th>
                        <th class="pkg-sort" data-key="size" style="cursor:pointer">Size</th>
                        <th>Description</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody id="pkg-tbody">
                    <tr><td colspan="5"><div class="placeholder">Select a package manager above.</div></td></tr>
                </tbody>
            </table>
        </div>
    `;

    container.querySelectorAll(".pkg-view-tab").forEach((btn) => {
        btn.addEventListener("click", () => {
            view = btn.dataset.view;
            renderViewTabs();
            renderBody();
        });
    });
    document.getElementById("pkg-search")?.addEventListener("input", () => {
        if (view === "installed") renderInstalled();
    });
    document.getElementById("pkg-search")?.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && view === "search") doSearch();
    });
    document.getElementById("btn-pkg-upgrade-all")?.addEventListener("click", upgradeAll);
    container.querySelectorAll(".pkg-sort").forEach((th) => {
        th.addEventListener("click", () => {
            if (sortKey === th.dataset.key) sortDir *= -1;
            else { sortKey = th.dataset.key; sortDir = 1; }
            renderInstalled();
        });
    });
}

export function load() { initManagers(); }
export function destroy() { container = null; packages = []; managers = []; searchResults = []; updates = []; }
