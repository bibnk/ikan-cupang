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
            var showLimit = 3;
            emailsHtml = '<div class="live-item-emails">';
            item.emails.forEach(function (em, idx) {
                var hiddenClass = idx >= showLimit ? ' style="display:none;" data-extra="1"' : '';
                emailsHtml += '<div class="live-item-email"' + hiddenClass + '>' +
                    '<span class="date">' + escapeHtml(em.date || '') + '</span>' +
                    '<span class="from">' + escapeHtml(em.from) + '</span>' +
                    '<span class="subject">' + escapeHtml(em.subject) + '</span>' +
                    '</div>';
            });
            if (item.emails.length > showLimit) {
                emailsHtml += '<button class="live-show-more-btn" data-expanded="0">▼ Show More (' + (item.emails.length - showLimit) + ' lainnya)</button>';
            }
            emailsHtml += '</div>';
        }
        div.innerHTML =
            '<div class="live-item-header">' +
            '<span class="live-item-dot"></span>' +
            '<span class="live-item-account">' + escapeHtml(credential) + '</span>' +
            '<span class="live-item-count">' + item.email_count + ' email</span>' +
            '<div class="live-item-actions">' +
            '<button class="live-action-btn live-get-btn" data-email="' + escapeHtml(item.account) + '" data-pass="' + escapeHtml(item.password || '') + '">📨</button>' +
            '<button class="live-action-btn live-browse-btn" data-email="' + escapeHtml(item.account) + '" data-pass="' + escapeHtml(item.password || '') + '">📂</button>' +
            '<button class="live-action-btn live-del-btn" data-email="' + escapeHtml(item.account) + '" data-pass="' + escapeHtml(item.password || '') + '">🗑️</button>' +
            '</div>' +
            '</div>' + emailsHtml;
        liveList.prepend(div);

        // Attach handlers
        div.querySelector(".live-get-btn").addEventListener("click", function () {
            handleLiveGetEmail(this.dataset.email, this.dataset.pass, this);
        });
        div.querySelector(".live-browse-btn").addEventListener("click", function () {
            handleBrowseEmail(this.dataset.email, this.dataset.pass, this);
        });
        div.querySelector(".live-del-btn").addEventListener("click", function () {
            handleLiveDeleteEmail(this.dataset.email, this.dataset.pass, this);
        });
        var showMoreBtn = div.querySelector(".live-show-more-btn");
        if (showMoreBtn) {
            showMoreBtn.addEventListener("click", function () {
                var expanded = this.getAttribute("data-expanded") === "1";
                var extras = this.parentNode.querySelectorAll('[data-extra="1"]');
                extras.forEach(function (el) { el.style.display = expanded ? "none" : ""; });
                this.setAttribute("data-expanded", expanded ? "0" : "1");
                this.textContent = expanded
                    ? "▼ Show More (" + extras.length + " lainnya)"
                    : "▲ Show Less";
            });
        }
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
    // ---- Email Browser Feature ----
    async function handleBrowseEmail(emailAddr, password, btn) {
        var oldText = btn.textContent;
        btn.disabled = true;
        btn.textContent = "⏳";
        
        try {
            var res = await fetch(APP_BASE+"/api/email-folders", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email: emailAddr, password: password })
            });
            var data = await res.json();
            btn.disabled = false;
            btn.textContent = oldText;
            
            if (!data.success) {
                alert("❌ " + (data.error || "Gagal mendapatkan folder"));
                return;
            }
            
            showEmailBrowser(emailAddr, password, data.folders);
        } catch (err) {
            btn.disabled = false;
            btn.textContent = oldText;
            alert("❌ Error: " + err.message);
        }
    }

    function getFolderIcon(name) {
        var n = name.toLowerCase();
        if (n === 'inbox') return '📥';
        if (n.indexOf('sent') !== -1) return '📤';
        if (n.indexOf('draft') !== -1) return '📝';
        if (n.indexOf('trash') !== -1 || n.indexOf('deleted') !== -1 || n.indexOf('bin') !== -1) return '🗑️';
        if (n.indexOf('spam') !== -1 || n.indexOf('junk') !== -1) return '⚠️';
        if (n.indexOf('archive') !== -1) return '📦';
        if (n.indexOf('starred') !== -1 || n.indexOf('flagged') !== -1) return '⭐';
        if (n.indexOf('important') !== -1) return '❗';
        if (n.indexOf('all') !== -1) return '📬';
        return '📁';
    }

    function showEmailBrowser(emailAddr, password, folders) {
        var old = document.getElementById("email-browser-modal");
        if (old) old.remove();

        var modal = document.createElement("div");
        modal.id = "email-browser-modal";
        modal.className = "ge-modal";

        var folderHtml = '';
        folders.forEach(function(f, idx) {
            var activeClass = idx === 0 ? " active" : "";
            var icon = getFolderIcon(f.name);
            folderHtml += '<div class="eb-folder-item' + activeClass + '" data-folder="' + escapeHtml(f.name) + '">' +
                '<span>' + icon + ' ' + escapeHtml(f.name) + '</span>' +
                '<span class="eb-folder-count">(' + (f.count || 0) + ')</span>' +
                '</div>';
        });

        modal.innerHTML =
            '<div class="ge-modal-backdrop"></div>' +
            '<div class="ge-modal-container" style="max-width:1100px; display:flex; flex-direction:column; height:85vh;">' +
            '<div class="ge-modal-header">' +
            '<div class="ge-modal-title">' +
            '<span class="ge-modal-subject">📂 Email Browser — ' + escapeHtml(emailAddr) + '</span>' +
            '</div>' +
            '<button class="ge-modal-close">&times;</button>' +
            '</div>' +
            '<div class="ge-modal-body" style="flex:1; padding:0; display:flex; overflow:hidden;">' +
            '<div class="eb-sidebar">' + folderHtml + '</div>' +
            '<div class="eb-main" style="display:flex; flex-direction:column;">' +
            '<div class="eb-toolbar">' +
            '<button class="btn-primary eb-del-btn" disabled>🗑️ Hapus Terpilih (0)</button>' +
            '<span class="eb-status-text">Memuat...</span>' +
            '</div>' +
            '<div class="eb-email-list-container" style="flex:1; overflow-y:auto; border-bottom:1px solid var(--border-color);">' +
            '<div class="eb-email-list"></div>' +
            '</div>' +
            '<div class="eb-viewer" style="flex:1; overflow-y:auto; display:none; padding:15px;"></div>' +
            '<div class="eb-pagination" style="padding:10px; border-top:1px solid var(--border-color); display:flex; justify-content:center; gap:15px; align-items:center;">' +
            '<button class="eb-prev-btn" disabled>◄ Prev</button>' +
            '<span class="eb-page-text">Page 1 / 1</span>' +
            '<button class="eb-next-btn" disabled>Next ►</button>' +
            '</div>' +
            '</div>' +
            '</div>' +
            '</div>';

        document.body.appendChild(modal);
        document.body.style.overflow = "hidden";

        var currentFolder = folders.length > 0 ? folders[0].name : "INBOX";
        var currentPage = 1;

        var sidebar = modal.querySelector(".eb-sidebar");
        var listContainer = modal.querySelector(".eb-email-list");
        var viewerContainer = modal.querySelector(".eb-viewer");
        var delBtn = modal.querySelector(".eb-del-btn");
        var statusText = modal.querySelector(".eb-status-text");
        var prevBtn = modal.querySelector(".eb-prev-btn");
        var nextBtn = modal.querySelector(".eb-next-btn");
        var pageText = modal.querySelector(".eb-page-text");

        async function loadFolder(folder, page) {
            statusText.textContent = "⏳ Memuat email...";
            listContainer.innerHTML = "";
            viewerContainer.style.display = "none";
            viewerContainer.innerHTML = "";
            delBtn.disabled = true;
            delBtn.innerHTML = '🗑️ Hapus Terpilih (0)';
            
            try {
                var res = await fetch(APP_BASE+"/api/email-list", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: emailAddr, password: password, folder: folder, page: page })
                });
                var d = await res.json();
                
                if (!d.success) {
                    statusText.textContent = "❌ " + (d.error || "Gagal memuat email");
                    return;
                }
                
                statusText.textContent = "✅ " + d.total + " email di " + folder;
                currentPage = d.page;
                
                prevBtn.disabled = currentPage <= 1;
                nextBtn.disabled = currentPage >= d.pages;
                pageText.textContent = 'Page ' + currentPage + ' / ' + Math.max(1, d.pages);

                if (d.emails.length === 0) {
                    listContainer.innerHTML = '<div style="padding:15px;text-align:center;color:var(--text-muted);">Tidak ada email di folder ini.</div>';
                    return;
                }
                
                var html = '';
                d.emails.forEach(function(em) {
                    var fwClass = em.seen ? 'normal' : 'bold';
                    html += '<div class="eb-email-row" data-uid="' + em.uid + '">' +
                        '<div class="eb-row-checkbox"><input type="checkbox" class="eb-checkbox" data-uid="' + em.uid + '"></div>' +
                        '<div class="eb-row-content" style="font-weight:' + fwClass + ';">' +
                        '<div class="eb-row-from">' + escapeHtml(em.from_name || em.from) + '</div>' +
                        '<div class="eb-row-subject">' + escapeHtml(em.subject || "(Tanpa subjek)") + '</div>' +
                        '<div class="eb-row-date">' + escapeHtml(em.date || '') + '</div>' +
                        '</div>' +
                        '</div>';
                });
                listContainer.innerHTML = html;
                
                // attach listeners
                listContainer.querySelectorAll(".eb-checkbox").forEach(function(cb) {
                    cb.addEventListener("change", function(e) {
                        e.stopPropagation();
                        var checked = listContainer.querySelectorAll(".eb-checkbox:checked").length;
                        delBtn.disabled = checked === 0;
                        delBtn.innerHTML = '🗑️ Hapus Terpilih (' + checked + ')';
                    });
                });
                
                listContainer.querySelectorAll(".eb-row-content").forEach(function(row) {
                    row.addEventListener("click", function() {
                        var p = this.closest(".eb-email-row");
                        var uid = p.getAttribute("data-uid");
                        loadEmail(folder, uid);
                        // mark as read visually
                        this.style.fontWeight = 'normal';
                    });
                });
                
            } catch (e) {
                statusText.textContent = "❌ Error: " + e.message;
            }
        }
        
        async function loadEmail(folder, uid) {
            viewerContainer.innerHTML = "<div style='text-align:center;padding:20px;'>⏳ Memuat pesan...</div>";
            viewerContainer.style.display = "block";
            
            try {
                var res = await fetch(APP_BASE+"/api/email-view", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: emailAddr, password: password, folder: folder, uid: uid })
                });
                var d = await res.json();
                
                if (!d.success) {
                    viewerContainer.innerHTML = "<div style='color:var(--color-die);padding:20px;'>❌ " + escapeHtml(d.error || "Gagal memuat pesan") + "</div>";
                    return;
                }
                
                var em = d.email;
                var attHtml = '';
                if (em.attachments && em.attachments.length > 0) {
                    attHtml = '<div style="margin-top:10px;padding:10px;background:var(--bg-input);border-radius:var(--radius-sm);">' +
                        '<strong>📎 Attachments:</strong><br>' + 
                        em.attachments.map(function(a){ return escapeHtml(a); }).join('<br>') +
                        '</div>';
                }
                
                viewerContainer.innerHTML = '<div class="eb-viewer-header" style="margin-bottom:15px;padding-bottom:10px;border-bottom:1px solid var(--border-color);">' +
                    '<h3 style="margin:0 0 5px 0;">' + escapeHtml(em.subject || "(Tanpa subjek)") + '</h3>' +
                    '<div style="font-size:13px;color:var(--text-muted);">' +
                    '<div><strong>Dari:</strong> ' + escapeHtml(em.from_name ? (em.from_name + " <" + em.from + ">") : em.from) + '</div>' +
                    '<div><strong>Ke:</strong> ' + escapeHtml(em.to || '') + '</div>' +
                    '<div><strong>Tanggal:</strong> ' + escapeHtml(em.date || '') + '</div>' +
                    '</div>' + attHtml +
                    '</div>' +
                    '<div class="eb-viewer-body" style="background:#fff;color:#000;padding:15px;border-radius:4px;overflow:auto;">' + (em.body_html || '') + '</div>';
                    
            } catch (e) {
                viewerContainer.innerHTML = "<div style='color:var(--color-die);padding:20px;'>❌ Error: " + escapeHtml(e.message) + "</div>";
            }
        }
        
        sidebar.querySelectorAll(".eb-folder-item").forEach(function(el) {
            el.addEventListener("click", function() {
                sidebar.querySelectorAll(".eb-folder-item").forEach(function(i){ i.classList.remove("active"); });
                this.classList.add("active");
                currentFolder = this.getAttribute("data-folder");
                loadFolder(currentFolder, 1);
            });
        });
        
        prevBtn.addEventListener("click", function() {
            if (currentPage > 1) loadFolder(currentFolder, currentPage - 1);
        });
        
        nextBtn.addEventListener("click", function() {
            loadFolder(currentFolder, currentPage + 1);
        });
        
        delBtn.addEventListener("click", async function() {
            var selectedUids = [];
            listContainer.querySelectorAll(".eb-checkbox:checked").forEach(function (cb) {
                selectedUids.push(cb.getAttribute("data-uid"));
            });

            if (selectedUids.length === 0) return;
            if (!confirm("❓ Hapus " + selectedUids.length + " email yang dipilih?")) return;

            delBtn.disabled = true;
            delBtn.innerHTML = '⏳ Menghapus...';

            var deleted = 0;
            for (var i = 0; i < selectedUids.length; i++) {
                try {
                    var res = await fetch(APP_BASE+"/api/delete-email", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            email: emailAddr,
                            password: password,
                            uid: selectedUids[i],
                            folder: currentFolder
                        }),
                    });
                    var d = await res.json();
                    if (d.success) deleted++;
                } catch (e) { /* skip */ }
            }

            alert("✅ " + deleted + " / " + selectedUids.length + " email berhasil dihapus.");
            loadFolder(currentFolder, currentPage);
        });

        modal.querySelector(".ge-modal-close").addEventListener("click", function () {
            modal.remove();
            document.body.style.overflow = "";
        });
        modal.querySelector(".ge-modal-backdrop").addEventListener("click", function () {
            modal.remove();
            document.body.style.overflow = "";
        });

        // Load initial
        if (folders.length > 0) {
            loadFolder(currentFolder, 1);
        }
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

            showLiveModal(emailAddr, password, data.emails);
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
    function showLiveModal(emailAddr, password, emails) {
        // Remove existing modal if any
        var old = document.getElementById("live-email-modal");
        if (old) old.remove();

        var modal = document.createElement("div");
        modal.id = "live-email-modal";
        modal.className = "ge-modal";

        var bodyHtml = '';
        emails.forEach(function (em, idx) {
            bodyHtml += '<div class="live-modal-email" id="live-modal-card-' + em.uid + '">' +
                '<div class="live-modal-email-header">' +
                '<input type="checkbox" class="live-modal-checkbox" data-uid="' + em.uid + '">' +
                '<span class="live-modal-num">#' + (idx + 1) + '</span>' +
                '<div class="live-modal-meta" data-toggle="' + idx + '">' +
                '<div class="live-modal-subject">' + escapeHtml(em.subject || "(Tanpa subjek)") + '</div>' +
                '<div class="live-modal-info">📧 ' + escapeHtml(em.from) + ' &nbsp;•&nbsp; 📅 ' + escapeHtml(em.date) + '</div>' +
                '</div>' +
                '<span class="live-modal-chevron" data-toggle="' + idx + '">▼</span>' +
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
            '<span class="ge-modal-info live-modal-total-count">' + emails.length + ' email ditemukan</span>' +
            '</div>' +
            '<div class="live-modal-toolbar">' +
            '<button class="btn-primary live-modal-del-btn" disabled>🗑️ Hapus Terpilih (0)</button>' +
            '</div>' +
            '<button class="ge-modal-close">&times;</button>' +
            '</div>' +
            '<div class="ge-modal-body">' + bodyHtml + '</div>' +
            '</div>';

        document.body.appendChild(modal);
        document.body.style.overflow = "hidden";

        // Handle Checkboxes
        var checkboxes = modal.querySelectorAll(".live-modal-checkbox");
        var delBtn = modal.querySelector(".live-modal-del-btn");
        var totalCountSpan = modal.querySelector(".live-modal-total-count");

        function updateToolbar() {
            var checked = modal.querySelectorAll(".live-modal-checkbox:checked").length;
            delBtn.disabled = checked === 0;
            delBtn.innerHTML = '🗑️ Hapus Terpilih (' + checked + ')';
        }

        checkboxes.forEach(function (cb) {
            cb.addEventListener("change", updateToolbar);
        });

        // Delete Logic
        delBtn.addEventListener("click", async function () {
            var selectedUids = [];
            modal.querySelectorAll(".live-modal-checkbox:checked").forEach(function (cb) {
                selectedUids.push(cb.getAttribute("data-uid"));
            });

            if (selectedUids.length === 0) return;
            if (!confirm("❓ Hapus " + selectedUids.length + " email yang dipilih?")) return;

            var oldText = delBtn.innerHTML;
            delBtn.disabled = true;
            delBtn.innerHTML = '⏳ Menghapus...';

            var deleted = 0;
            for (var i = 0; i < selectedUids.length; i++) {
                try {
                    var res = await fetch(APP_BASE+"/api/delete-email", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            email: emailAddr,
                            password: password,
                            uid: selectedUids[i],
                        }),
                    });
                    var d = await res.json();
                    if (d.success) {
                        deleted++;
                        var card = document.getElementById("live-modal-card-" + selectedUids[i]);
                        if (card) card.remove();
                    }
                } catch (e) { /* skip */ }
            }

            alert("✅ " + deleted + " / " + selectedUids.length + " email berhasil dihapus.");
            
            var remaining = modal.querySelectorAll(".live-modal-email").length;
            totalCountSpan.textContent = remaining + ' email ditemukan';
            updateToolbar();
        });

        // Toggle handlers for each email card
        modal.querySelectorAll(".live-modal-meta[data-toggle], .live-modal-chevron[data-toggle]").forEach(function (el) {
            el.style.cursor = "pointer";
            el.addEventListener("click", function (e) {
                if (e.target.classList.contains("live-modal-checkbox")) return;
                var idx = this.getAttribute("data-toggle");
                var body = document.getElementById("live-modal-body-" + idx);
                var header = this.closest(".live-modal-email-header");
                var chevron = header.querySelector(".live-modal-chevron");
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

