import { subscribe, getLatest } from "../ws.js";
import { api } from "../api.js";
import { esc } from "../ui.js";

let container = null;
let unsubscribe = null;
let charts = {};
let range = "1h";          // 1h = live (WS append), 24h/7d = server history
let liveHistory = [];      // recent snapshots when in 1h mode
let cardEls = {};          // for in-place text updates

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
}

function fmtBytesShort(v) {
    if (v >= 1024 ** 3) return (v / 1024 ** 3).toFixed(1) + "G";
    if (v >= 1024 ** 2) return (v / 1024 ** 2).toFixed(0) + "M";
    if (v >= 1024) return (v / 1024).toFixed(0) + "K";
    return v.toFixed(0);
}

function timeLabel(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const PALETTE = ["#4f8ef7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#39c5cf",
                 "#e0823d", "#7ee787", "#ffa198", "#79c0ff", "#d2a8ff", "#56d364"];
const CORE_COLORS = ["#4f8ef7", "#3fb950", "#d29922", "#f85149", "#a371f7", "#39c5cf", "#e0823d", "#7ee787"];
// Chart.js draws on canvas and cannot resolve CSS var() — always use literal hex here.
const CHART_TEXT = "#8b949e";
const CHART_GRID = "rgba(139,148,158,0.10)";

function createChart(canvasId, datasets, opts = {}) {
    const el = document.getElementById(canvasId);
    if (!el) return null;
    return new Chart(el.getContext("2d"), {
        type: "line",
        data: { labels: [], datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: "index" },
            scales: {
                x: {
                    ticks: { maxTicksLimit: 6, color: CHART_TEXT, font: { size: 10 } },
                    grid: { display: false },
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        maxTicksLimit: 5,
                        color: CHART_TEXT,
                        font: { size: 10 },
                        callback: opts.yFormat || ((v) => v),
                    },
                    grid: { color: CHART_GRID },
                },
            },
            plugins: {
                legend: {
                    display: datasets.length > 1,
                    position: "top",
                    labels: { color: CHART_TEXT, boxWidth: 8, padding: 8, font: { size: 10 } },
                },
                tooltip: {
                    enabled: true,
                    backgroundColor: "#1a1d27",
                    borderColor: "#252836",
                    borderWidth: 1,
                    titleColor: "#8b949e",
                    bodyColor: "#e1e4e8",
                    padding: 8,
                    displayColors: false,
                    callbacks: {
                        title: (items) => items.length ? timeLabel(items[0].parsed.x ?? 0) : "",
                        label: opts.tooltipLabel || undefined,
                    },
                },
            },
            elements: { point: { radius: 0, hoverRadius: 3 }, line: { tension: 0.3, borderWidth: 1.5 } },
            parsing: opts.parsing || false,
            normalized: true,
        },
    });
}

// ── Cards: built once, updated via textContent (no flicker) ──

function buildCards(data) {
    const cpu = data.cpu || {}, ram = data.ram || {}, sys = data.system || {}, gpu = data.gpu || {};
    let html = '<div class="cards-grid">';

    html += `<div class="card">
        <div class="card-header"><span class="card-title">CPU</span></div>
        <div class="card-value" id="mc-cpu">–</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(cpu.model || "Unknown")}</div>
        <div style="font-size:11px;color:var(--text-dim)">${cpu.count ?? 0} cores | <span id="mc-temp">–</span></div>
        <div style="height:4px;background:var(--card-border);border-radius:2px;margin-top:6px"><div id="mc-cpu-bar" style="height:4px;width:0%;background:var(--accent);border-radius:2px"></div></div>
    </div>`;

    html += `<div class="card">
        <div class="card-header"><span class="card-title">RAM</span></div>
        <div class="card-value" id="mc-ram">–</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:2px" id="mc-ram-detail">–</div>
        <div style="font-size:11px;color:var(--text-dim)" id="mc-swap">–</div>
        <div style="height:4px;background:var(--card-border);border-radius:2px;margin-top:6px"><div id="mc-ram-bar" style="height:4px;width:0%;background:var(--accent);border-radius:2px"></div></div>
    </div>`;

    html += `<div class="card">
        <div class="card-header"><span class="card-title">System</span></div>
        <div style="font-size:13px">${esc(sys.hostname || "unknown")}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(sys.kernel || "")}</div>
        <div style="font-size:11px;color:var(--text-muted)">${esc(sys.distro || "")}</div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px">Up: <span id="mc-up">–</span> | Load: <span id="mc-load">–</span></div>
    </div>`;

    html += `<div class="card">
        <div class="card-header"><span class="card-title">GPU</span></div>
        <div id="mc-gpu" style="font-size:12px;color:var(--text-muted)">No GPU detected</div>
    </div>`;

    html += '</div><div class="cards-grid" id="mc-extra">';
    html += '</div>';
    return html;
}

function pctColor(p) {
    return p > 90 ? "var(--danger)" : p > 70 ? "var(--warning)" : "var(--accent)";
}

function updateCards(data) {
    if (!cardEls.cpu) return; // not built yet
    const cpu = data.cpu || {}, ram = data.ram || {}, sys = data.system || {}, gpu = data.gpu || {};

    cardEls.cpu.textContent = (cpu.percent ?? 0).toFixed(1) + "%";
    cardEls.cpuBar.style.width = Math.min(cpu.percent ?? 0, 100) + "%";
    cardEls.cpuBar.style.background = pctColor(cpu.percent ?? 0);
    cardEls.temp.textContent = cpu.temperature != null ? cpu.temperature + "°C" : "–";
    cardEls.ram.textContent = (ram.percent ?? 0).toFixed(1) + "%";
    cardEls.ramDetail.textContent = `${formatBytes(ram.used)} / ${formatBytes(ram.total)}`;
    cardEls.ramBar.style.width = Math.min(ram.percent ?? 0, 100) + "%";
    cardEls.ramBar.style.background = pctColor(ram.percent ?? 0);
    cardEls.swap.textContent = `Swap: ${formatBytes(ram.swap_used)} / ${formatBytes(ram.swap_total)}`;
    const up = sys.uptime || 0;
    cardEls.up.textContent = `${Math.floor(up / 86400)}d ${Math.floor(up % 86400 / 3600)}h ${Math.floor(up % 3600 / 60)}m`;
    cardEls.load.textContent = (sys.load_avg || []).slice(0, 3).map(v => v.toFixed(2)).join(" / ");

    // GPU (first device detailed)
    if (gpu.available && gpu.devices && gpu.devices.length > 0) {
        const d = gpu.devices[0];
        const bits = [esc(d.name || "GPU")];
        if (d.utilization != null) bits.push(`${d.utilization}% util`);
        if (d.vram_total) bits.push(`${formatBytes(d.vram_used)} / ${formatBytes(d.vram_total)} VRAM`);
        if (d.temperature) bits.push(`${d.temperature}°C`);
        cardEls.gpu.innerHTML = bits.join("<br>");
    }

    // Disk + net cards: rebuild only the small extra grid (cheap, few nodes)
    const extra = document.getElementById("mc-extra");
    if (!extra) return;
    let html = "";
    for (const d of (data.disk || [])) {
        if (d.error) continue;
        html += `<div class="card">
            <div class="card-header"><span class="card-title">Disk (${esc(d.mountpoint)})</span></div>
            <div class="card-value">${(d.percent ?? 0).toFixed(0)}%</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${formatBytes(d.used)} / ${formatBytes(d.total)}</div>
            <div style="font-size:11px;color:var(--text-dim)">R: ${formatBytes(d.read_bytes_per_sec)}/s · W: ${formatBytes(d.write_bytes_per_sec)}/s</div>
        </div>`;
    }
    for (const n of (data.network || [])) {
        if (n.interface === "lo") continue;
        html += `<div class="card">
            <div class="card-header"><span class="card-title">Net (${esc(n.interface)})</span></div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:4px">↓ ${formatBytes(n.bytes_recv_per_sec)}/s · ↑ ${formatBytes(n.bytes_sent_per_sec)}/s</div>
            <div style="font-size:11px;color:var(--text-dim)">Total: ↓ ${formatBytes(n.bytes_recv)} · ↑ ${formatBytes(n.bytes_sent)}</div>
        </div>`;
    }
    extra.innerHTML = html;
}

// ── Charts ──

function initCharts(perCoreCount) {
    charts.cpu = createChart("chart-cpu", [{ label: "CPU %", data: [], borderColor: "#4f8ef7", backgroundColor: "rgba(79,142,247,0.1)", fill: true, parsing: false }], {
        yFormat: (v) => v + "%",
        tooltipLabel: (item) => `CPU: ${item.parsed.y?.toFixed(1)}%`,
    });

    charts.cores = createChart("chart-cores",
        Array.from({ length: perCoreCount || 0 }, (_, i) => ({
            label: `c${i}`, data: [], borderColor: CORE_COLORS[i % CORE_COLORS.length], borderWidth: 1, parsing: false,
        })), {
        yFormat: (v) => v + "%",
        tooltipLabel: (item) => `${item.dataset.label}: ${item.parsed.y?.toFixed(0)}%`,
    });

    charts.ram = createChart("chart-ram", [{ label: "Used", data: [], borderColor: "#3fb950", backgroundColor: "rgba(63,185,80,0.1)", fill: true, parsing: false }], {
        yFormat: fmtBytesShort,
        tooltipLabel: (item) => `RAM: ${formatBytes(item.parsed.y)}`,
    });

    charts.disk = createChart("chart-disk", [
        { label: "Read", data: [], borderColor: "#4f8ef7", parsing: false },
        { label: "Write", data: [], borderColor: "#d29922", parsing: false },
    ], {
        yFormat: fmtBytesShort,
        tooltipLabel: (item) => `${item.dataset.label}: ${formatBytes(item.parsed.y)}/s`,
    });

    charts.net = createChart("chart-net", [
        { label: "Down", data: [], borderColor: "#4f8ef7", parsing: false },
        { label: "Up", data: [], borderColor: "#3fb950", parsing: false },
    ], {
        yFormat: fmtBytesShort,
        tooltipLabel: (item) => `${item.dataset.label}: ${formatBytes(item.parsed.y)}/s`,
    });

    charts.gpu = createChart("chart-gpu", [
        { label: "Util %", data: [], borderColor: "#a371f7", parsing: false },
    ], {
        yFormat: (v) => v + "%",
        tooltipLabel: (item) => `GPU: ${item.parsed.y?.toFixed(0)}%`,
    });
}

function pushPoint(chart, label, values) {
    if (!chart) return;
    chart.data.labels.push(label);
    chart.data.datasets.forEach((ds, i) => ds.data.push(values[i] ?? null));
    const MAX = range === "1h" ? 720 : 400;
    if (chart.data.labels.length > MAX) {
        chart.data.labels.shift();
        chart.data.datasets.forEach((ds) => ds.data.shift());
    }
}

function renderCharts(history) {
    if (!charts.cpu || !history.length) return;
    // reset datasets from full history
    const labels = history.map((p) => timeLabel(p.timestamp));
    const set = (chart, perDatasetValues) => {
        if (!chart) return;
        chart.data.labels = labels;
        chart.data.datasets.forEach((ds, i) => { ds.data = perDatasetValues(i); });
        chart.update("none");
    };

    set(charts.cpu, () => history.map((p) => p.cpu?.percent ?? 0));
    const coreCount = charts.cores?.data.datasets.length || 0;
    set(charts.cores, (i) => history.map((p) => p.cpu?.per_core?.[i] ?? 0));
    set(charts.ram, () => history.map((p) => p.ram?.used ?? 0));
    set(charts.disk, (i) => history.map((p) => {
        let total = 0;
        for (const d of (p.disk || [])) total += (i === 0 ? d.read_bytes_per_sec : d.write_bytes_per_sec) || 0;
        return total;
    }));
    set(charts.net, (i) => history.map((p) => {
        let total = 0;
        for (const n of (p.network || [])) {
            if (n.interface === "lo") continue;
            total += (i === 0 ? n.bytes_recv_per_sec : n.bytes_sent_per_sec) || 0;
        }
        return total;
    }));
    set(charts.gpu, () => history.map((p) => p.gpu?.devices?.[0]?.utilization ?? null));
}

// ── Range switching ──

async function loadRange(newRange) {
    range = newRange;
    document.querySelectorAll(".mrange").forEach((b) => b.classList.toggle("btn-primary", b.dataset.range === range));
    try {
        const data = await api.get(`/metrics/history?range=${range}`);
        let points = data.points || [];
        if (range === "1h" && getLatest()) points = points.slice(-719).concat([getLatest()]);
        renderCharts(points);
    } catch (err) { /* charts stay as-is */ }
}

function handleMessage(msg) {
    if (msg.type === "history") {
        liveHistory = msg.data || [];
        if (range === "1h") renderCharts(liveHistory);
        const latest = liveHistory[liveHistory.length - 1];
        if (latest) updateCards(latest);
        return;
    }
    if (msg.type === "snapshot") {
        const snap = msg.data;
        updateCards(snap);
        if (range === "1h") {
            liveHistory.push(snap);
            if (liveHistory.length > 720) liveHistory.shift();
            // live-append just the new point (cheap)
            if (charts.cpu) {
                const label = timeLabel(snap.timestamp);
                pushPoint(charts.cpu, label, [snap.cpu?.percent ?? 0]);
                pushPoint(charts.cores, label, (snap.cpu?.per_core || []));
                pushPoint(charts.ram, label, [snap.ram?.used ?? 0]);
                pushPoint(charts.disk, label, [
                    (snap.disk || []).reduce((a, d) => a + (d.read_bytes_per_sec || 0), 0),
                    (snap.disk || []).reduce((a, d) => a + (d.write_bytes_per_sec || 0), 0),
                ]);
                pushPoint(charts.net, label, [
                    (snap.network || []).reduce((a, n) => a + (n.interface === "lo" ? 0 : (n.bytes_recv_per_sec || 0)), 0),
                    (snap.network || []).reduce((a, n) => a + (n.interface === "lo" ? 0 : (n.bytes_sent_per_sec || 0)), 0),
                ]);
                pushPoint(charts.gpu, label, [snap.gpu?.devices?.[0]?.utilization ?? null]);
            }
        }
    }
}

export function init(el) {
    container = el;
    container.innerHTML = `
        <div id="metrics-cards"></div>
        <div style="display:flex;gap:4px;margin:12px 0;">
            <button class="btn btn-sm mrange btn-primary" data-range="1h">1h (live)</button>
            <button class="btn btn-sm mrange" data-range="24h">24h</button>
            <button class="btn btn-sm mrange" data-range="7d">7d</button>
        </div>
        <div id="metrics-graphs">
            <div class="cards-grid" style="grid-template-columns: 1fr 1fr;">
                <div class="card"><div class="card-header"><span class="card-title">CPU Usage</span></div><div style="height:150px"><canvas id="chart-cpu"></canvas></div></div>
                <div class="card"><div class="card-header"><span class="card-title">RAM Used</span></div><div style="height:150px"><canvas id="chart-ram"></canvas></div></div>
            </div>
            <div class="cards-grid" style="grid-template-columns: 1fr 1fr;">
                <div class="card"><div class="card-header"><span class="card-title">Per-Core CPU</span></div><div style="height:150px"><canvas id="chart-cores"></canvas></div></div>
                <div class="card"><div class="card-header"><span class="card-title">GPU</span></div><div style="height:150px"><canvas id="chart-gpu"></canvas></div></div>
            </div>
            <div class="cards-grid" style="grid-template-columns: 1fr 1fr;">
                <div class="card"><div class="card-header"><span class="card-title">Disk I/O</span></div><div style="height:150px"><canvas id="chart-disk"></canvas></div></div>
                <div class="card"><div class="card-header"><span class="card-title">Network I/O</span></div><div style="height:150px"><canvas id="chart-net"></canvas></div></div>
            </div>
        </div>
    `;

    container.querySelectorAll(".mrange").forEach((btn) => {
        btn.addEventListener("click", () => loadRange(btn.dataset.range));
    });
}

export function load() {
    // First snapshot arrives over WS immediately after subscribe; build cards then charts.
    unsubscribe = subscribe(handleMessage);

    // Build cards on first data; charts need core count from first snapshot.
    const bootstrap = async () => {
        try {
            const snap = await api.get("/metrics/current");
            const cardsEl = document.getElementById("metrics-cards");
            if (cardsEl && !cardEls.cpu) {
                cardsEl.innerHTML = buildCards(snap);
                cardEls = {
                    cpu: document.getElementById("mc-cpu"),
                    cpuBar: document.getElementById("mc-cpu-bar"),
                    temp: document.getElementById("mc-temp"),
                    ram: document.getElementById("mc-ram"),
                    ramDetail: document.getElementById("mc-ram-detail"),
                    ramBar: document.getElementById("mc-ram-bar"),
                    swap: document.getElementById("mc-swap"),
                    up: document.getElementById("mc-up"),
                    load: document.getElementById("mc-load"),
                    gpu: document.getElementById("mc-gpu"),
                };
            }
            if (!charts.cpu) initCharts((snap.cpu || {}).count || 0);
            updateCards(snap);
            await loadRange("1h");
        } catch (err) {
            const cardsEl = document.getElementById("metrics-cards");
            if (cardsEl) cardsEl.innerHTML = `<div class="placeholder">Error loading metrics: ${esc(err.message)}</div>`;
        }
    };
    bootstrap();
}

export function destroy() {
    if (unsubscribe) { unsubscribe(); unsubscribe = null; }
    for (const key of Object.keys(charts)) {
        charts[key]?.destroy();
        charts[key] = null;
    }
    charts = {};
    cardEls = {};
    liveHistory = [];
    container = null;
}
