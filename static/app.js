// app.js — IMAP Email Checker Frontend (persistent + file upload + default senders)

(function () {
    "use strict";

    var APP_BASE = (window.APP_BASE || "");


    var DEFAULT_SENDERS = "noreply@booking.com\nrewards@booking.com\nemail.campaign@sg.booking.com\n@account.agoda.com\n@agoda.com";

    var form = document.getElementById("checker-form");
    var accountsInput = document.getElementById("accounts-input");
    var accountsFile = document.getElementById("accounts-file");
    var sendersInput = document.getElementById("senders-input");
    var keywordsInput = document.getElementById("keywords-input");
    var daysInput = document.getElementById("days-input");
    var threadsInput = document.getElementById("threads-input");
    var startBtn = document.getElementById("start-btn");
    var stopBtn = document.getElementById("stop-btn");
    var accountsCount = document.getElementById("accounts-count");
    var fileInfo = document.getElementById("file-info");
    var fileDropArea = document.getElementById("file-drop-area");

    var progressSection = document.getElementById("progress-section");
    var progressBar = document.getElementById("progress-bar");
    var progressPct = document.getElementById("progress-percentage");
    var progressDetail = document.getElementById("progress-detail");
    var progressStatus = document.getElementById("progress-status");

    var statLive = document.getElementById("stat-live");
    var statNoemail = document.getElementById("stat-noemail");
    var statDie = document.getElementById("stat-die");
    var statUnreg = document.getElementById("stat-unreg");
    var statSkipped = document.getElementById("stat-skipped");

    var liveSection = document.getElementById("live-section");
    var liveList = document.getElementById("live-list");
    var liveCounter = document.getElementById("live-counter");

    var resultsSection = document.getElementById("results-section");

    var currentJobId = null;
    var evtSource = null;
    var renderedLiveCount = 0;
    var fileContent = "";  // Content from uploaded file
    var currentInputMode = "text"; // "text" or "file"
    var currentJobSenders = ""; // Senders used in current check job
    var currentJobKeywords = ""; // Keywords used in current check job

    // Proxy state
    var useProxy = false;
    var proxyFileContent = "";
    var proxyInputMode = "text";
    var proxyInput = document.getElementById("proxy-input");
    var proxyFileInput = document.getElementById("proxy-file");
    var proxyCount = document.getElementById("proxy-count");

    // ---- Tab switching ----
    window.switchTab = function (mode) {
        currentInputMode = mode;
        document.getElementById("tab-text").classList.toggle("active", mode === "text");
        document.getElementById("tab-file").classList.toggle("active", mode === "file");
        document.getElementById("input-text-mode").classList.toggle("hidden", mode !== "text");
        document.getElementById("input-file-mode").classList.toggle("hidden", mode !== "file");
        updateAccountCount();
    };

    // ---- File upload ----
    accountsFile.addEventListener("change", function () {
        handleFileSelect(this.files[0]);
    });

    // Drag & drop
    fileDropArea.addEventListener("click", function () {
        accountsFile.click();
    });
    fileDropArea.addEventListener("dragover", function (e) {
        e.preventDefault();
        fileDropArea.classList.add("dragover");
    });
    fileDropArea.addEventListener("dragleave", function () {
        fileDropArea.classList.remove("dragover");
    });
    fileDropArea.addEventListener("drop", function (e) {
        e.preventDefault();
        fileDropArea.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    function handleFileSelect(file) {
        if (!file) return;
        if (!file.name.endsWith(".txt")) {
            alert("Hanya file .txt yang didukung!");
            return;
        }
        var reader = new FileReader();
        reader.onload = function (e) {
            fileContent = e.target.result;
            var n = countAccounts(fileContent);
            fileInfo.textContent = "📄 " + file.name + " — " + n + " akun terdeteksi";
            fileInfo.classList.remove("hidden");
            updateAccountCount();
        };
        reader.readAsText(file);
    }

    // ---- Proxy toggle & input ----
    window.toggleProxy = function (on) {
        useProxy = on;
        document.getElementById("proxy-off").classList.toggle("active", !on);
        document.getElementById("proxy-on").classList.toggle("active", on);
        document.getElementById("proxy-input-area").classList.toggle("hidden", !on);
        updateProxyCount();
    };

    window.switchProxyTab = function (mode) {
        proxyInputMode = mode;
        document.getElementById("proxy-tab-text").classList.toggle("active", mode === "text");
        document.getElementById("proxy-tab-file").classList.toggle("active", mode === "file");
        document.getElementById("proxy-text-mode").classList.toggle("hidden", mode !== "text");
        document.getElementById("proxy-file-mode").classList.toggle("hidden", mode !== "file");
        updateProxyCount();
    };

    function countProxies(text) {
        return text.trim().split("\n").filter(function (line) {
            line = line.trim();
            return line && !line.startsWith("#") && (line.includes(":") || line.includes("@"));
        }).length;
    }

    function updateProxyCount() {
        var text = proxyInputMode === "file" ? proxyFileContent : (proxyInput ? proxyInput.value : "");
        var n = countProxies(text);
        if (proxyCount) proxyCount.textContent = n + " proxy terdeteksi";
    }

    if (proxyInput) {
        proxyInput.addEventListener("input", updateProxyCount);
    }

    // Proxy file upload
    if (proxyFileInput) {
        proxyFileInput.addEventListener("change", function () {
            handleProxyFile(this.files[0]);
        });
    }

    var proxyDropArea = document.getElementById("proxy-drop-area");
    if (proxyDropArea) {
        proxyDropArea.addEventListener("click", function () { proxyFileInput.click(); });
        proxyDropArea.addEventListener("dragover", function (e) { e.preventDefault(); proxyDropArea.classList.add("dragover"); });
        proxyDropArea.addEventListener("dragleave", function () { proxyDropArea.classList.remove("dragover"); });
        proxyDropArea.addEventListener("drop", function (e) {
            e.preventDefault();
            proxyDropArea.classList.remove("dragover");
            if (e.dataTransfer.files.length > 0) handleProxyFile(e.dataTransfer.files[0]);
        });
    }

    function handleProxyFile(file) {
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function (e) {
            proxyFileContent = e.target.result;
            var n = countProxies(proxyFileContent);
            var info = document.getElementById("proxy-file-info");
            if (info) {
                info.textContent = "📄 " + file.name + " — " + n + " proxy terdeteksi";
                info.classList.remove("hidden");
            }
            updateProxyCount();
        };
        reader.readAsText(file);
    }

    // ---- Persistence via localStorage ----
    var STORAGE_KEY = "imap_checker_job_id";

    function saveJobId(id) {
        if (id) {
            localStorage.setItem(STORAGE_KEY, id);
        } else {
            localStorage.removeItem(STORAGE_KEY);
        }
    }

    function loadJobId() {
        return localStorage.getItem(STORAGE_KEY);
    }

    // ---- On page load: reconnect if job exists ----
    (function reconnect() {
        var savedJobId = loadJobId();
        if (!savedJobId) return;
        currentJobId = savedJobId;
        renderedLiveCount = 0;
        showProgress();
        listenProgress(savedJobId);
    })();

    // ---- Account counter ----
    function countAccounts(text) {
        var re = /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}:.+$/;
        return text.trim().split("\n").filter(function (line) { return re.test(line.trim()); }).length;
    }

    function updateAccountCount() {
        var text = currentInputMode === "file" ? fileContent : accountsInput.value;
        var n = countAccounts(text);
        accountsCount.textContent = n + " akun terdeteksi";
    }

    accountsInput.addEventListener("input", function () {
        updateAccountCount();
    });

    // ---- Form submit ----
    form.addEventListener("submit", async function (e) {
        e.preventDefault();

        var accounts = currentInputMode === "file" ? fileContent : accountsInput.value.trim();
        var senders = sendersInput.value.trim();
        var keywords = keywordsInput.value.trim();
        var days = parseInt(daysInput.value, 10) || 365;
        var threads = parseInt(threadsInput.value, 10) || 500;
        var proxies = useProxy ? (proxyInputMode === "file" ? proxyFileContent : (proxyInput ? proxyInput.value.trim() : "")) : "";

        if (!accounts || countAccounts(accounts) === 0) {
            return alert("Masukkan daftar akun atau upload file .txt!");
        }

        // Use defaults if both sender and keyword are empty
        if (!senders && !keywords) {
            senders = DEFAULT_SENDERS;
        }

        startBtn.disabled = true;
        startBtn.querySelector(".btn-text").textContent = "Memproses...";
        stopBtn.classList.remove("hidden");
        stopBtn.disabled = false;

        try {
            var res = await fetch(APP_BASE+"/api/check", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    accounts: accounts,
                    senders: senders,
                    keywords: keywords,
                    proxies: proxies,
                    search_days: days,
                    max_threads: threads,
                }),
            });

            var data = await res.json();

            if (!res.ok) {
                alert(data.error || "Terjadi kesalahan");
                resetFormButtons();
                return;
            }

            currentJobId = data.job_id;
            saveJobId(currentJobId);
            renderedLiveCount = 0;
            currentJobSenders = senders;
            currentJobKeywords = keywords;

            // Feedback dedup: show banner if duplicates were dropped during parse.
            if (data.duplicates_removed && data.duplicates_removed > 0) {
                var bannerId = "dedup-banner";
                var existing = document.getElementById(bannerId);
                if (existing) existing.remove();
                var banner = document.createElement("div");
                banner.id = bannerId;
                banner.style.cssText = "background:#fef3c7;border:1px solid #f59e0b;color:#78350f;padding:10px 14px;border-radius:6px;margin:12px 0;font-size:14px;";
                banner.textContent = "⚠️ " + data.duplicates_removed + " baris duplikat / format invalid dibuang. Akun unik yang dicek: " + data.total_accounts;
                progressSection.parentNode.insertBefore(banner, progressSection);
                setTimeout(function () { banner.remove(); }, 8000);
            }

            showProgress();
            listenProgress(currentJobId);
        } catch (err) {
            alert("Gagal menghubungi server: " + err.message);
            resetFormButtons();
        }
    });

    // ---- Stop ----
    stopBtn.addEventListener("click", async function () {
        if (!currentJobId) return;
        stopBtn.disabled = true;
        stopBtn.querySelector(".btn-text").textContent = "Menyetop...";
        try { await fetch(APP_BASE+"/api/stop/" + currentJobId, { method: "POST" }); } catch (err) { console.error(err); }
    });

    function resetFormButtons() {
        startBtn.disabled = false;
        startBtn.querySelector(".btn-text").textContent = "Mulai Pengecekan";
        stopBtn.classList.add("hidden");
    }

    function showProgress() {
        progressSection.classList.remove("hidden");
        resultsSection.classList.remove("hidden");
        progressStatus.textContent = "Sedang Berjalan";
        progressStatus.className = "status-badge running";
        progressBar.style.animation = "shimmer 2s infinite linear";
    }

    // ---- SSE ----
    function listenProgress(jobId) {
        if (evtSource) evtSource.close();
        evtSource = new EventSource(APP_BASE+"/api/status/" + jobId);

        evtSource.onmessage = function (event) {
            var d = JSON.parse(event.data);
            updateUI(d);
            if (d.status === "done" || d.status === "stopped") {
                evtSource.close();
                evtSource = null;
                onComplete(d);
            }
        };

        evtSource.onerror = function () {
            evtSource.close();
            evtSource = null;
        };
    }

    function updateUI(d) {
        var pct = d.percentage;
        progressBar.style.width = pct + "%";
        progressPct.textContent = pct + "%";
        progressDetail.textContent = d.checked + " / " + d.total + " akun";
        statLive.textContent = d.live;
        statNoemail.textContent = d.noemail;
        statDie.textContent = d.die;
        statUnreg.textContent = d.unreg;
        statSkipped.textContent = d.skipped;

        if (d.status === "running") {
            stopBtn.classList.remove("hidden");
            stopBtn.disabled = false;
            startBtn.disabled = true;
            startBtn.querySelector(".btn-text").textContent = "Sedang Berjalan...";
        }

        enableDownloadLink("live", d.live > 0);
        enableDownloadLink("noemail", d.noemail > 0);
        enableDownloadLink("die", d.die > 0);
        enableDownloadLink("unreg", d.unreg > 0);
        enableDownloadLink("domain_skipped", d.skipped > 0);

        if (d.live_accounts && d.live_accounts.length > 0) {
            liveSection.classList.remove("hidden");
            liveCounter.textContent = d.live_accounts.length;

            if (renderedLiveCount === 0 && d.live_accounts.length > 0) {
                liveList.innerHTML = "";
                for (var i = 0; i < d.live_accounts.length; i++) {
                    appendLiveItem(d.live_accounts[i]);
                }
                renderedLiveCount = d.live_accounts.length;
            } else if (d.live_accounts.length > renderedLiveCount) {
                for (var j = renderedLiveCount; j < d.live_accounts.length; j++) {
                    appendLiveItem(d.live_accounts[j]);
                }
                renderedLiveCount = d.live_accounts.length;
            }
        }
    }

    function appendLiveItem(item) {
        var div = document.createElement("div");
        div.className = "live-item";
        var credential = item.account + ":" + (item.password || "");
        var emailsHtml = "";
        if (item.emails && item.emails.length > 0) {
            emailsHtml = '<div class="live-item-emails">';
            item.emails.forEach(function (em) {
                emailsHtml += '<div class="live-item-email">' +
                    '<span class="date">' + escapeHtml(em.date || '') + '</span>' +
                    '<span class="from">' + escapeHtml(em.from) + '</span>' +
                    '<span class="subject">' + escapeHtml(em.subject) + '</span>' +
                    '</div>';
            });
            emailsHtml += '</div>';
        }
        div.innerHTML =
            '<div class="live-item-header">' +
            '<span class="live-item-dot"></span>' +
            '<span class="live-item-account">' + escapeHtml(credential) + '</span>' +
            '<span class="live-item-count">' + item.email_count + ' email</span>' +
            '<div class="live-item-actions">' +
            '<button class="live-action-btn live-get-btn" data-email="' + escapeHtml(item.account) + '" data-pass="' + escapeHtml(item.password || '') + '">📨</button>' +
            '<button class="live-action-btn live-del-btn" data-email="' + escapeHtml(item.account) + '" data-pass="' + escapeHtml(item.password || '') + '">🗑️</button>' +
            '</div>' +
            '</div>' + emailsHtml;
        liveList.prepend(div);

        // Attach handlers
        div.querySelector(".live-get-btn").addEventListener("click", function () {
            handleLiveGetEmail(this.dataset.email, this.dataset.pass, this);
        });
        div.querySelector(".live-del-btn").addEventListener("click", function () {
            handleLiveDeleteEmail(this.dataset.email, this.dataset.pass, this);
        });
    }

    // ---- Sender Picker Modal ----
    function showSenderPicker(emailAddr, password, btn) {
        var old = document.getElementById("sender-picker-modal");
        if (old) old.remove();

        var hasJobSenders = !!(currentJobSenders || currentJobKeywords);

        var modal = document.createElement("div");
        modal.id = "sender-picker-modal";
        modal.className = "ge-modal";

        var jobSenderPreview = "";
        if (currentJobSenders) {
            jobSenderPreview = currentJobSenders.split("\n").filter(function(s){return s.trim();}).slice(0, 3).join(", ");
            if (currentJobSenders.split("\n").filter(function(s){return s.trim();}).length > 3) jobSenderPreview += "...";
        }
        if (currentJobKeywords) {
            var kwPreview = currentJobKeywords.split("\n").filter(function(s){return s.trim();}).slice(0, 2).join(", ");
            jobSenderPreview += (jobSenderPreview ? " + keyword: " : "keyword: ") + kwPreview;
        }

        var defaultPreview = DEFAULT_SENDERS.split("\n").filter(function(s){return s.trim();}).slice(0, 3).join(", ") + "...";

        modal.innerHTML =
            '<div class="ge-modal-backdrop"></div>' +
            '<div class="ge-modal-container" style="max-width:520px;">' +
            '<div class="ge-modal-header">' +
            '<div class="ge-modal-title">' +
            '<span class="ge-modal-subject">📨 Get Email — ' + escapeHtml(emailAddr) + '</span>' +
            '<span class="ge-modal-info">Pilih sender untuk mengambil email</span>' +
            '</div>' +
            '<button class="ge-modal-close">&times;</button>' +
            '</div>' +
            '<div class="ge-modal-body" style="padding:20px;">' +
            '<div class="sender-picker-options">' +

            // Option 1: Same as check IMAP
            (hasJobSenders ?
            '<div class="sender-picker-opt" data-choice="job">' +
            '<div class="sender-picker-radio"><span class="radio-dot"></span></div>' +
            '<div class="sender-picker-content">' +
            '<div class="sender-picker-label">📋 Sama dengan Check IMAP</div>' +
            '<div class="sender-picker-desc">' + escapeHtml(jobSenderPreview) + '</div>' +
            '</div></div>' : '') +

            // Option 2: Default
            '<div class="sender-picker-opt" data-choice="default">' +
            '<div class="sender-picker-radio"><span class="radio-dot"></span></div>' +
            '<div class="sender-picker-content">' +
            '<div class="sender-picker-label">⭐ Default Sender</div>' +
            '<div class="sender-picker-desc">' + escapeHtml(defaultPreview) + '</div>' +
            '</div></div>' +

            // Option 3: Custom
            '<div class="sender-picker-opt" data-choice="custom">' +
            '<div class="sender-picker-radio"><span class="radio-dot"></span></div>' +
            '<div class="sender-picker-content">' +
            '<div class="sender-picker-label">✏️ Custom</div>' +
            '<div class="sender-picker-desc">Masukkan sender/keyword sendiri</div>' +
            '</div></div>' +

            // Custom input area (hidden by default)
            '<div id="sender-picker-custom-area" class="sender-picker-custom-area" style="display:none;">' +
            '<label style="font-size:13px;color:#94a3b8;display:block;margin-bottom:6px;">Sender (satu per baris)</label>' +
            '<textarea id="sender-picker-senders" rows="3" placeholder="noreply@booking.com&#10;@agoda.com" style="width:100%;padding:10px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;font-size:13px;resize:vertical;"></textarea>' +
            '<label style="font-size:13px;color:#94a3b8;display:block;margin:10px 0 6px;">Keyword / Subject (opsional)</label>' +
            '<textarea id="sender-picker-keywords" rows="2" placeholder="booking confirmation" style="width:100%;padding:10px;border-radius:8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;font-size:13px;resize:vertical;"></textarea>' +
            '</div>' +

            '</div>' +
            '<div style="margin-top:18px;text-align:right;">' +
            '<button id="sender-picker-cancel" class="btn-stop" style="margin-right:10px;padding:8px 20px;font-size:14px;">Batal</button>' +
            '<button id="sender-picker-go" class="btn-primary" style="padding:8px 20px;font-size:14px;" disabled>' +
            '<span class="btn-icon">📨</span><span class="btn-text">Ambil Email</span>' +
            '</button>' +
            '</div>' +
            '</div>' +
            '</div>';

        document.body.appendChild(modal);
        document.body.style.overflow = "hidden";

        var selectedChoice = null;
        var goBtn = modal.querySelector("#sender-picker-go");
        var customArea = modal.querySelector("#sender-picker-custom-area");

        // Option click handlers
        modal.querySelectorAll(".sender-picker-opt").forEach(function(opt) {
            opt.addEventListener("click", function() {
                modal.querySelectorAll(".sender-picker-opt").forEach(function(o) { o.classList.remove("selected"); });
                this.classList.add("selected");
                selectedChoice = this.dataset.choice;
                goBtn.disabled = false;

                if (selectedChoice === "custom") {
                    customArea.style.display = "block";
                } else {
                    customArea.style.display = "none";
                }
            });
        });

        // Close handlers
        function closePicker() {
            modal.remove();
            document.body.style.overflow = "";
        }
        modal.querySelector(".ge-modal-close").addEventListener("click", closePicker);
        modal.querySelector(".ge-modal-backdrop").addEventListener("click", closePicker);
        modal.querySelector("#sender-picker-cancel").addEventListener("click", closePicker);

        // Go button
        goBtn.addEventListener("click", function() {
            var senders = "";
            var keywords = "";

            if (selectedChoice === "job") {
                senders = currentJobSenders;
                keywords = currentJobKeywords;
            } else if (selectedChoice === "default") {
                senders = DEFAULT_SENDERS;
            } else if (selectedChoice === "custom") {
                senders = modal.querySelector("#sender-picker-senders").value.trim();
                keywords = modal.querySelector("#sender-picker-keywords").value.trim();
                if (!senders && !keywords) {
                    alert("Isi minimal salah satu: sender atau keyword!");
                    return;
                }
            }

            closePicker();
            doLiveGetEmail(emailAddr, password, senders, keywords, btn);
        });
    }

    // ---- Live Get Email (with sender picker) ----
    async function handleLiveGetEmail(emailAddr, password, btn) {
        showSenderPicker(emailAddr, password, btn);
    }

    async function doLiveGetEmail(emailAddr, password, senders, keywords, btn) {
        btn.disabled = true;
        btn.textContent = "⏳";

        try {
            var res = await fetch(APP_BASE+"/api/get-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: emailAddr,
                    password: password,
                    senders: senders,
                    keywords: keywords,
                    search_days: 365,
                }),
            });
            var data = await res.json();
            btn.disabled = false;
            btn.textContent = "📨";

            if (!data.success) {
                alert("❌ " + (data.error || "Gagal mengambil email"));
                return;
            }

            if (!data.emails || data.emails.length === 0) {
                alert("📭 Tidak ada email ditemukan.");
                return;
            }

            showLiveModal(emailAddr, data.emails);
        } catch (err) {
            btn.disabled = false;
            btn.textContent = "📨";
            alert("❌ Error: " + err.message);
        }
    }

    // ---- Live Delete Email ----
    async function handleLiveDeleteEmail(emailAddr, password, btn) {
        if (!confirm("⚠️ Hapus SEMUA email dari sender default di akun " + emailAddr + "?\n\nEmail akan dihapus permanen.")) return;

        btn.disabled = true;
        btn.textContent = "⏳";

        var senders = DEFAULT_SENDERS;

        try {
            // First fetch the emails to get UIDs
            var res = await fetch(APP_BASE+"/api/get-email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    email: emailAddr,
                    password: password,
                    senders: senders,
                    keywords: "",
                    search_days: 365,
                }),
            });
            var data = await res.json();

            if (!data.success || !data.emails || data.emails.length === 0) {
                btn.disabled = false;
                btn.textContent = "🗑️";
                alert(data.success ? "📭 Tidak ada email untuk dihapus." : "❌ " + (data.error || "Gagal"));
                return;
            }

            // Delete each email
            var deleted = 0;
            for (var i = 0; i < data.emails.length; i++) {
                try {
                    var delRes = await fetch(APP_BASE+"/api/delete-email", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            email: emailAddr,
                            password: password,
                            uid: data.emails[i].uid,
                        }),
                    });
                    var delData = await delRes.json();
                    if (delData.success) deleted++;
                } catch (e) { /* skip */ }
            }

            btn.disabled = false;
            btn.textContent = "🗑️";
            alert("✅ " + deleted + " / " + data.emails.length + " email berhasil dihapus dari " + emailAddr);
        } catch (err) {
            btn.disabled = false;
            btn.textContent = "🗑️";
            alert("❌ Error: " + err.message);
        }
    }

    // ---- Live Email Modal ----
    function showLiveModal(emailAddr, emails) {
        // Remove existing modal if any
        var old = document.getElementById("live-email-modal");
        if (old) old.remove();

        var modal = document.createElement("div");
        modal.id = "live-email-modal";
        modal.className = "ge-modal";

        var bodyHtml = '';
        emails.forEach(function (em, idx) {
            bodyHtml += '<div class="live-modal-email">' +
                '<div class="live-modal-email-header" data-toggle="' + idx + '">' +
                '<span class="live-modal-num">#' + (idx + 1) + '</span>' +
                '<div class="live-modal-meta">' +
                '<div class="live-modal-subject">' + escapeHtml(em.subject || "(Tanpa subjek)") + '</div>' +
                '<div class="live-modal-info">📧 ' + escapeHtml(em.from) + ' &nbsp;•&nbsp; 📅 ' + escapeHtml(em.date) + '</div>' +
                '</div>' +
                '<span class="live-modal-chevron">▼</span>' +
                '</div>' +
                '<div class="live-modal-body" id="live-modal-body-' + idx + '" style="display:none;">' + (em.body_html || '') + '</div>' +
                '</div>';
        });

        modal.innerHTML =
            '<div class="ge-modal-backdrop"></div>' +
            '<div class="ge-modal-container" style="max-width:900px;">' +
            '<div class="ge-modal-header">' +
            '<div class="ge-modal-title">' +
            '<span class="ge-modal-subject">📬 Email dari ' + escapeHtml(emailAddr) + '</span>' +
            '<span class="ge-modal-info">' + emails.length + ' email ditemukan</span>' +
            '</div>' +
            '<button class="ge-modal-close">&times;</button>' +
            '</div>' +
            '<div class="ge-modal-body">' + bodyHtml + '</div>' +
            '</div>';

        document.body.appendChild(modal);
        document.body.style.overflow = "hidden";

        // Toggle handlers for each email card
        modal.querySelectorAll(".live-modal-email-header[data-toggle]").forEach(function (hdr) {
            hdr.style.cursor = "pointer";
            hdr.addEventListener("click", function () {
                var idx = this.getAttribute("data-toggle");
                var body = document.getElementById("live-modal-body-" + idx);
                var chevron = this.querySelector(".live-modal-chevron");
                if (body.style.display === "none") {
                    body.style.display = "block";
                    chevron.textContent = "▲";
                    this.closest(".live-modal-email").classList.add("expanded");
                } else {
                    body.style.display = "none";
                    chevron.textContent = "▼";
                    this.closest(".live-modal-email").classList.remove("expanded");
                }
            });
        });

        // Close handlers
        modal.querySelector(".ge-modal-close").addEventListener("click", function () {
            modal.remove();
            document.body.style.overflow = "";
        });
        modal.querySelector(".ge-modal-backdrop").addEventListener("click", function () {
            modal.remove();
            document.body.style.overflow = "";
        });
    }

    function escapeHtml(str) {
        var div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function onComplete(d) {
        progressStatus.textContent = d.status === "stopped" ? "Dihentikan" : "Selesai";
        progressStatus.className = "status-badge " + (d.status === "stopped" ? "stopped" : "done");
        progressBar.style.animation = "none";
        resetFormButtons();
        saveJobId(null);
        enableDownloadLink("live", d.live > 0);
        enableDownloadLink("noemail", d.noemail > 0);
        enableDownloadLink("die", d.die > 0);
        enableDownloadLink("unreg", d.unreg > 0);
        enableDownloadLink("domain_skipped", d.skipped > 0);
    }

    function enableDownloadLink(type, enable) {
        var btn = document.querySelector('.download-btn[data-type="' + type + '"]');
        if (!btn || !currentJobId) return;
        if (enable) {
            btn.classList.remove("disabled");
            btn.href = APP_BASE + "/api/download/" + currentJobId + "/" + type;
            btn.setAttribute("download", type + "_" + currentJobId + ".txt");
        } else {
            btn.classList.add("disabled");
            btn.removeAttribute("href");
            btn.removeAttribute("download");
        }
    }

    document.querySelectorAll(".download-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            if (this.classList.contains("disabled")) e.preventDefault();
        });
    });
})();

