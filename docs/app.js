// ── Config ───────────────────────────────────────────────────────────────────
const DATA_ROOT = "../data/predictions";   // relative to docs/

// ── Bootstrap ────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", async () => {
    const index = await fetchJSON(`${DATA_ROOT}/index.json`);
    if (!index || !index.entries || index.entries.length === 0) {
        showError("No prediction files found yet. Run predict.py first.");
        return;
    }

    const sel = document.getElementById("dateSelect");
    index.entries
        .sort((a, b) => b.date.localeCompare(a.date))
        .forEach(e => {
            const opt = document.createElement("option");
            opt.value = e.file;
            opt.textContent = `${e.date}  ${e.venue}`;
            sel.appendChild(opt);
        });

    loadDay(sel.value);
});

// ── Load one day ─────────────────────────────────────────────────────────────
async function loadDay(file) {
    document.getElementById("summarySection").style.display = "none";
    document.getElementById("detailSection").innerHTML =
        `<div class="loading">⏳ Loading predictions…</div>`;

    const data = await fetchJSON(`${DATA_ROOT}/${file}`);
    if (!data) { showError(`Could not load ${file}`); return; }

    document.getElementById("headerMeta").textContent =
        `${data.date}  ·  ${data.venue}  ·  ${data.races.length} races`;

    const vTag = document.getElementById("venueTag");
    vTag.textContent = data.venue;
    vTag.className = `badge badge-${data.venue.toLowerCase()}`;

    document.getElementById("generatedAt").textContent =
        `Generated: ${data.generated_at
            ? data.generated_at.replace("T", " ").slice(0, 16) : "—"}`;

    buildSummary(data.races);
    buildDetails(data.races);
}

// ── Summary table ─────────────────────────────────────────────────────────────
function buildSummary(races) {
    const tbody = document.getElementById("summaryBody");
    tbody.innerHTML = "";

    races.forEach(race => {
        const h = race.horses || [];
        const top4 = h.slice(0, 4);
        const vals = h.filter(x => x.value_flag === "VALUE").map(x => x.horse_name);

        const tr = document.createElement("tr");
        tr.onclick = () => toggleRaceCard(race.race_no);
        tr.innerHTML = `
      <td><strong>R${race.race_no}</strong></td>
      <td><span class="muted">${race.race_class || ""} ${race.distance || ""}m</span></td>
      ${top4.map(h => `
        <td>
          <div class="pick-cell">
            <span class="pick-name">
              <span class="pick-no">${h.horse_no}</span>${h.horse_name}
            </span>
            <span class="pick-sub">
              ${h.win_prob?.toFixed(1) || "—"}%  $${h.sim_odds || "—"}
            </span>
          </div>
        </td>`).join("")}
      ${"<td>—</td>".repeat(Math.max(0, 4 - top4.length))}
      <td>${vals.length
                ? vals.map(v => `<span class="badge badge-value">💰 ${v}</span>`).join(" ")
                : '<span class="muted">—</span>'}</td>`;
        tbody.appendChild(tr);
    });

    document.getElementById("summarySection").style.display = "block";
}

// ── Race detail cards ─────────────────────────────────────────────────────────
function buildDetails(races) {
    const sec = document.getElementById("detailSection");
    sec.innerHTML = "";

    races.forEach(race => {
        const horses = race.horses || [];
        const card = document.createElement("div");
        card.className = "race-card";
        card.id = `card-${race.race_no}`;

        const valueList = horses
            .filter(h => h.value_flag === "VALUE")
            .map(h => `💰 ${h.horse_name}`).join("  ");

        card.innerHTML = `
      <div class="race-card-header" onclick="toggleRaceCard(${race.race_no})">
        <div>
          <span class="race-card-title">Race ${race.race_no}  ${race.race_name || ""}</span>
          <span class="race-card-meta">
            · ${race.race_class || ""}  ${race.distance || ""}m  ${race.surface || ""}
          </span>
        </div>
        <div style="display:flex;gap:12px;align-items:center">
          ${valueList ? `<span style="font-size:12px">${valueList}</span>` : ""}
          <span class="race-card-toggle" id="tog-${race.race_no}">▼</span>
        </div>
      </div>
      <div class="race-card-body" id="body-${race.race_no}">
        <div class="table-wrap" style="margin:0;padding:0">
          <table>
            <thead>
              <tr>
                <th>Rnk</th><th>#</th><th>Horse</th><th>Jockey</th>
                <th>Draw</th><th>Form</th><th>Rtg</th><th>Mkt</th>
                <th>Draw</th><th>Jky</th><th>Trn</th><th>H2H</th>
                <th>Score</th><th>Win%</th><th>SimOdds</th>
                <th>MktOdds</th><th>EV</th><th>Flag</th>
              </tr>
            </thead>
            <tbody>
              ${horses.map((h, i) => buildHorseRow(h, i)).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
        sec.appendChild(card);
    });
}

function buildHorseRow(h, i) {
    const isTop4 = i < 4;
    const star = isTop4 ? '<span class="rank-star">★</span>' : "";
    const flagMap = { VALUE: "badge-value", OVER: "badge-over", FAIR: "badge-fair" };
    const flagLbl = { VALUE: "✅ VALUE", OVER: "❌ OVER", FAIR: "➖ FAIR" };
    const flag = h.value_flag || "—";
    const ev = h.ev != null ? parseFloat(h.ev) : null;
    const evHtml = ev != null
        ? `<span class="${ev >= 0 ? "ev-pos" : "ev-neg"}">${ev >= 0 ? "+" : ""}${ev.toFixed(3)}</span>`
        : "—";
    const prob = h.win_prob || 0;
    const probHtml = `
    <div class="prob-wrap">
      <div class="prob-bar">
        <div class="prob-fill" style="width:${Math.min(prob, 100)}%"></div>
      </div>
      <span class="prob-num">${prob.toFixed(1)}%</span>
    </div>`;
    const sc = v => {
        const n = parseFloat(v) || 0;
        const c = n >= 7 ? "score-high" : n >= 5 ? "score-mid" : "score-low";
        return `<span class="score-val ${c}">${n.toFixed(1)}</span>`;
    };

    return `
    <tr style="${isTop4 ? "background:#f9fff9" : ""}">
      <td>${star}<span class="rank-num">${i + 1}</span></td>
      <td><strong>${h.horse_no}</strong></td>
      <td style="font-weight:${isTop4 ? "700" : "400"}">${h.horse_name || "—"}</td>
      <td class="muted">${h.jockey || "—"}</td>
      <td>${h.draw || "—"}</td>
      <td>${sc(h.s_form)}</td>
      <td>${sc(h.s_rating)}</td>
      <td>${sc(h.s_market)}</td>
      <td>${sc(h.s_draw)}</td>
      <td>${sc(h.s_jockey)}</td>
      <td>${sc(h.s_trainer)}</td>
      <td>${sc(h.s_h2h)}</td>
      <td><strong>${parseFloat(h.composite || 0).toFixed(3)}</strong></td>
      <td>${probHtml}</td>
      <td><span class="odds-sim">$${h.sim_odds || "—"}</span></td>
      <td><span class="odds-mkt">${h.win_odds > 0 ? h.win_odds + "x" : "—"}</span></td>
      <td>${evHtml}</td>
      <td>${flag !== "—"
            ? `<span class="badge ${flagMap[flag] || ""}">${flagLbl[flag] || flag}</span>`
            : "—"}</td>
    </tr>`;
}

// ── Toggle collapse ───────────────────────────────────────────────────────────
function toggleRaceCard(raceNo) {
    const body = document.getElementById(`body-${raceNo}`);
    const tog = document.getElementById(`tog-${raceNo}`);
    if (!body) return;
    body.classList.toggle("open");
    tog.textContent = body.classList.contains("open") ? "▲" : "▼";
    if (body.classList.contains("open")) {
        document.getElementById(`card-${raceNo}`)
            .scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function fetchJSON(url) {
    try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return await r.json();
    } catch (e) {
        console.error("fetchJSON failed:", url, e);
        return null;
    }
}

function showError(msg) {
    document.getElementById("detailSection").innerHTML =
        `<div class="error-box"><strong>⚠ Error</strong><br>${msg}</div>`;
}