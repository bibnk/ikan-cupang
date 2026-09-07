/* resolver.js — Resolver progress page
 * Fetches /api/resolver, draws a per-job line chart (unreg vs resolved over
 * time) with Chart.js, and lists the domains resolved during the check.
 * Auto-refreshes while any job is still running.
 */
(function () {
    var APP_BASE = (window.APP_BASE || "");
    var charts = {};       // job_id -> Chart instance
    var refreshTimer = null;

    function el(id) { return document.getElementById(id); }

    function fmtTime(iso) {
        if (!iso) return "-";
        try {
            var d = new Date(iso);
            return d.toLocaleString("id-ID", { hour12: false });
        } catch (e) { return iso; }
    }

    function statusBadge(status) {
        var map = {
            running: ["#3b82f6", "Running"],
            done: ["#22c55e", "Done"],
            stopped: ["#f59e0b", "Stopped"],
            idle: ["#64748b", "Idle"]
        };
        var c = map[status] || ["#64748b", status || "?"];
        return '<span class="rs-badge" style="background:' + c[0] + '22;color:' + c[0] +
            ';border:1px solid ' + c[0] + '55">' + c[1] + '</span>';
    }

    function buildChart(job) {
        var cid = "chart-" + job.job_id;
        var canvas = document.getElementById(cid);
        if (!canvas) return;
        var hist = job.resolve_history || [];

        // Build labels as elapsed seconds from first sample
        var labels = [];
        var unreg = [];
        var resolved = [];
        var checkedPct = [];
        if (hist.length > 0) {
            var t0 = hist[0].t;
            for (var i = 0; i < hist.length; i++) {
                var h = hist[i];
                labels.push(Math.max(0, Math.round(h.t - t0)) + "s");
                unreg.push(h.unreg);
                resolved.push(h.resolved);
                checkedPct.push(job.total > 0 ? Math.round(h.checked / job.total * 100) : 0);
            }
        }

        if (charts[job.job_id]) {
            var ch = charts[job.job_id];
            ch.data.labels = labels;
            ch.data.datasets[0].data = unreg;
            ch.data.datasets[1].data = resolved;
            ch.data.datasets[2].data = checkedPct;
            ch.update("none");
            return;
        }

        charts[job.job_id] = new Chart(canvas.getContext("2d"), {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Unreg",
                        data: unreg,
                        borderColor: "#f59e0b",
                        backgroundColor: "rgba(245,158,11,0.12)",
                        tension: 0.3, fill: true, pointRadius: 0, borderWidth: 2
                    },
                    {
                        label: "Resolved",
                        data: resolved,
                        borderColor: "#06b6d4",
                        backgroundColor: "rgba(6,182,212,0.12)",
                        tension: 0.3, fill: true, pointRadius: 0, borderWidth: 2
                    },
                    {
                        label: "Checked %",
                        data: checkedPct,
                        borderColor: "#64748b",
                        borderDash: [5, 4],
                        tension: 0.2, fill: false, pointRadius: 0, borderWidth: 1,
                        yAxisID: "y1"
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { labels: { color: "#cbd5e1", boxWidth: 12, font: { size: 11 } } }
                },
                scales: {
                    x: {
                        ticks: { color: "#64748b", maxTicksLimit: 8, font: { size: 10 } },
                        grid: { color: "rgba(100,116,139,0.12)" }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: "#94a3b8", precision: 0, font: { size: 10 } },
                        grid: { color: "rgba(100,116,139,0.12)" }
                    },
                    y1: {
                        position: "right", beginAtZero: true, max: 100,
                        ticks: { color: "#64748b", precision: 0, font: { size: 10 } },
                        grid: { drawOnChartArea: false }
                    }
                }
            }
        });
    }

    function resolvedTable(job) {
        var map = job.resolved_map || {};
        var domains = Object.keys(map);
        if (domains.length === 0) return '<p class="rs-none">Belum ada domain ter-resolve.</p>';
        domains.sort();
        var rows = "";
        for (var i = 0; i < domains.length; i++) {
            var d = domains[i];
            var c = map[d];
            rows += '<tr><td class="rs-domain">' + d + '</td>' +
                '<td>' + (c.server || "-") + '</td>' +
                '<td>' + (c.port || "-") + '</td>' +
                '<td><span class="rs-via rs-via-' + (c.via || "mx") + '">' + (c.via || "mx") + '</span></td></tr>';
        }
        return '<div class="rs-table-wrap"><table class="rs-table">' +
            '<thead><tr><th>Domain</th><th>Server</th><th>Port</th><th>Via</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div>';
    }

    function render(data) {
        var jobs = (data && data.jobs) || [];
        el("resolver-loading").classList.add("hidden");

        // Summary
        var totResolved = 0, totUnreg = 0, anyRunning = false;
        for (var i = 0; i < jobs.length; i++) {
            totResolved += jobs[i].resolved || 0;
            totUnreg += jobs[i].unreg || 0;
            if (jobs[i].status === "running") anyRunning = true;
        }
        el("rs-total-jobs").textContent = jobs.length;
        el("rs-total-resolved").textContent = totResolved;
        el("rs-total-unreg").textContent = totUnreg;

        if (jobs.length === 0) {
            el("resolver-empty").classList.remove("hidden");
            el("resolver-list").innerHTML = "";
            scheduleRefresh(false);
            return;
        }
        el("resolver-empty").classList.add("hidden");

        var list = el("resolver-list");
        list.innerHTML = "";
        for (var j = 0; j < jobs.length; j++) {
            (function (job) {
                var card = document.createElement("div");
                card.className = "rs-card";
                card.innerHTML =
                    '<div class="rs-card-head">' +
                        '<div class="rs-card-title">' +
                            '<span class="rs-jobid">Job ' + job.job_id.substring(0, 8) + '</span>' +
                            statusBadge(job.status) +
                        '</div>' +
                        '<div class="rs-card-meta">' + fmtTime(job.created_at) + '</div>' +
                    '</div>' +
                    '<div class="rs-card-nums">' +
                        '<span>Total <b>' + job.total + '</b></span>' +
                        '<span>Checked <b>' + job.checked + '</b></span>' +
                        '<span class="rs-cyan-t">Resolved <b>' + (job.resolved || 0) + '</b></span>' +
                        '<span class="rs-amber-t">Unreg <b>' + (job.unreg || 0) + '</b></span>' +
                    '</div>' +
                    '<div class="rs-chart-wrap"><canvas id="chart-' + job.job_id + '"></canvas></div>' +
                    '<details class="rs-details"><summary>Domain ter-resolve (' +
                        Object.keys(job.resolved_map || {}).length + ')</summary>' +
                        resolvedTable(job) +
                    '</details>';
                list.appendChild(card);
                buildChart(job);
            })(jobs[j]);
        }

        scheduleRefresh(anyRunning);
    }

    function scheduleRefresh(anyRunning) {
        if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
        // Refresh fast while something is running, slow otherwise
        refreshTimer = setTimeout(load, anyRunning ? 2000 : 15000);
    }

    function load() {
        fetch(APP_BASE + "/api/resolver")
            .then(function (r) { return r.json(); })
            .then(function (d) { render(d); })
            .catch(function () { scheduleRefresh(false); });
    }

    // Cleanup charts on unload
    window.addEventListener("beforeunload", function () {
        for (var k in charts) { try { charts[k].destroy(); } catch (e) {} }
        if (refreshTimer) clearTimeout(refreshTimer);
    });

    load();
})();
