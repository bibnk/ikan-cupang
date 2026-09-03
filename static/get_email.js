// get_email.js — Get Email Frontend (modal popup for email body)

(function () {
    "use strict";

    var APP_BASE = (window.APP_BASE || "");


    var DEFAULT_SENDERS = "@booking.com\nrewards@booking.com\nemail.campaign@sg.booking.com\n@account.agoda.com\n@agoda.com";

    const form = document.getElementById("get-email-form");
    const accountInput = document.getElementById("ge-account");
    const sendersInput = document.getElementById("ge-senders");
    const keywordsInput = document.getElementById("ge-keywords");
    const daysInput = document.getElementById("ge-days");
    const submitBtn = document.getElementById("ge-submit");

    const loadingDiv = document.getElementById("ge-loading");
    const errorDiv = document.getElementById("ge-error");
    const resultsSection = document.getElementById("ge-results");
    const emailList = document.getElementById("ge-email-list");
    const countBadge = document.getElementById("ge-count");
    const emptyDiv = document.getElementById("ge-empty");

    // Modal elements
    const modal = document.getElementById("ge-modal");
    const modalClose = document.getElementById("ge-modal-close");
    const modalSubject = modal.querySelector(".ge-modal-subject");
    const modalInfo = modal.querySelector(".ge-modal-info");
    const modalBody = modal.querySelector(".ge-modal-body");

    // Store credentials and email data
    let currentEmail = "";
    let currentPassword = "";
    let emailsData = []; // store fetched emails for modal

    // ---- Modal controls ----
    function openModal(idx) {
        var em = emailsData[idx];
        if (!em) return;

        modalSubject.textContent = em.subject || "(Tanpa subjek)";
        modalInfo.innerHTML = "📧 " + escapeHtml(em.from) + " &nbsp;•&nbsp; 📅 " + escapeHtml(em.date);

        // Build body + links
        var html = em.body_html || "";

        if (em.links && em.links.length > 0) {
            html += '<div class="ge-links-section">' +
                '<div class="ge-links-header">🔗 Link Ditemukan (' + em.links.length + ')</div>' +
                '<div class="ge-links-list">';
            em.links.forEach(function (link, i) {
                var label = link.label || "";
                var url = link.url || "";
                if (label && label !== url) {
                    html += '<div class="ge-link-item">' +
                        '<span class="ge-link-num">' + (i + 1) + '.</span>' +
                        '<div class="ge-link-detail">' +
                        '<span class="ge-link-label">' + escapeHtml(label) + '</span>' +
                        '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="ge-link-url">' + escapeHtml(url) + '</a>' +
                        '</div></div>';
                } else {
                    html += '<div class="ge-link-item">' +
                        '<span class="ge-link-num">' + (i + 1) + '.</span>' +
                        '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="ge-link-url">' + escapeHtml(url) + '</a>' +
                        '</div>';
                }
            });
            html += '</div></div>';
        }

        modalBody.innerHTML = html;
        modal.classList.remove("hidden");
        document.body.style.overflow = "hidden";
    }

    function closeModal() {
        modal.classList.add("hidden");
        document.body.style.overflow = "";
    }

    modalClose.addEventListener("click", closeModal);
    modal.querySelector(".ge-modal-backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !modal.classList.contains("hidden")) {
            closeModal();
        }
    });

    // ---- Form submit ----
    form.addEventListener("submit", async function (e) {
        e.preventDefault();

        const accountStr = accountInput.value.trim();
        let senders = sendersInput.value.trim();
        let keywords = keywordsInput.value.trim();
        const days = parseInt(daysInput.value, 10) || 365;

        if (!accountStr) return alert("Masukkan akun email!");
        if (!senders && !keywords) {
            senders = DEFAULT_SENDERS;
        }

        const colonIdx = accountStr.indexOf(":");
        if (colonIdx === -1) return alert("Format akun: email:password");
        const emailAddr = accountStr.substring(0, colonIdx).trim();
        const password = accountStr.substring(colonIdx + 1).trim();
        if (!emailAddr || !password) return alert("Email dan password wajib diisi!");

        currentEmail = emailAddr;
        currentPassword = password;

        submitBtn.disabled = true;
        submitBtn.querySelector(".btn-text").textContent = "Mengambil email...";
        loadingDiv.classList.remove("hidden");
        errorDiv.classList.add("hidden");
        resultsSection.classList.add("hidden");
        emptyDiv.classList.add("hidden");
        emailList.innerHTML = "";
        emailsData = [];

        try {
            const res = await fetch(APP_BASE+"/api/get-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: emailAddr,
                    password: password,
                    senders: senders,
                    keywords: keywords,
                    search_days: days,
                }),
            });

            const data = await res.json();
            loadingDiv.classList.add("hidden");
            submitBtn.disabled = false;
            submitBtn.querySelector(".btn-text").textContent = "Ambil Email";

            if (!data.success) {
                errorDiv.textContent = "❌ " + (data.error || "Terjadi kesalahan");
                errorDiv.classList.remove("hidden");
                return;
            }

            if (!data.emails || data.emails.length === 0) {
                emptyDiv.classList.remove("hidden");
                return;
            }

            emailsData = data.emails;
            countBadge.textContent = data.emails.length;
            resultsSection.classList.remove("hidden");

            data.emails.forEach(function (em, idx) {
                var card = document.createElement("div");
                card.className = "ge-email-card";
                card.setAttribute("data-uid", em.uid || "");

                card.innerHTML =
                    '<div class="ge-email-header">' +
                    '<div class="ge-email-num">#' + (idx + 1) + '</div>' +
                    '<div class="ge-email-meta">' +
                    '<div class="ge-email-subject">' + escapeHtml(em.subject || "(Tanpa subjek)") + '</div>' +
                    '<div class="ge-email-info">' +
                    '<span class="ge-email-from">📧 ' + escapeHtml(em.from) + '</span>' +
                    '<span class="ge-email-date">📅 ' + escapeHtml(em.date) + '</span>' +
                    '</div>' +
                    '</div>' +
                    '<div class="ge-btn-group">' +
                    '<button class="ge-toggle-btn" data-idx="' + idx + '">' +
                    '<span class="ge-toggle-label">👁 Tampilkan</span>' +
                    '</button>' +
                    '<button class="ge-delete-btn" data-uid="' + escapeHtml(em.uid || '') + '">' +
                    '🗑️ Hapus' +
                    '</button>' +
                    '</div>' +
                    '</div>';
                emailList.appendChild(card);
            });

            // Attach view handlers (open modal)
            document.querySelectorAll(".ge-toggle-btn").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var idx = parseInt(this.getAttribute("data-idx"), 10);
                    openModal(idx);
                });
            });

            // Attach delete handlers
            document.querySelectorAll(".ge-delete-btn").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var uid = this.getAttribute("data-uid");
                    handleDelete(uid, this);
                });
            });

        } catch (err) {
            loadingDiv.classList.add("hidden");
            submitBtn.disabled = false;
            submitBtn.querySelector(".btn-text").textContent = "Ambil Email";
            errorDiv.textContent = "❌ Gagal menghubungi server: " + err.message;
            errorDiv.classList.remove("hidden");
        }
    });

    async function handleDelete(uid, btnEl) {
        if (!uid) return alert("UID email tidak ditemukan.");
        if (!confirm("⚠️ Email ini akan dihapus PERMANEN (termasuk dari Trash). Lanjutkan?")) return;

        var card = btnEl.closest(".ge-email-card");
        btnEl.disabled = true;
        btnEl.textContent = "Menghapus...";

        try {
            var res = await fetch(APP_BASE+"/api/delete-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: currentEmail,
                    password: currentPassword,
                    uid: uid,
                }),
            });

            var data = await res.json();

            if (data.success) {
                card.classList.add("ge-email-deleted");
                card.innerHTML =
                    '<div class="ge-deleted-msg">' +
                    '<span>✅ Email berhasil dihapus permanen</span>' +
                    '</div>';
                var remaining = document.querySelectorAll(".ge-email-card:not(.ge-email-deleted)").length;
                countBadge.textContent = remaining;
            } else {
                alert("❌ Gagal menghapus: " + (data.error || "Unknown error"));
                btnEl.disabled = false;
                btnEl.textContent = "🗑️ Hapus";
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
            btnEl.disabled = false;
            btnEl.textContent = "🗑️ Hapus";
        }
    }

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }
})();
