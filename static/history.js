// history.js — Job History Frontend (private per user, click to show live list)

(function () {
    "use strict";

    var APP_BASE = (window.APP_BASE || "");


    var loading = document.getElementById("history-loading");
    var empty = document.getElementById("history-empty");
    var list = document.getElementById("history-list");

    async function loadJobs() {
        try {
            var res = await fetch(APP_BASE+"/api/jobs");
            var jobs = await res.json();

            loading.classList.add("hidden");

            if (!jobs || jobs.length === 0) {
                empty.classList.remove("hidden");
                list.innerHTML = "";
                return;
            }
            empty.classList.add("hidden");
            renderJobs(jobs);
        } catch (err) {
            loading.classList.add("hidden");
            list.innerHTML = '<div class="ge-error">❌ Gagal memuat: ' + err.message + '</div>';
        }
    }

    function renderJobs(jobs) {
        list.innerHTML = "";
        jobs.forEach(function (j) {
            var card = document.createElement("div");
            card.className = "hist-card " + (j.status === "running" ? "hist-running" : j.status === "done" ? "hist-done" : "hist-stopped");
            card.id = "hist-" + j.job_id;

            var statusIcon = j.status === "running" ? "🔄" : j.status === "done" ? "✅" : "⏹️";
            var statusText = j.status === "running" ? "Sedang Berjalan" : j.status === "done" ? "Selesai" : "Dihentikan";

            var ownerBadge = j.owner_label ? '<span class="hist-owner-badge">👤 ' + escapeHtml(j.owner_label) + '</span>' : '';

            var pctBar = '<div class="hist-progress"><div class="hist-progress-fill" style="width:' + j.percentage + '%"></div></div>';

            var statsHtml =
                '<div class="hist-stats">' +
                '<span class="hist-stat live">✅ ' + j.live + '</span>' +
                '<span class="hist-stat noemail">📭 ' + j.noemail + '</span>' +
                '<span class="hist-stat die">💀 ' + j.die + '</span>' +
                '<span class="hist-stat unreg">❓ ' + j.unreg + '</span>' +
                '<span class="hist-stat skipped">⏭️ ' + j.skipped + '</span>' +
                '</div>';

            var meta = j.meta || {};
            var metaHtml = '<div class="hist-meta">' +
                '<span>📊 ' + j.checked + '/' + j.total + ' akun (' + j.percentage + '%)</span>' +
                '<span>📅 ' + j.created_at + '</span>' +
                '<span>⏰ Expires: ' + j.expires_at + '</span>' +
                '</div>';

            var buttonsHtml = '<div class="hist-actions">';
            if (j.live > 0) buttonsHtml += '<a href="' + APP_BASE + '/api/download/' + j.job_id + '/live" class="hist-dl-btn live" download>✅ Live.txt</a>';
            if (j.noemail > 0) buttonsHtml += '<a href="' + APP_BASE + '/api/download/' + j.job_id + '/noemail" class="hist-dl-btn noemail" download>📭 NoEmail.txt</a>';
            if (j.die > 0) buttonsHtml += '<a href="' + APP_BASE + '/api/download/' + j.job_id + '/die" class="hist-dl-btn die" download>💀 Die.txt</a>';
            if (j.unreg > 0) buttonsHtml += '<a href="' + APP_BASE + '/api/download/' + j.job_id + '/unreg" class="hist-dl-btn unreg" download>❓ Unreg.txt</a>';
            if (j.noimap > 0) buttonsHtml += '<a href="' + APP_BASE + '/api/download/' + j.job_id + '/noimap" class="hist-dl-btn noimap" download>📥 No IMAP.txt</a>';
            buttonsHtml += '<button class="hist-extend-btn" onclick="event.stopPropagation();extendJob(\'' + j.job_id + '\')">➕ Tambah 7 Hari</button>';
            buttonsHtml += '<button class="hist-delete-btn" onclick="event.stopPropagation();deleteJob(\'' + j.job_id + '\')">🗑️ Hapus</button>';
            buttonsHtml += '</div>';

            // Live list expand section (hidden by default)
            var liveSection = '<div class="hist-live-section hidden" id="live-' + j.job_id + '">' +
                '<div class="hist-live-header">📋 Live Accounts</div>' +
                '<div class="hist-live-list" id="live-list-' + j.job_id + '">' +
                '<div class="hist-live-loading">Memuat...</div>' +
                '</div></div>';

            card.innerHTML =
                '<div class="hist-header hist-clickable" onclick="toggleLive(\'' + j.job_id + '\')">' +
                '<div class="hist-title">' +
                '<span class="hist-status-icon">' + statusIcon + '</span>' +
                '<span class="hist-job-id">Job #' + j.job_id + '</span>' +
                '<span class="hist-status-badge ' + j.status + '">' + statusText + '</span>' +
                ownerBadge +
                '<span class="hist-expand-hint">🔽 klik untuk lihat live</span>' +
                '</div>' +
                '</div>' +
                pctBar + statsHtml + metaHtml + buttonsHtml + liveSection;

            list.appendChild(card);
        });
    }

    window.toggleLive = async function (jobId) {
        var section = document.getElementById("live-" + jobId);
        var listEl = document.getElementById("live-list-" + jobId);

        if (section.classList.contains("hidden")) {
            section.classList.remove("hidden");
            listEl.innerHTML = '<div class="hist-live-loading">⏳ Memuat live accounts...</div>';

            try {
                var res = await fetch(APP_BASE+"/api/jobs/" + jobId + "/live");
                var data = await res.json();

                if (data.success && data.accounts && data.accounts.length > 0) {
                    var html = '<div class="hist-live-count">' + data.count + ' akun live</div>';
                    data.accounts.forEach(function (acc, idx) {
                        html += '<div class="hist-live-item">' +
                            '<span class="hist-live-num">#' + (idx + 1) + '</span>' +
                            '<span class="hist-live-account">' + escapeHtml(acc) + '</span>' +
                            '</div>';
                    });
                    listEl.innerHTML = html;
                } else if (data.success) {
                    listEl.innerHTML = '<div class="hist-live-empty">Belum ada akun live</div>';
                } else {
                    listEl.innerHTML = '<div class="hist-live-empty">❌ ' + (data.error || 'Gagal memuat') + '</div>';
                }
            } catch (err) {
                listEl.innerHTML = '<div class="hist-live-empty">❌ Error: ' + err.message + '</div>';
            }
        } else {
            section.classList.add("hidden");
        }
    };

    window.extendJob = async function (jobId) {
        try {
            var res = await fetch(APP_BASE+"/api/jobs/" + jobId + "/extend", { method: "POST" });
            var data = await res.json();
            if (data.success) {
                alert("✅ " + data.message + "\nExpires: " + data.expires_at);
                loadJobs();
            } else {
                alert("❌ " + (data.error || "Gagal"));
            }
        } catch (err) {
            alert("❌ Error: " + err.message);
        }
    };

    window.deleteJob = async function (jobId) {
        if (!confirm("⚠️ Hapus job #" + jobId + " beserta semua file hasilnya?")) return;
        try {
            var res = await fetch(APP_BASE+"/api/jobs/" + jobId + "/delete", { method: "POST" });
            var data = await res.json();
            if (data.success) {
                loadJobs();
            } else {
                alert("❌ " + (data.error || "Gagal"));
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

    loadJobs();
    setInterval(loadJobs, 5000);
})();
