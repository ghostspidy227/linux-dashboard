const BASE = "/api";

function getToken() {
    return localStorage.getItem("token");
}

async function request(method, path, body = null) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const opts = { method, headers };
    if (body && method !== "GET") opts.body = JSON.stringify(body);

    const res = await fetch(BASE + path, opts);
    if (res.status === 401) {
        localStorage.removeItem("token");
        if (typeof window.showLogin === "function") window.showLogin();
        throw new Error("Authentication required. Please log in again.");
    }
    if (!res.ok) {
        const err = await res.text();
        throw new Error(err || `HTTP ${res.status}`);
    }
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("application/json")) return res.json();
    return res;
}

export const api = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    delete: (path) => request("DELETE", path),
};
