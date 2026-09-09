import { toast, esc } from "./ui.js";
import { api } from "./api.js";
import { initAIPanel } from "./ai_panel.js";

// --- Auth / Login ---

async function checkAuth() {
    const token = localStorage.getItem("token");
    if (!token) return false;
    try {
        await api.get("/health");
        return true;
    } catch (e) {
        localStorage.removeItem("token");
        return false;
    }
}

async function showLogin() {
    document.getElementById("app-shell").style.display = "none";
    document.getElementById("login-screen").style.display = "flex";

    try {
        const status = await fetch("/api/auth/status").then(r => r.json());
        if (status.default_password) {
            document.getElementById("login-warning").style.display = "block";
        }
    } catch (e) {}

    document.getElementById("btn-login").onclick = async () => {
        const username = document.getElementById("login-username").value.trim();
        const password = document.getElementById("login-password").value;
        const errEl = document.getElementById("login-error");
        errEl.style.display = "none";

        try {
            const resp = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password }),
            });
            const data = await resp.json();
            if (!resp.ok) {
                errEl.textContent = data.detail || "Login failed";
                errEl.style.display = "block";
                return;
            }
            localStorage.setItem("token", data.token);
            document.getElementById("login-screen").style.display = "none";
            document.getElementById("app-shell").style.display = "flex";
            if (data.default_password) {
                toast("Warning: You are using the default password. Change it in Settings.", "warning", 5000);
            }
            initApp();
        } catch (e) {
            errEl.textContent = "Connection error";
            errEl.style.display = "block";
        }
    };

    document.getElementById("login-password").onkeydown = (e) => {
        if (e.key === "Enter") document.getElementById("btn-login").click();
    };
}

window.showLogin = showLogin;

// --- Section routing ---

const sectionModules = {
    metrics:  () => import("./sections/metrics.js"),
    network:  () => import("./sections/network.js"),
    services: () => import("./sections/services.js"),
    processes:() => import("./sections/processes.js"),
    packages: () => import("./sections/packages.js"),
    firewall: () => import("./sections/firewall.js"),
    settings: () => import("./sections/settings.js"),
};

let currentSection = null;
let currentModule = null;

const sectionTitleEl = document.getElementById("section-title");
const sectionBodyEl = document.getElementById("section-body");
const navItems = document.querySelectorAll("#sidebar-nav .nav-item");

function initApp() {
    navItems.forEach((item) => {
        item.replaceWith(item.cloneNode(true));
    });
    const freshItems = document.querySelectorAll("#sidebar-nav .nav-item");
    freshItems.forEach((item) => {
        item.addEventListener("click", async (e) => {
            e.preventDefault();
            const section = item.dataset.section;
            if (section && section !== currentSection) {
                await loadSection(section);
            }
        });
    });

    initAIPanel();

    fetchEnabledSections().then(() => loadSection("metrics"));
}

async function loadSection(name) {
    if (currentModule && currentModule.destroy) {
        currentModule.destroy();
    }

    const items = document.querySelectorAll("#sidebar-nav .nav-item");
    items.forEach((el) => {
        el.classList.toggle("active", el.dataset.section === name);
    });

    sectionBodyEl.innerHTML = '<div class="placeholder"><span class="spinner"></span> Loading...</div>';
    sectionTitleEl.textContent = name.charAt(0).toUpperCase() + name.slice(1);

    try {
        const mod = await sectionModules[name]();
        currentModule = mod;
        currentSection = name;

        const sectionEl = document.createElement("div");
        sectionEl.id = `section-${name}`;
        sectionBodyEl.innerHTML = "";
        sectionBodyEl.appendChild(sectionEl);

        if (mod.init) mod.init(sectionEl);
        if (mod.load) mod.load();
        applyPendingFilter(name);
    } catch (err) {
        console.error(`Failed to load section ${name}:`, err);
        sectionBodyEl.innerHTML = `<div class="placeholder">Error loading section: ${esc(err.message)}</div>`;
    }
}

const FILTER_INPUTS = {
    services: "svc-search",
    processes: "proc-search",
    packages: "pkg-search",
    network: "net-proc-search",
};

function applyPendingFilter(section) {
    const f = window.__pendingFilter;
    window.__pendingFilter = "";
    if (!f) return;
    const el = document.getElementById(FILTER_INPUTS[section]);
    if (el) {
        el.value = f;
        el.dispatchEvent(new Event("input"));
    }
}

// Cross-section links: navigateSection("services", "nginx") jumps + filters.
window.navigateSection = async function(section, filter) {
    if (!sectionModules[section]) return;
    window.__pendingFilter = filter || "";
    if (section !== currentSection) {
        await loadSection(section);
    } else {
        applyPendingFilter(section);
    }
};

async function fetchEnabledSections() {
    try {
        const data = await api.get("/settings");
        if (data && data.sections) {
            const items = document.querySelectorAll("#sidebar-nav .nav-item");
            items.forEach((el) => {
                const key = el.dataset.section;
                if (key === "settings") return;
                if (!data.sections[key]) {
                    el.classList.add("hidden");
                }
            });
        }
        window.AI_ENABLED = !!(data && data.ai && data.ai.enabled);
    } catch (e) {}
}

// --- Startup ---

checkAuth().then((ok) => {
    if (ok) {
        document.getElementById("login-screen").style.display = "none";
        document.getElementById("app-shell").style.display = "flex";
        initApp();
    } else {
        showLogin();
    }
});
