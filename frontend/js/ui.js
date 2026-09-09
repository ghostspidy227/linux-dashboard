const toastContainer = document.getElementById("toast-container");

export function esc(str) {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return String(str ?? "").replace(/[&<>"']/g, (c) => map[c]);
}

export function toast(message, type = "success", duration = 3000) {
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    el.textContent = message;
    toastContainer.appendChild(el);
    setTimeout(() => {
        el.style.opacity = "0";
        el.style.transition = "opacity 0.2s";
        setTimeout(() => el.remove(), 200);
    }, duration);
}

export function confirm(message) {
    return new Promise((resolve) => {
        const overlay = document.getElementById("modal-overlay");
        overlay.innerHTML = "";
        overlay.classList.remove("hidden");

        const modal = document.createElement("div");
        modal.className = "modal";
        modal.style.minWidth = "340px";
        modal.innerHTML = `
            <div class="modal-header">
                <h3>Confirm Action</h3>
            </div>
            <div class="modal-body">
                <p class="confirm-message">${esc(message)}</p>
            </div>
            <div class="modal-footer">
                <button class="btn btn-cancel">Cancel</button>
                <button class="btn btn-primary btn-confirm">Confirm</button>
            </div>
        `;

        modal.querySelector(".btn-cancel").addEventListener("click", () => {
            overlay.classList.add("hidden");
            resolve(false);
        });
        modal.querySelector(".btn-confirm").addEventListener("click", () => {
            overlay.classList.add("hidden");
            resolve(true);
        });
        overlay.onclick = (e) => {
            if (e.target === overlay) {
                overlay.classList.add("hidden");
                resolve(false);
            }
        };

        overlay.appendChild(modal);
    });
}

export function openModal(title, contentHtml, buttons = []) {
    const overlay = document.getElementById("modal-overlay");
    overlay.innerHTML = "";
    overlay.classList.remove("hidden");

    const modal = document.createElement("div");
    modal.className = "modal";
    modal.style.width = "720px";
    modal.style.maxWidth = "95vw";

    let btnHtml = buttons.map((b, i) => {
        const cls = b.cls || "btn";
        return `<button class="${cls}" data-btn-idx="${i}">${b.label}</button>`;
    }).join("");

    modal.innerHTML = `
        <div class="modal-header">
            <h3>${esc(title)}</h3>
            <button class="btn-close">&times;</button>
        </div>
        <div class="modal-body">${contentHtml}</div>
        <div class="modal-footer">${btnHtml || ""}</div>
    `;

    const close = () => overlay.classList.add("hidden");
    modal.querySelector(".btn-close").addEventListener("click", close);
    overlay.onclick = (e) => {
        if (e.target === overlay) close();
    };

    buttons.forEach((b, i) => {
        const btn = modal.querySelector(`[data-btn-idx="${i}"]`);
        if (btn && b.onClick) {
            btn.addEventListener("click", () => {
                const result = b.onClick(modal);
                if (result !== false) close();
            });
        }
    });

    overlay.appendChild(modal);
    return modal;
}

export function closeModal() {
    document.getElementById("modal-overlay").classList.add("hidden");
}

export function openDrawer() {
    document.getElementById("ai-panel").classList.remove("hidden");
}

export function closeDrawer() {
    document.getElementById("ai-panel").classList.add("hidden");
}
