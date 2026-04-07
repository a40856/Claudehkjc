// ── Config ───────────────────────────────────────────────────────────────────
const DATA_ROOT = "data/predictions";
const DATA_RESULTS_ROOT = "data/results";

// ── Globals ──────────────────────────────────────────────────────────────────
let currentIndex = null;
let currentWorkbook = null;
let currentDayMeta = null;

// ── Bootstrap ────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", async () => {
    try {
        const indexRes = await fetch(`${DATA_ROOT}/index.json`);
        if (!indexRes.ok) throw new Error("Could not load index.json");
        currentIndex = await indexRes.json();
    } catch (e) {
        console.error(e);
        showError("No prediction files found. Ensure GitHub Actions has run.");
        hideLoad();
        return;
    }

    if (!currentIndex || !currentIndex.entries || currentIndex.entries.length === 0) {
        showError("Index is empty.");
        hideLoad();
        return;
    }

    const sel = document.getElementById("dateSelect");
    sel.innerHTML = "";
    
    // Sort descending by date
    currentIndex.entries.sort((a, b) => b.file.localeCompare(a.file)).forEach(e => {
        const opt = document.createElement("option");
        opt.value = e.file;
        opt.textContent = `${e.date} — ${e.venue}`;
        sel.appendChild(opt);
    });

    loadDay(sel.value);
});

// ── Load one day (XLSX) ──────────────────────────────────────────────────────
async function loadDay(filename) {
    showLoad();
    document.getElementById("summarySection").style.display = "none";
    document.getElementById("detailSection").innerHTML = "";

    const meta = currentIndex.entries.find(e => e.file === filename);
    currentDayMeta = meta;

    try {
        const url = `${DATA_ROOT}/${filename}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const arrayBuffer = await response.arrayBuffer();
        
        // Parse the Excel file using SheetJS
        // read raw data
        currentWorkbook = XLSX.read(arrayBuffer, { type: 'array' });
        
        updateHeader(meta);
        buildDashboardSummary();
        buildRaceDetails();

        hideLoad();
        document.getElementById("summarySection").style.display = "block";
    } catch (err) {
        console.error(err);
        showError(`Failed to load or parse ${filename}: ${err.message}`);
        hideLoad();
    }
}

// ── UI Helpers ────────────────────────────────────────────────────────────────
function showLoad() {
    const overlay = document.getElementById("loadingOverlay");
    if(overlay) overlay.classList.remove("hidden");
    document.getElementById("headerMetaText").textContent = "Loading projection data...";
}

function hideLoad() {
    const overlay = document.getElementById("loadingOverlay");
    if(overlay) overlay.classList.add("hidden");
}

function updateHeader(meta) {
    document.getElementById("headerMetaText").textContent = `System online. Model updated.`;
    
    const vTag = document.getElementById("venueTag");
    vTag.textContent = meta.venue;
    vTag.className = `venue-indicator venue-${meta.venue.toLowerCase()}`;

    const dateStr = meta.generated_at ? new Date(meta.generated_at).toLocaleString() : "Unknown";
    document.getElementById("generatedAt").textContent = `Generated: ${dateStr}`;
}

function showError(msg) {
    document.getElementById("detailSection").innerHTML = `
        <div class="glass-panel" style="padding: 24px; border-color: var(--danger); text-align: center;">
            <div style="font-size: 32px; margin-bottom: 12px;">⚠️</div>
            <h3 style="color: var(--danger); font-family: var(--font-heading); font-size: 20px;">System Error</h3>
            <p style="color: var(--text-muted); margin-top: 8px;">${msg}</p>
        </div>
    `;
}

// ── Build Summary Cards ───────────────────────────────────────────────────────
function buildDashboardSummary() {
    if (!currentWorkbook.Sheets["Summary"]) {
        console.warn("No 'Summary' sheet found in workbook.");
        return;
    }

    const summaryData = XLSX.utils.sheet_to_json(currentWorkbook.Sheets["Summary"]);
    const grid = document.getElementById("summaryGrid");
    grid.innerHTML = "";

    summaryData.forEach(row => {
        const card = document.createElement("div");
        card.className = "race-summary-card fade-in";
        card.onclick = () => {
            const el = document.getElementById(`race-card-${row.race_no}`);
            if (el) {
                if(el.classList.contains("collapsed")) toggleRaceCard(row.race_no);
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        };

        // Ensure variables exist
        const p1_no = row["P1_no"] || "-", p1_horse = row["P1_horse"] || "-", p1_win = row["P1_win%"] || "-", p1_odds = row["P1_calcodds"] || "-";
        const p2_no = row["P2_no"] || "-", p2_horse = row["P2_horse"] || "-", p2_win = row["P2_win%"] || "-", p2_odds = row["P2_calcodds"] || "-";
        const p3_no = row["P3_no"] || "-", p3_horse = row["P3_horse"] || "-", p3_win = row["P3_win%"] || "-", p3_odds = row["P3_calcodds"] || "-";
        const p4_no = row["P4_no"] || "-", p4_horse = row["P4_horse"] || "-", p4_win = row["P4_win%"] || "-", p4_odds = row["P4_calcodds"] || "-";

        card.innerHTML = `
            <div class="rsc-header">
                <span class="rsc-title">Race ${row.race_no}</span>
                <span class="rsc-meta">${row.race_name || ""}</span>
            </div>
            <div class="rsc-picks">
                <!-- 1st Pick (Banker) -->
                <div class="rsc-pick banker">
                    <div class="rsc-pick-left">
                        <span class="horse-no">${p1_no}</span>
                        <span class="horse-name">${p1_horse}</span>
                    </div>
                    <div class="rsc-pick-right">
                        <span class="prob-badge">${Number(p1_win).toFixed(1)}%</span>
                        <span class="odds-badge">$${Number(p1_odds).toFixed(1)}</span>
                    </div>
                </div>
                <!-- 2nd Pick -->
                <div class="rsc-pick">
                    <div class="rsc-pick-left">
                        <span class="horse-no" style="background: var(--card-bg)">${p2_no}</span>
                        <span class="horse-name">${p2_horse}</span>
                    </div>
                    <div class="rsc-pick-right">
                        <span class="prob-badge" style="color:var(--text-main); font-size:12px;">${Number(p2_win).toFixed(1)}%</span>
                        <span class="odds-badge">$${Number(p2_odds).toFixed(1)}</span>
                    </div>
                </div>
                <!-- 3rd Pick -->
                <div class="rsc-pick">
                    <div class="rsc-pick-left">
                        <span class="horse-no" style="background: var(--card-bg)">${p3_no}</span>
                        <span class="horse-name">${p3_horse}</span>
                    </div>
                    <div class="rsc-pick-right">
                        <span class="prob-badge" style="color:var(--text-main); font-size:12px;">${Number(p3_win).toFixed(1)}%</span>
                        <span class="odds-badge">$${Number(p3_odds).toFixed(1)}</span>
                    </div>
                </div>
                <!-- 4th Pick -->
                <div class="rsc-pick">
                    <div class="rsc-pick-left">
                        <span class="horse-no" style="background: var(--card-bg)">${p4_no}</span>
                        <span class="horse-name">${p4_horse}</span>
                    </div>
                    <div class="rsc-pick-right">
                        <span class="prob-badge" style="color:var(--text-main); font-size:12px;">${Number(p4_win).toFixed(1)}%</span>
                        <span class="odds-badge">$${Number(p4_odds).toFixed(1)}</span>
                    </div>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

// ── Build Full Detailed Tables ────────────────────────────────────────────────
function buildRaceDetails() {
    const sec = document.getElementById("detailSection");
    sec.innerHTML = `
        <div class="section-title-wrap" style="margin-top: 48px;">
            <h2 class="section-title">Field Analytics</h2>
            <p class="section-subtitle">Comprehensive scoring matrix per race</p>
        </div>
    `;

    // Loop through R1 -> R15
    for(let i=1; i<=15; i++) {
        const sheetName = `R${i}`;
        if(!currentWorkbook.Sheets[sheetName]) continue;

        const raceData = XLSX.utils.sheet_to_json(currentWorkbook.Sheets[sheetName]);
        if(!raceData || raceData.length === 0) continue;

        const horseRows = raceData.filter(r => !r.bet_type || r.bet_type === "");
        const dividendRows = raceData.filter(r => r.bet_type && r.bet_type !== "");

        const card = document.createElement("div");
        card.className = "glass-panel detail-card collapsed fade-in";
        card.id = `race-card-${i}`;

        card.innerHTML = `
            <div class="dc-header" onclick="toggleRaceCard(${i})">
                <div class="dc-title-group">
                    <span class="dc-race-no">R${i}</span>
                    <div>
                        <div class="dc-name">Full Field Projection</div>
                        <div class="dc-meta">${horseRows.length} Runners</div>
                    </div>
                </div>
                <div class="dc-toggle" id="tog-${i}">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                </div>
            </div>
            <div class="dc-body" id="body-${i}">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Rnk</th>
                            <th>#</th>
                            <th>Horse</th>
                            <th>Draw</th>
                            <th>Rating</th>
                            <th>Form</th>
                            <th>Mkt</th>
                            <th>Draw Score</th>
                            <th>Jockey Score</th>
                            <th>Train Score</th>
                            <th>H2H Score</th>
                            <th>Composite</th>
                            <th>Valuation</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${horseRows.map((h, idx) => buildDetailRow(h, idx)).join("")}
                    </tbody>
                </table>
                ${dividendRows.length ? buildDividendsTable(dividendRows) : ""}
            </div>
        `;
        sec.appendChild(card);
    }

    buildCrossCheck();
}

function buildDetailRow(h, idx) {
    const isTopPick = idx === 0;
    
    // Formatting helpers
    const fSc = (v) => {
        const n = parseFloat(v) || 0;
        const c = n >= 7.5 ? "score-high" : n >= 5.5 ? "score-mid" : "score-low";
        return `<span class="score-val ${c}">${n.toFixed(1)}</span>`;
    };

    const prob = parseFloat(h.win_prob) || 0;
    
    return `
        <tr class="${isTopPick ? 'top-pick' : ''}">
            <td><span class="rank-${idx+1} font-heading font-bold">${idx+1}</span></td>
            <td><strong>${h.horse_no}</strong></td>
            <td>
                <div class="horse-cell">
                    <span class="horse-cell-name">${h.horse_name}</span>
                    <span class="horse-cell-jockey">${h.jockey} / ${h.trainer}</span>
                </div>
            </td>
            <td>${h.draw || "—"}</td>
            <td>${h.rating || "—"}</td>
            <td>${fSc(h.s_form)}</td>
            <td>${fSc(h.s_market)}</td>
            <td>${fSc(h.s_draw)}</td>
            <td>${fSc(h.s_jockey)}</td>
            <td>${fSc(h.s_trainer)}</td>
            <td>${fSc(h.s_h2h)}</td>
            <td><span class="composite-score">${parseFloat(h.composite || 0).toFixed(3)}</span></td>
            <td>
                <div class="prob-cell">
                    <span class="prob-text">${prob.toFixed(1)}% <span class="muted-text font-normal" style="font-size:11px">($${parseFloat(h.calc_odds || 0).toFixed(1)})</span></span>
                    <div class="prob-bar-container">
                        <div class="prob-bar-fill" style="width: ${Math.min(prob, 100)}%"></div>
                    </div>
                </div>
            </td>
        </tr>
    `;
}

function buildDividendsTable(rows) {
    const body = rows.map(r => `
        <tr>
            <td>${r.bet_type || ""}</td>
            <td>${r.combo || ""}</td>
            <td>${r.dividend || ""}</td>
        </tr>
    `).join("");

    return `
        <div class="race-dividends" style="margin-top: 18px;">
            <div class="section-subtitle" style="margin-bottom: 8px;">Actual Dividends</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th>Pool</th>
                        <th>Combo</th>
                        <th>Dividend</th>
                    </tr>
                </thead>
                <tbody>
                    ${body}
                </tbody>
            </table>
        </div>
    `;
}

async function buildCrossCheck() {
    const container = document.getElementById("crosscheckTableContainer");
    container.innerHTML = "";

    if (!currentDayMeta) {
        container.innerHTML = `<p>No date selected.</p>`;
        return;
    }

    const jsonFile = `${DATA_RESULTS_ROOT}/${currentDayMeta.file.replace(/\.xlsx$/, "_crosscheck.json")}`;
    const resultsFile = `${DATA_RESULTS_ROOT}/${currentDayMeta.file}`;

    try {
        let crosscheckData = null;

        const jsonRes = await fetch(jsonFile);
        if (jsonRes.ok) {
            crosscheckData = await jsonRes.json();
        }

        if (crosscheckData) {
            const header = `<div class="crosscheck-summary" style="margin-bottom: 16px;">` +
                `<strong>Cross-check Total:</strong> ${crosscheckData.summary.total_hits}/${crosscheckData.summary.total_picks}` +
                ` (${crosscheckData.summary.hit_rate}% top ${4})` +
                `</div>`;

            const hdr = `<table class="data-table"><thead><tr><th>Race</th><th>Prediction</th><th>Actual</th><th>Hits</th></tr></thead><tbody>`;
            const rows = crosscheckData.races.map(r => {
                const actual = r.actual.map(a => `${a.horse_no}${a.hit ? "✅" : ""}`).join("-");
                return `<tr><td>R${r.race_no}</td><td>${r.prediction_string}</td><td>${actual}</td><td>${r.hits}/${r.total}</td></tr>`;
            });
            container.innerHTML = header + hdr + rows.join("") + `</tbody></table>`;
            document.getElementById("crosscheckSection").style.display = "block";
            return;
        }

        const res = await fetch(resultsFile);
        if (!res.ok) {
            container.innerHTML = `<p>No results file found for ${currentDayMeta.date} ${currentDayMeta.venue}.</p>`;
            document.getElementById("crosscheckSection").style.display = "block";
            return;
        }

        const resultsBuffer = await res.arrayBuffer();
        const resultsWorkbook = XLSX.read(resultsBuffer, { type: "array" });

        if (!resultsWorkbook.Sheets["Results"]) {
            container.innerHTML = `<p>Results sheet missing in ${currentDayMeta.file}.</p>`;
            document.getElementById("crosscheckSection").style.display = "block";
            return;
        }

        const resultsData = XLSX.utils.sheet_to_json(resultsWorkbook.Sheets["Results"]);
        const summaryData = currentWorkbook.Sheets["Summary"] ? XLSX.utils.sheet_to_json(currentWorkbook.Sheets["Summary"]) : [];

        if (summaryData.length === 0) {
            container.innerHTML = `<p>No summary data available for projections.</p>`;
            document.getElementById("crosscheckSection").style.display = "block";
            return;
        }

        const hdr = `<table class="data-table"><thead><tr><th>Race</th><th>Pick</th><th>Horse #</th><th>Predicted Horse</th><th>Actual Pos</th><th>Hit (Top 4)</th></tr></thead><tbody>`;
        const rows = [];

        summaryData.forEach(row => {
            for (let pick = 1; pick <= 4; pick++) {
                const pickNo = row[`P${pick}_no`];
                const pickName = row[`P${pick}_horse`] || "";
                const result = resultsData.find(r => Number(r.race_no) === Number(row.race_no) && Number(r.horse_no) === Number(pickNo));
                const actualPos = result ? result.pos : "N/A";
                const hit = (result && Number(actualPos) > 0 && Number(actualPos) <= 4) ? "✓" : "✗";
                const hitClass = hit === "✓" ? "hit-yes" : "hit-no";
                rows.push(`<tr><td>R${row.race_no}</td><td>${pick}</td><td>${pickNo}</td><td>${pickName}</td><td>${actualPos}</td><td class="${hitClass}">${hit}</td></tr>`);
            }
        });

        container.innerHTML = hdr + rows.join("") + `</tbody></table>`;
        document.getElementById("crosscheckSection").style.display = "block";
    } catch (err) {
        console.error(err);
        container.innerHTML = `<p>Error loading cross-check: ${err.message}</p>`;
        document.getElementById("crosscheckSection").style.display = "block";
    }
}

// ── Toggle Accordion ──────────────────────────────────────────────────────────
window.toggleRaceCard = function(raceNo) {
    const card = document.getElementById(`race-card-${raceNo}`);
    if (card) {
        card.classList.toggle("collapsed");
    }
};