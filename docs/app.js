// ── Config ───────────────────────────────────────────────────────────────────
const DATA_ROOT        = "./data";
const MANIFEST_URL     = `${DATA_ROOT}/odds_manifest.json`;
const HORSES_CSV_URL   = `${DATA_ROOT}/horses.csv`;
const ACCURACY_JSON_URL = `${DATA_ROOT}/model_accuracy_report_latest.json`;

// ── Globals ──────────────────────────────────────────────────────────────────
let manifest        = null;     // odds_manifest.json
let horsesLookup    = null;     // {horse_id: {venue: stats}} from CSV
let currentTag      = null;
let currentMerged   = null;     // merged JSON for the selected meeting
let accuracyReport  = null;
let allPicksRows    = [];       // flattened across all meetings (for all-picks view)

// ── Bootstrap ────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", async () => {
    showLoad();
    try {
        const [manifestRes, horsesRes, accRes] = await Promise.all([
            fetch(MANIFEST_URL),
            fetch(HORSES_CSV_URL),
            fetch(ACCURACY_JSON_URL).catch(() => null),
        ]);
        manifest = await manifestRes.json();
        await loadHorsesLookup(horsesRes);
        if (accRes && accRes.ok) accuracyReport = await accRes.json();

        if (!manifest || !manifest.entries || manifest.entries.length === 0) {
            showError("No meetings found in manifest.");
            hideLoad();
            return;
        }

        populateMeetingPicker();
        buildAccuracyView();
        // Auto-load first meeting
        const first = manifest.entries[0];
        loadMeeting(first.predictions_xlsx || `${DATA_ROOT}/odds/${first.tag}_merged.json`);
    } catch (e) {
        console.error(e);
        showError(`Bootstrap failed: ${e.message}`);
        hideLoad();
    }
});

// ── Load CSV (horses.csv) into a fast lookup ────────────────────────────────
async function loadHorsesLookup(response) {
    const text = await response.text();
    const lines = text.split("\n").slice(1); // skip header
    horsesLookup = {};
    for (const line of lines) {
        if (!line.trim()) continue;
        // Simple CSV (no quoted commas in this dataset)
        const parts = line.split(",");
        if (parts.length < 6) continue;
        const horse_id = parts[0].trim();
        const venue    = parts[2].trim();
        const distance = parseInt(parts[3].trim(), 10);
        const surface  = parts[4].trim();
        const starts   = parseInt(parts[6].trim(), 10) || 0;
        const wins     = parseInt(parts[7].trim(), 10) || 0;
        const places   = parseInt(parts[8].trim(), 10) || 0;
        const top4     = parseFloat(parts[10].trim()) || 0;
        const avg_pos  = parseFloat(parts[11].trim()) || 0;
        const best_at  = parseInt(parts[12].trim(), 10) || 99;
        const recent5  = parseFloat(parts[13].trim()) || 0;
        const days_since = parseInt(parts[14].trim(), 10) || 999;
        if (!horsesLookup[horse_id]) horsesLookup[horse_id] = [];
        horsesLookup[horse_id].push({
            venue, distance, surface,
            starts, wins, places, top4, avg_pos, best_at, recent5, days_since,
        });
    }
}

function lookupHorseHistory(horse_id, venue, distance, surface) {
    if (!horsesLookup || !horsesLookup[horse_id]) return null;
    const exact = horsesLookup[horse_id].find(
        s => s.venue === venue && s.distance === distance && s.surface === surface
    );
    if (exact) return exact;
    // Fallback: same horse, any (venue,distance,surface)
    return horsesLookup[horse_id][0] || null;
}

// ── View switching ──────────────────────────────────────────────────────────
function switchView(name) {
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
    const target = document.getElementById(`view-${name}`);
    if (target) target.classList.add("active");
    document.querySelector(`.nav-tab[data-view="${name}"]`)?.classList.add("active");
}

// ── Meeting picker ──────────────────────────────────────────────────────────
function populateMeetingPicker() {
    const sel = document.getElementById("dateSelect");
    const filterSel = document.getElementById("filterMeeting");
    sel.innerHTML = "";
    filterSel.innerHTML = '<option value="">All meetings</option>';

    const sorted = [...manifest.entries].sort((a, b) => b.tag.localeCompare(a.tag));
    for (const e of sorted) {
        const opt = document.createElement("option");
        opt.value = e.tag;
        opt.textContent = `${e.tag.replace("_", " ")}`;
        sel.appendChild(opt);

        const opt2 = document.createElement("option");
        opt2.value = e.tag;
        opt2.textContent = e.tag.replace("_", " ");
        filterSel.appendChild(opt2);
    }
    sel.onchange = () => loadMeeting(sel.value);
    sel.value = sorted[0].tag;
}

// ── Load one meeting ────────────────────────────────────────────────────────
async function loadMeeting(tagOrPath) {
    showLoad();
    document.getElementById("summarySection").style.display = "none";
    document.getElementById("detailSection").innerHTML = "";
    document.getElementById("crosscheckTableContainer").innerHTML = "Loading…";

    // Resolve to tag + URL
    let tag, mergedUrl;
    if (tagOrPath.endsWith(".xlsx") || tagOrPath.endsWith(".json")) {
        // path form
        const fname = tagOrPath.split("/").pop().replace(/\.(xlsx|json)$/, "");
        tag = fname;
    } else {
        tag = tagOrPath;
    }
    currentTag = tag;
    const entry = manifest.entries.find(e => e.tag === tag);
    if (!entry) {
        showError(`No manifest entry for ${tag}`);
        hideLoad();
        return;
    }
    mergedUrl = entry.merged_json;

    try {
        const resp = await fetch(mergedUrl);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        currentMerged = await resp.json();
    } catch (e) {
        showError(`Failed to load ${mergedUrl}: ${e.message}`);
        hideLoad();
        return;
    }

    renderMeetingHeader();
    renderSummaryCards();
    renderCrossCheckTable();
    renderRaceDetails();
    collectAllPicksRows();  // for the all-picks view
    hideLoad();
    document.getElementById("summarySection").style.display = "block";
}

function renderMeetingHeader() {
    const flags = [];
    if (currentMerged.has_pre)  flags.push("📊 pre-odds");
    if (currentMerged.has_post) flags.push("💰 dividends");
    document.getElementById("meetingFlags").textContent = flags.join("  ");
    const venueEl = document.getElementById("venueTag");
    venueEl.textContent = currentMerged.venue;
    venueEl.className = `venue-indicator venue-${currentMerged.venue.toLowerCase()}`;
}

function renderSummaryCards() {
    const grid = document.getElementById("summaryGrid");
    grid.innerHTML = "";
    const races = Object.values(currentMerged.races || {});
    let raceIndex = 1;
    for (const race of races) {
        const card = document.createElement("div");
        card.className = "race-summary-card fade-in";
        const top4 = race.horses.filter(h => h.model_rank !== null).slice(0, 4);
        const anyHit = top4.some(h => h.is_top4);
        card.classList.add(anyHit ? "rsc-hit" : "rsc-miss");
        const cardBody = top4.map((h, i) => {
            const cls = ["banker","","",""][i] || "";
            const pct = h.win_prob ? h.win_prob.toFixed(1) + "%" : "—";
            const odds = h.calc_odds ? h.calc_odds.toFixed(1) : "—";
            const preW = h.pre_win_odds ? h.pre_win_odds.toFixed(1) : "—";
            const wDiv = h.win_div ? `$${h.win_div.toFixed(0)}` : "—";
            const pDiv = h.pla_div ? `$${h.pla_div.toFixed(0)}` : "—";
            const act = h.actual_pos !== null && h.actual_pos !== undefined
                ? `<span class="act-badge act-${h.actual_pos <= 4 ? h.actual_pos : 'bad'}">${h.actual_pos}</span>` : "";
            return `
                <div class="rsc-pick ${cls}">
                    <div class="rsc-pick-left">
                        <span class="horse-no">${h.horse_no}</span>
                        <span class="horse-name" onclick="openHorsePanel('${h.horse_id}','${currentMerged.venue}',${race.distance},'${race.surface}')">${h.horse_name}</span>
                    </div>
                    <div class="rsc-pick-right">
                        <span class="prob-badge">${pct}</span>
                        <span class="odds-badge">$${odds}</span>
                        ${act ? `<span class="act-pos">→ ${act}</span>` : ""}
                    </div>
                </div>
                <div class="rsc-pick-meta">
                    pre: $${preW} · win_div: ${wDiv} · pla_div: ${pDiv}
                </div>`;
        }).join("");
        const top4hit = top4.filter(h => h.is_top4).map(h => `#${h.horse_no}`).join(", ") || "—";
        card.innerHTML = `
            <div class="rsc-header">
                <span class="rsc-title">R${race.race_no}</span>
                <span class="rsc-meta">${race.distance}m ${race.surface} · ${race.field_size} runners</span>
            </div>
            <div class="rsc-picks">${cardBody}</div>
            <div class="rsc-footer">top-4 hit: <b>${top4hit}</b></div>
        `;
        grid.appendChild(card);
        raceIndex++;
    }
}

function renderCrossCheckTable() {
    const container = document.getElementById("crosscheckTableContainer");
    const races = Object.values(currentMerged.races || {});
    if (!races.length) {
        container.innerHTML = "<p class='muted-text'>No races in this meeting.</p>";
        return;
    }
    let html = `
    <table class="glass-panel" style="width:100%; border-collapse: separate; border-spacing: 0; overflow: hidden;">
        <thead>
            <tr style="background: var(--panel-bg);">
                <th style="padding: 10px;">Race</th>
                <th style="padding: 10px;">Model Rank</th>
                <th style="padding: 10px;">Horse</th>
                <th style="padding: 10px;">Win%</th>
                <th style="padding: 10px;">Calc</th>
                <th style="padding: 10px;">Pre WIN</th>
                <th style="padding: 10px;">Win Div</th>
                <th style="padding: 10px;">Pla Div</th>
                <th style="padding: 10px;">Actual</th>
                <th style="padding: 10px;">QPL pairs</th>
            </tr>
        </thead>
        <tbody>`;
    for (const race of races) {
        for (const h of race.horses.filter(x => x.model_rank !== null)) {
            const ap = h.actual_pos;
            const apCell = ap !== null && ap !== undefined
                ? `<span class="act-badge act-${ap <= 4 ? ap : 'bad'}">${ap}</span>`
                : "—";
            const qpl = (h.qpl_pairs || []).map(p =>
                `<span class="qpl-pair">${p.pair}=$${p.dividend.toFixed(0)}</span>`).join(" ") || "—";
            const hit = h.is_top4 ? "✅" : "❌";
            html += `
            <tr style="border-top: 1px solid var(--panel-border);">
                <td style="padding: 8px 10px;"><b>R${race.race_no}</b></td>
                <td style="padding: 8px 10px;">P${h.model_rank} ${hit}</td>
                <td style="padding: 8px 10px;">
                  <a href="#" onclick="event.preventDefault(); openHorsePanel('${h.horse_id}','${currentMerged.venue}',${race.distance},'${race.surface}')" style="color: var(--accent-primary); text-decoration: none;">
                    #${h.horse_no} ${h.horse_name}
                  </a>
                </td>
                <td style="padding: 8px 10px;">${h.win_prob?.toFixed(1) ?? "—"}%</td>
                <td style="padding: 8px 10px;">${h.calc_odds?.toFixed(1) ?? "—"}</td>
                <td style="padding: 8px 10px;">${h.pre_win_odds?.toFixed(1) ?? "—"}</td>
                <td style="padding: 8px 10px;">${h.win_div ? "$"+h.win_div.toFixed(0) : "—"}</td>
                <td style="padding: 8px 10px;">${h.pla_div ? "$"+h.pla_div.toFixed(0) : "—"}</td>
                <td style="padding: 8px 10px;">${apCell}</td>
                <td style="padding: 8px 10px; font-size: 11px;">${qpl}</td>
            </tr>`;
        }
    }
    html += `</tbody></table>`;
    container.innerHTML = html;
}

function renderRaceDetails() {
    const sec = document.getElementById("detailSection");
    sec.innerHTML = `<div class="section-title-wrap" style="margin-top:48px;">
        <h2 class="section-title">Field Analytics</h2>
        <p class="section-subtitle">Per-race score breakdown + horse history side panel</p>
    </div>`;
    const races = Object.values(currentMerged.races || {});
    for (const race of races) {
        const card = document.createElement("div");
        card.className = "glass-panel detail-card collapsed fade-in";
        card.id = `race-card-${race.race_no}`;
        const top4 = race.horses.filter(h => h.model_rank !== null);
        const allInField = top4.concat(race.horses.filter(h => h.model_rank === null));
        const horseRows = allInField.map(h => {
            const age = h.draw ? `D${h.draw}` : "—";
            return `
            <tr>
                <td style="padding: 6px 10px;">${h.model_rank ? `P${h.model_rank}` : "—"}</td>
                <td style="padding: 6px 10px;">
                    <a href="#" onclick="event.preventDefault(); openHorsePanel('${h.horse_id}','${currentMerged.venue}',${race.distance},'${race.surface}')" style="color: var(--accent-primary); text-decoration:none;">
                    #${h.horse_no} ${h.horse_name}</a>
                </td>
                <td style="padding: 6px 10px;">${age}</td>
                <td style="padding: 6px 10px;">${h.weight_lbs}</td>
                <td style="padding: 6px 10px;">${h.rating}</td>
                <td style="padding: 6px 10px;">${h.last6_runs || "—"}</td>
                <td style="padding: 6px 10px;">${h.jockey || "—"}</td>
                <td style="padding: 6px 10px;">${h.trainer || "—"}</td>
                <td style="padding: 6px 10px;">${h.win_prob?.toFixed(1) ?? "—"}%</td>
                <td style="padding: 6px 10px;">${h.calc_odds?.toFixed(1) ?? "—"}</td>
                <td style="padding: 6px 10px;">${h.actual_pos ?? "—"}</td>
            </tr>`;
        }).join("");
        card.innerHTML = `
            <div class="dc-header" onclick="toggleRaceCard(${race.race_no})">
                <div class="dc-title-group">
                    <span class="dc-race-no">R${race.race_no}</span>
                    <div>
                        <div class="dc-name">${race.distance}m ${race.surface} · ${race.field_size} runners</div>
                        <div class="dc-meta">Top-4 + lifetime stats in side panel (click any horse)</div>
                    </div>
                </div>
                <div class="dc-toggle">▼</div>
            </div>
            <div class="dc-body">
                <table style="width:100%; border-collapse: collapse; font-size:13px;">
                    <thead>
                        <tr style="border-bottom: 1px solid var(--panel-border);">
                            <th style="padding: 8px 10px; text-align: left;">P</th>
                            <th style="padding: 8px 10px; text-align: left;">Horse</th>
                            <th style="padding: 8px 10px; text-align: left;">Dr</th>
                            <th style="padding: 8px 10px; text-align: left;">Wt</th>
                            <th style="padding: 8px 10px; text-align: left;">Rt</th>
                            <th style="padding: 8px 10px; text-align: left;">Last6</th>
                            <th style="padding: 8px 10px; text-align: left;">Jockey</th>
                            <th style="padding: 8px 10px; text-align: left;">Trainer</th>
                            <th style="padding: 8px 10px; text-align: left;">Win%</th>
                            <th style="padding: 8px 10px; text-align: left;">Calc</th>
                            <th style="padding: 8px 10px; text-align: left;">Pos</th>
                        </tr>
                    </thead>
                    <tbody>${horseRows}</tbody>
                </table>
            </div>
        `;
        sec.appendChild(card);
    }
}

function toggleRaceCard(n) {
    const card = document.getElementById(`race-card-${n}`);
    if (card) card.classList.toggle("collapsed");
}

// ── Horse side panel ────────────────────────────────────────────────────────
function openHorsePanel(horse_id, venue, distance, surface) {
    const hist = lookupHorseHistory(horse_id, venue, distance, surface);
    const panel = document.getElementById("horsePanel");
    const body  = document.getElementById("horsePanelBody");
    document.getElementById("horsePanelTitle").textContent = horse_id;
    if (!hist) {
        body.innerHTML = `<p class="muted-text">No lifetime data for ${horse_id}.</p>`;
    } else {
        body.innerHTML = `
            <div class="hp-stats">
                <div class="hp-stat"><span class="hp-label">Venue</span><span class="hp-val">${hist.venue}</span></div>
                <div class="hp-stat"><span class="hp-label">Distance</span><span class="hp-val">${hist.distance}m</span></div>
                <div class="hp-stat"><span class="hp-label">Surface</span><span class="hp-val">${hist.surface}</span></div>
                <div class="hp-stat"><span class="hp-label">Starts</span><span class="hp-val">${hist.starts}</span></div>
                <div class="hp-stat"><span class="hp-label">Wins</span><span class="hp-val">${hist.wins}</span></div>
                <div class="hp-stat"><span class="hp-label">Places</span><span class="hp-val">${hist.places}</span></div>
                <div class="hp-stat"><span class="hp-label">Top-4 rate</span><span class="hp-val">${(hist.top4 * 100).toFixed(0)}%</span></div>
                <div class="hp-stat"><span class="hp-label">Avg pos</span><span class="hp-val">${hist.avg_pos.toFixed(2)}</span></div>
                <div class="hp-stat"><span class="hp-label">Best at slice</span><span class="hp-val">${hist.best_at}</span></div>
                <div class="hp-stat"><span class="hp-label">Recent L5</span><span class="hp-val">${hist.recent5.toFixed(2)}</span></div>
                <div class="hp-stat"><span class="hp-label">Days since</span><span class="hp-val">${hist.days_since}</span></div>
            </div>
            <p class="muted-text" style="margin-top: 12px; font-size: 11px;">
                Slice = ${venue} ${distance}m ${surface}. Falls back to horse's most-frequent slice if exact not found.
            </p>`;
    }
    panel.classList.add("open");
    document.getElementById("horsePanelBackdrop").classList.add("open");
}

function closeHorsePanel() {
    document.getElementById("horsePanel").classList.remove("open");
    document.getElementById("horsePanelBackdrop").classList.remove("open");
}

// ── Accuracy view ───────────────────────────────────────────────────────────
function buildAccuracyView() {
    if (!accuracyReport) {
        document.getElementById("accuracySummary").innerHTML =
            '<p class="muted-text">No accuracy report loaded.</p>';
        return;
    }
    const h = accuracyReport.headline || {};
    const roi = accuracyReport.roi_sim || {};
    document.getElementById("accuracySummary").innerHTML = `
        <div class="accuracy-cards-row">
            <div class="acc-card"><div class="acc-val">${h.top4_pct_any_pick ?? "?"}%</div><div class="acc-lbl">Top-4 hit rate</div></div>
            <div class="acc-card"><div class="acc-val">${h.p1_win_pct ?? "?"}%</div><div class="acc-lbl">P1 win rate</div></div>
            <div class="acc-card"><div class="acc-val">${h.win_pct_any_pick ?? "?"}%</div><div class="acc-lbl">Win any of 4</div></div>
            <div class="acc-card"><div class="acc-val">${accuracyReport.naive_baseline_top4_pct ?? "?"}%</div><div class="acc-lbl">Naive favourites</div></div>
            <div class="acc-card"><div class="acc-val">${roi.place_roi_pct ?? "?"}%</div><div class="acc-lbl">Place ROI</div></div>
            <div class="acc-card"><div class="acc-val">${roi.win_roi_pct ?? "?"}%</div><div class="acc-lbl">Win ROI</div></div>
        </div>`;

    // Per-meeting bar chart (CSS-only — no chart lib)
    const per = accuracyReport.per_meeting || [];
    if (per.length === 0) {
        document.getElementById("accuracyChart").innerHTML = "";
        return;
    }
    const max = 100;
    const bars = per.map(m => `
        <div class="acc-bar-row" title="${m.meeting}: ${m.top4_pct}% top-4 hit">
            <span class="acc-bar-label">${m.meeting.replace("_"," ")}</span>
            <div class="acc-bar-track">
                <div class="acc-bar-fill" style="width: ${m.top4_pct / max * 100}%; background: ${m.top4_pct >= 90 ? 'var(--accent-primary)' : m.top4_pct >= 60 ? 'var(--gold)' : 'var(--danger)'};"></div>
            </div>
            <span class="acc-bar-val">${m.top4_pct}%</span>
        </div>`).join("");
    document.getElementById("accuracyChart").innerHTML = `<div class="acc-bar-chart">${bars}</div>`;

    // Per-meeting summary grid
    document.getElementById("accuracyPerMeeting").innerHTML = per.map(m => `
        <div class="pm-card">
            <div class="pm-head">${m.meeting.replace("_"," ")}</div>
            <div class="pm-body">
                <div><b>${m.races}</b> races</div>
                <div>P1 win: ${m.p1_wins}/${m.races}</div>
                <div>Top-4 hit: ${m.races_top4_hit}/${m.races} (${m.top4_pct}%)</div>
            </div>
        </div>`).join("");
}

// ── All-picks view ─────────────────────────────────────────────────────────
function collectAllPicksRows() {
    if (!currentMerged) return;
    for (const race of Object.values(currentMerged.races)) {
        for (const h of race.horses.filter(x => x.model_rank !== null)) {
            allPicksRows.push({
                meeting:    currentMerged.tag,
                venue:      currentMerged.venue,
                race_no:    race.race_no,
                rank:       h.model_rank,
                horse_no:   h.horse_no,
                horse_name: h.horse_name,
                horse_id:   h.horse_id,
                win_prob:   h.win_prob,
                calc_odds:  h.calc_odds,
                pre_win:    h.pre_win_odds,
                win_div:    h.win_div,
                pla_div:    h.pla_div,
                actual_pos: h.actual_pos,
                is_top4:    h.is_top4,
            });
        }
    }
}

function renderAllPicksTable() {
    const filter = document.getElementById("filterMeeting").value;
    const rows = allPicksRows.filter(r => !filter || r.meeting === filter);
    const html = `
    <table class="glass-panel" style="width:100%; border-collapse: separate; border-spacing: 0; overflow:auto;">
        <thead>
            <tr style="background: var(--panel-bg);">
                <th style="padding: 8px;">Meeting</th><th style="padding: 8px;">R</th><th style="padding: 8px;">P</th>
                <th style="padding: 8px;">Horse</th><th style="padding: 8px;">Win%</th><th style="padding: 8px;">Calc</th>
                <th style="padding: 8px;">Pre</th><th style="padding: 8px;">Win$</th><th style="padding: 8px;">Pla$</th>
                <th style="padding: 8px;">Pos</th>
            </tr>
        </thead>
        <tbody>
            ${rows.map(r => `
            <tr style="border-top: 1px solid var(--panel-border);">
                <td style="padding: 6px 10px;">${r.meeting.replace("_"," ")}</td>
                <td style="padding: 6px 10px;">R${r.race_no}</td>
                <td style="padding: 6px 10px;">${r.rank}</td>
                <td style="padding: 6px 10px;">#${r.horse_no} ${r.horse_name}</td>
                <td style="padding: 6px 10px;">${r.win_prob?.toFixed(1) ?? "—"}%</td>
                <td style="padding: 6px 10px;">${r.calc_odds?.toFixed(1) ?? "—"}</td>
                <td style="padding: 6px 10px;">${r.pre_win?.toFixed(1) ?? "—"}</td>
                <td style="padding: 6px 10px;">${r.win_div ? "$"+r.win_div.toFixed(0) : "—"}</td>
                <td style="padding: 6px 10px;">${r.pla_div ? "$"+r.pla_div.toFixed(0) : "—"}</td>
                <td style="padding: 6px 10px;">${r.actual_pos ?? "—"}</td>
            </tr>`).join("")}
        </tbody>
    </table>
    <p class="muted-text" style="margin-top:8px;">${rows.length} picks shown</p>`;
    document.getElementById("allPicksTable").innerHTML = html;
}

// Hook: refresh all-picks table when its view is shown
document.addEventListener("DOMContentLoaded", () => {
    // Override nav clicks to refresh data views
    document.querySelectorAll(".nav-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            if (tab.dataset.view === "all-picks") renderAllPicksTable();
        });
    });
});

// ── UI Helpers ──────────────────────────────────────────────────────────────
function showLoad() {
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.classList.remove("hidden");
    document.getElementById("headerMetaText").textContent = "Loading…";
}
function hideLoad() {
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.classList.add("hidden");
    document.getElementById("headerMetaText").textContent = "Online";
}
function showError(msg) {
    document.getElementById("crosscheckTableContainer").innerHTML = `
        <div class="glass-panel" style="padding:24px; border-color: var(--danger); text-align:center;">
            <div style="font-size:32px; margin-bottom:12px;">⚠️</div>
            <h3 style="color: var(--danger); font-family: var(--font-heading);">System Error</h3>
            <p style="color: var(--text-muted); margin-top:8px;">${msg}</p>
        </div>`;
}
