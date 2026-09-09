let ws = null;
let reconnectTimer = null;
let listeners = new Set();
let historyData = [];
let latestSnapshot = null;

function getWsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/ws/metrics`;
}

function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    if (!localStorage.getItem("token")) return; // nothing to auth with; subscribe() retries post-login

    ws = new WebSocket(getWsUrl());

    // Server requires {"type":"auth"} as the first message before sending anything.
    ws.onopen = () => {
        const token = localStorage.getItem("token");
        if (!token) { ws.close(); return; }
        ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "history") {
                historyData = msg.data || [];
                for (const fn of listeners) fn({ type: "history", data: historyData });
            } else {
                latestSnapshot = msg;
                historyData.push(msg);
                if (historyData.length > 720) historyData.shift();
                for (const fn of listeners) fn({ type: "snapshot", data: msg });
            }
        } catch (e) {
            // ignore malformed messages
        }
    };

    ws.onclose = (event) => {
        ws = null;
        if (event.code === 4401) {
            // Auth rejected — stale token. Drop it and surface login again.
            localStorage.removeItem("token");
            if (typeof window.showLogin === "function") window.showLogin();
            return;
        }
        scheduleReconnect();
    };

    ws.onerror = () => {
        ws?.close();
    };
}

function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
    }, 3000);
}

export function subscribe(fn) {
    listeners.add(fn);
    if (!ws || ws.readyState === WebSocket.CLOSED) connect();
    if (historyData.length > 0) {
        fn({ type: "history", data: historyData });
    }
    if (latestSnapshot) {
        fn({ type: "snapshot", data: latestSnapshot });
    }
    return () => listeners.delete(fn);
}

export function getLatest() {
    return latestSnapshot;
}

export function getHistory() {
    return historyData;
}

connect();
