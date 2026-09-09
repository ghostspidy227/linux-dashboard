import { api } from "../api.js";
import { toast, confirm, openModal, closeModal, esc } from "../ui.js";

let container = null;
let fwData = null;

const ACTION_CLASS = {
    ALLOW: "badge-active",
    DENY: "badge-error",
    REJECT: "badge-warning",
    ACCEPT: "badge-active",
    DROP: "badge-error",
};

function actionBadge(a) {
    const cls = ACTION_CLASS[a] || "badge-sleeping";
    return `<span class="badge ${cls}">${a.toLowerCase()}</span>`;
}

function renderUFW() {
    const body = document.getElementById("fw-body");
    if (!body || !fwData) return;

    const enabled = fwData.enabled;
    const toggleColor = enabled ? "var(--danger)" : "var(--success)";
    const toggleLabel = enabled ? "DISABLE FIREWALL" : "ENABLE FIREWALL";

    let rulesHtml = "";
    if (fwData.rules && fwData.rules.length > 0) {
        rulesHtml = fwData.rules.map((r) => `
            <tr>
                <td>${r.num}</td>
                <td style="font-size:11px;font-family:monospace">${esc(r.to || "")}</td>
                <td>${actionBadge(r.action)}</td>
                <td style="font-size:11px">${esc(r.direction || "")}</td>
                <td style="font-size:11px;font-family:monospace">${esc(r.from || "any")}</td>
                <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;color:var(--text-dim)">${esc(r.comment || "")}</td>
                <td class="actions">
                    <button class="btn-icon btn-danger btn-fw-del" data-num="${r.num}" title="Delete">&#10007;</button>
                </td>
            </tr>
        `).join("");
    } else {
        rulesHtml = `<tr><td colspan="7"><div class="placeholder">No rules configured.</div></td></tr>`;
    }

    body.innerHTML = `
        <div style="display:flex;gap:16px;align-items:center;margin-bottom:20px;">
            <button class="btn ${enabled ? 'btn-danger' : 'btn-primary'}" id="btn-fw-toggle" style="font-weight:700;padding:10px 24px;">
                ${toggleLabel}
            </button>
            <div style="display:flex;gap:12px;align-items:center;font-size:13px;">
                <span style="color:var(--text-muted)">Incoming:</span>
                <select id="fw-policy-in" ${!enabled ? 'disabled' : ''}>
                    <option value="deny" ${fwData.default_in === "deny" ? "selected" : ""}>Deny</option>
                    <option value="allow" ${fwData.default_in === "allow" ? "selected" : ""}>Allow</option>
                    <option value="reject" ${fwData.default_in === "reject" ? "selected" : ""}>Reject</option>
                </select>
                <span style="color:var(--text-muted)">Outgoing:</span>
                <select id="fw-policy-out" ${!enabled ? 'disabled' : ''}>
                    <option value="deny" ${fwData.default_out === "deny" ? "selected" : ""}>Deny</option>
                    <option value="allow" ${fwData.default_out === "allow" ? "selected" : ""}>Allow</option>
                    <option value="reject" ${fwData.default_out === "reject" ? "selected" : ""}>Reject</option>
                </select>
            </div>
            <div style="flex:1;"></div>
            <button class="btn" id="btn-fw-refresh">Refresh</button>
            <button class="btn btn-primary" id="btn-fw-add">+ Add Rule</button>
            <button class="btn btn-danger" id="btn-fw-reset">Reset</button>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>To</th>
                        <th>Action</th>
                        <th>Dir</th>
                        <th>From</th>
                        <th>Comment</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody id="fw-rules-tbody">${rulesHtml}</tbody>
            </table>
        </div>
    `;

    // Toggle
    document.getElementById("btn-fw-toggle")?.addEventListener("click", toggleFW);

    // Policy change
    document.getElementById("fw-policy-in")?.addEventListener("change", (e) => {
        setPolicy("incoming", e.target.value);
    });
    document.getElementById("fw-policy-out")?.addEventListener("change", (e) => {
        setPolicy("outgoing", e.target.value);
    });

    // Refresh
    document.getElementById("btn-fw-refresh")?.addEventListener("click", loadStatus);

    // Add rule
    document.getElementById("btn-fw-add")?.addEventListener("click", showAddRule);

    // Reset
    document.getElementById("btn-fw-reset")?.addEventListener("click", doReset);

    // Delete buttons
    document.querySelectorAll(".btn-fw-del").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteRule(parseInt(btn.dataset.num));
        });
    });
}

function renderIPTables() {
    const body = document.getElementById("fw-body");
    if (!body || !fwData) return;

    const renderFamily = (tables, v6) => {
        let html = "";
        for (const [tname, tdata] of Object.entries(tables || {})) {
            let chainsHtml = "";
            for (const [cname, cdata] of Object.entries(tdata.chains || {})) {
                let rulesHtml = (cdata.rules || []).map((r) => `
                    <tr>
                        <td>${r.num}</td>
                        <td>${esc(r.target)}</td>
                        <td style="font-size:11px">${esc(r.prot)}</td>
                        <td style="font-size:11px">${esc(r.source)}</td>
                        <td style="font-size:11px">${esc(r.destination)}</td>
                        <td style="font-size:11px;color:var(--text-dim)">${esc(r.pkts)}</td>
                        <td style="font-size:11px;color:var(--text-dim)">${esc(r.bytes)}</td>
                        <td class="actions"><button class="btn-icon btn-danger btn-fw-del-ipt"
                            data-num="${r.num}" data-chain="${esc(cname)}" data-table="${esc(tname)}" data-v6="${v6 ? 1 : 0}"
                            title="Delete rule">&times;</button></td>
                    </tr>
                `).join("") || `<tr><td colspan="8"><div class="placeholder">No rules in ${esc(cname)}</div></td></tr>`;

                chainsHtml += `
                    <div style="margin-bottom:12px;">
                        <h4 style="color:var(--accent);font-size:13px;">Chain: ${esc(cname)} <span style="color:var(--text-muted)">(policy: ${esc(cdata.policy)})</span></h4>
                        <table>
                            <thead><tr><th>#</th><th>Target</th><th>Proto</th><th>Src</th><th>Dst</th><th>Pkts</th><th>Bytes</th><th></th></tr></thead>
                            <tbody>${rulesHtml}</tbody>
                        </table>
                    </div>`;
            }
            html += `<details style="margin-bottom:16px;"><summary style="font-size:14px;font-weight:600;cursor:pointer;">Table: ${esc(tname)}${v6 ? " (IPv6)" : ""}</summary>${chainsHtml}</details>`;
        }
        return html;
    };

    body.innerHTML = `
        <div style="margin-bottom:16px;">
            <button class="btn" id="btn-fw-refresh">Refresh</button>
        </div>
        ${renderFamily(fwData.tables, false)}
        ${renderFamily(fwData.tables6, true)}
    `;

    document.getElementById("btn-fw-refresh")?.addEventListener("click", loadStatus);
    document.querySelectorAll(".btn-fw-del-ipt").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteIPTablesRule(btn.dataset);
        });
    });
}

async function deleteIPTablesRule(info) {
    const label = `${info.table}/${info.chain} #${info.num}${info.v6 === "1" ? " (IPv6)" : ""}`;
    if (!(await confirm(`Delete iptables rule ${label}?`))) return;
    try {
        const v6 = info.v6 === "1";
        await api.delete(`/firewall/rules/${info.num}?chain=${encodeURIComponent(info.chain)}&table=${encodeURIComponent(info.table)}&v6=${v6}`);
        toast("Rule deleted", "success");
        loadStatus();
    } catch (err) {
        toast("Failed: " + err.message, "error");
    }
}

async function loadStatus() {
    try {
        fwData = await api.get("/firewall/status");
        if (fwData.type === "ufw") {
            renderUFW();
        } else if (fwData.type === "iptables") {
            renderIPTables();
        } else {
            document.getElementById("fw-body").innerHTML = `<div class="placeholder">No firewall detected.</div>`;
        }
    } catch (err) {
        toast("Error: " + err.message, "error");
    }
}

async function toggleFW() {
    if (fwData.enabled) {
        if (!(await confirm("Disable the firewall? This will allow all incoming traffic."))) return;
        try {
            await api.post("/firewall/disable");
            toast("Firewall disabled", "warning");
            loadStatus();
        } catch (err) { toast("Failed: " + err.message, "error"); }
    } else {
        if (!(await confirm("Enable the firewall? This may block incoming connections."))) return;
        try {
            await api.post("/firewall/enable");
            toast("Firewall enabled", "success");
            loadStatus();
        } catch (err) { toast("Failed: " + err.message, "error"); }
    }
}

async function setPolicy(direction, policy) {
    try {
        await api.put("/firewall/policy", { direction, policy });
        toast(`Default ${direction} set to ${policy}`, "success");
        loadStatus();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}

const PRESETS = [
    { label: "SSH",        port: "22",    proto: "tcp" },
    { label: "HTTP",       port: "80",    proto: "tcp" },
    { label: "HTTPS",      port: "443",   proto: "tcp" },
    { label: "Samba",      port: "445",   proto: "tcp" },
    { label: "WireGuard",  port: "51820", proto: "udp" },
    { label: "Jellyfin",   port: "8096",  proto: "tcp" },
    { label: "MySQL",      port: "3306",  proto: "tcp" },
    { label: "PostgreSQL", port: "5432",  proto: "tcp" },
];

function showAddRule() {
    const html = `
        <div style="display:grid;gap:12px;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Direction</label>
                    <select id="ar-dir" style="width:100%"><option value="in">Incoming</option><option value="out">Outgoing</option></select>
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Action</label>
                    <select id="ar-action" style="width:100%"><option value="allow">Allow</option><option value="deny">Deny</option><option value="reject">Reject</option><option value="limit">Limit (rate-limit, UFW only)</option></select>
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Protocol</label>
                    <select id="ar-proto" style="width:100%"><option value="any">Any</option><option value="tcp">TCP</option><option value="udp">UDP</option><option value="both">TCP + UDP</option></select>
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Port (e.g. 80, 8000:9000)</label>
                    <input type="text" id="ar-port" style="width:100%" placeholder="any">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">From IP / CIDR</label>
                    <input type="text" id="ar-from" style="width:100%" placeholder="any" value="any">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">To IP / CIDR</label>
                    <input type="text" id="ar-to" style="width:100%" placeholder="any" value="any">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Interface (optional)</label>
                    <input type="text" id="ar-iface" style="width:100%" placeholder="e.g. eth0">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--text-muted)">Comment</label>
                    <input type="text" id="ar-comment" style="width:100%" maxlength="128" placeholder="optional">
                </div>
            </div>
            <div>
                <label style="font-size:12px;color:var(--text-muted)">Common rules</label>
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">
                    ${PRESETS.map((p, i) => `<button type="button" class="btn btn-sm ar-preset" data-idx="${i}" style="padding:3px 10px;">${esc(p.label)}</button>`).join("")}
                </div>
            </div>
            <div>
                <label style="font-size:12px;color:var(--text-muted)">Preview (exact command that will run)</label>
                <pre id="ar-preview" style="background:var(--bg);padding:10px;border-radius:var(--radius);font-size:11px;font-family:monospace;white-space:pre-wrap;"></pre>
            </div>
        </div>
    `;

    let previewTimer = null;

    function collectRule() {
        return {
            direction: document.getElementById("ar-dir")?.value || "in",
            action: document.getElementById("ar-action")?.value || "allow",
            protocol: document.getElementById("ar-proto")?.value || "",
            port: document.getElementById("ar-port")?.value || "",
            from: document.getElementById("ar-from")?.value || "any",
            to: document.getElementById("ar-to")?.value || "any",
            interface: document.getElementById("ar-iface")?.value || "",
            comment: document.getElementById("ar-comment")?.value || "",
        };
    }

    async function updatePreview() {
        const el = document.getElementById("ar-preview");
        if (!el) return;
        try {
            const res = await api.post("/firewall/preview", collectRule());
            if (res.valid) {
                el.textContent = `$ ${res.command}`;
                el.style.color = "var(--text)";
            } else {
                el.textContent = `✗ ${res.error}`;
                el.style.color = "var(--danger)";
            }
        } catch (err) {
            el.textContent = `✗ ${err.message}`;
            el.style.color = "var(--danger)";
        }
    }

    function queuePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(updatePreview, 250);
    }

    const modal = openModal("Add Firewall Rule", html, [
        {
            label: "Add Rule",
            cls: "btn btn-primary",
            onClick: async () => {
                const rule = collectRule();
                try {
                    const check = await api.post("/firewall/preview", rule);
                    if (!check.valid) {
                        toast(check.error, "error");
                        return false;
                    }
                    await api.post("/firewall/rules", rule);
                    toast("Rule added: " + check.command, "success", 5000);
                    loadStatus();
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

    modal.querySelectorAll(".ar-preset").forEach((btn) => {
        btn.addEventListener("click", () => {
            const p = PRESETS[parseInt(btn.dataset.idx)];
            if (!p) return;
            document.getElementById("ar-port").value = p.port;
            document.getElementById("ar-proto").value = p.proto;
            document.getElementById("ar-action").value = "allow";
            document.getElementById("ar-dir").value = "in";
            updatePreview();
        });
    });

    ["ar-dir", "ar-action", "ar-proto", "ar-port", "ar-from", "ar-to", "ar-iface", "ar-comment"].forEach((id) => {
        document.getElementById(id)?.addEventListener("input", queuePreview);
        document.getElementById(id)?.addEventListener("change", queuePreview);
    });
    updatePreview();
}

async function deleteRule(num) {
    if (!(await confirm(`Delete firewall rule #${num}?`))) return;
    try {
        await api.delete(`/firewall/rules/${num}`);
        toast("Rule deleted", "success");
        loadStatus();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}

async function doReset() {
    if (!(await confirm("Reset firewall to defaults? All rules will be removed."))) return;
    try {
        await api.post("/firewall/reset");
        toast("Firewall reset to defaults", "warning");
        loadStatus();
    } catch (err) { toast("Failed: " + err.message, "error"); }
}

export function init(el) {
    container = el;
    container.innerHTML = `<div id="fw-body"><div class="placeholder"><span class="spinner"></span> Loading...</div></div>`;
}

export function load() { loadStatus(); }
export function destroy() { container = null; fwData = null; }
