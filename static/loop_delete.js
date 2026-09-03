// loop_delete.js — Loop Delete Frontend

(function () {
    "use strict";

    var APP_BASE = (window.APP_BASE || "");


    const form = document.getElementById("ld-form");
    const accountInput = document.getElementById("ld-account");
    const sendersInput = document.getElementById("ld-senders");
    const keywordsInput = document.getElementById("ld-keywords");
    const intervalInput = document.getElementById("ld-interval");
    const startBtn = document.getElementById("ld-start-btn");
    const jobsList = document.getElementById("ld-jobs-list");
    const jobsCount = document.getElementById("ld-jobs-count");

    let pollInterval = null;

    // Start polling on page load
    loadJobs();
    pollInterval = setInterval(loadJobs, 2000);

    // Form submit
    form.addEventListener("submit", async function (e) {
        e.preventDefault();

        var accountStr = accountInput.value.trim();
        var senders = sendersInput.value.trim();
        var keywords = keywordsInput.value.trim();
        var interval = parseInt(intervalInput.value, 10) || 5;

        if (!accountStr) return alert("Masukkan akun email!");
        if (!senders && !keywords) return alert("Isi minimal salah satu: Sender atau Keyword!");

        var colonIdx = accountStr.indexOf(":");
        if (colonIdx === -1) return alert("Format akun: email:password");
        var emailAddr = accountStr.substring(0, colonIdx).trim();
        var password = accountStr.substring(colonIdx + 1).trim();
        if (!emailAddr || !password) return alert("Email dan password wajib diisi!");

        startBtn.disabled = true;
        startBtn.querySelector(".btn-text").textContent = "Memulai...";

        try {
            var res = await fetch(APP_BASE+"/api/loop-delete/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: emailAddr,
                    password: password,
                    senders: senders,
                    keywords: keywords,
                    interval: interval,
                }),
            });

            var data = await res.json();

            if (data.success) {
                accountInput.value = "";
                loadJobs();
            } else {
                alert("❌ " + (data.error || "Gagal memulai loop delete"));
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
        }

        startBtn.disabled = false;
        startBtn.querySelector(".btn-text").textContent = "Mulai Loop Delete";
    });

    async function loadJobs() {
        try {
            var res = await fetch(APP_BASE+"/api/loop-delete/list");
            var jobs = await res.json();
            renderJobs(jobs);
        } catch (err) {
            // Silently fail
        }
    }

    function renderJobs(jobs) {
        if (!jobs || jobs.length === 0) {
            jobsList.innerHTML = '<div class="ld-empty">Belum ada loop delete yang berjalan</div>';
            jobsCount.textContent = "0";
            return;
        }

        jobsCount.textContent = jobs.filter(function (j) { return j.status === "running"; }).length;

        var html = "";
        jobs.forEach(function (j) {
            var isRunning = j.status === "running";
            var statusClass = isRunning ? "ld-running" : "ld-stopped";
            var statusText = isRunning ? "🟢 Berjalan" : "🔴 Berhenti";
            var sendersList = (j.senders || []).join(", ") || "-";
            var keywordsList = (j.keywords || []).join(", ") || "-";

            html +=
                '<div class="ld-job-card ' + statusClass + '">' +
                '<div class="ld-job-header">' +
                '<div class="ld-job-info">' +
                '<div class="ld-job-email">' + escapeHtml(j.email) + '</div>' +
                '<div class="ld-job-id">ID: ' + j.job_id + ' • ' + statusText + '</div>' +
                '</div>' +
                '<div class="ld-btn-group">' +
                (isRunning ?
                    '<button class="btn-stop ld-stop-btn" onclick="stopJob(\'' + j.job_id + '\')">' +
                    '<span class="btn-icon">⏹️</span><span class="btn-text">Stop</span>' +
                    '</button>' :
                    '<button class="ld-restart-btn" onclick="restartJob(\'' + j.job_id + '\')">' +
                    '▶️ Start' +
                    '</button>' +
                    '<button class="ld-delete-btn" onclick="deleteLoopJob(\'' + j.job_id + '\')">' +
                    '🗑️ Hapus' +
                    '</button>'
                ) +
                '</div>' +
                '</div>' +
                '<div class="ld-job-stats">' +
                '<div class="ld-stat"><span class="ld-stat-value">' + j.cycles + '</span><span class="ld-stat-label">Siklus</span></div>' +
                '<div class="ld-stat"><span class="ld-stat-value">' + j.total_deleted + '</span><span class="ld-stat-label">Total Dihapus</span></div>' +
                '<div class="ld-stat"><span class="ld-stat-value">' + j.last_cycle_deleted + '</span><span class="ld-stat-label">Siklus Terakhir</span></div>' +
                '<div class="ld-stat"><span class="ld-stat-value">' + (j.last_cycle_time || "-") + '</span><span class="ld-stat-label">Waktu Terakhir</span></div>' +
                '</div>' +
                '<div class="ld-job-criteria">' +
                '<span>📧 Sender: ' + escapeHtml(sendersList) + '</span>' +
                '<span>🔍 Keyword: ' + escapeHtml(keywordsList) + '</span>' +
                '<span>⏱️ Interval: ' + j.interval + 's</span>' +
                (j.error ? '<span class="ld-error">⚠️ ' + escapeHtml(j.error) + '</span>' : '') +
                '</div>' +
                '</div>';
        });

        jobsList.innerHTML = html;
    }

    window.stopJob = async function (jobId) {
        if (!confirm("Stop loop delete ini?")) return;
        try {
            await fetch(APP_BASE+"/api/loop-delete/stop/" + jobId, { method: "POST" });
            loadJobs();
        } catch (err) {
            alert("Error: " + err.message);
        }
    };

    window.restartJob = async function (jobId) {
        try {
            var res = await fetch(APP_BASE+"/api/loop-delete/restart/" + jobId, { method: "POST" });
            var data = await res.json();
            if (data.success) {
                loadJobs();
            } else {
                alert("❌ " + (data.error || "Gagal restart"));
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
        }
    };

    window.deleteLoopJob = async function (jobId) {
        if (!confirm("⚠️ Hapus loop delete job ini?")) return;
        try {
            var res = await fetch(APP_BASE+"/api/loop-delete/delete/" + jobId, { method: "POST" });
            var data = await res.json();
            if (data.success) {
                loadJobs();
            } else {
                alert("❌ " + (data.error || "Gagal hapus"));
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
        }
    };

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }
})();
