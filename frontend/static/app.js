/* =====================================================================
   ARGUS CONSOLE — application logic
   Charts are hand-rolled canvas primitives following the house rules:
   one axis per chart, recessive grids, direct labels + legend for >=2
   series, hover tooltips everywhere, luminance (not hue) carries
   magnitude, dash/shape carries identity.
   ===================================================================== */

const RAMP = ["#a8f5b0", "#58e07a", "#23b552", "#12813a"];
const INK = "#d8ffe0", INK2 = "#86c896", MUTED = "#3f7a50", GRID = "rgba(14,58,32,.55)";
const WARN = "#ffc857", CRIT = "#ff5f56";
const FONT = '11px "Share Tech Mono", monospace';

const $ = (s) => document.querySelector(s);
const fmt = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n * 100) / 100);
const usd = (n) => "$" + fmt(n);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

/* ------------------------------------------------ tooltip */
const tip = $("#tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.style.display = "block";
  const pad = 14;
  tip.style.left = Math.min(x + pad, innerWidth - 320) + "px";
  tip.style.top = Math.min(y + pad, innerHeight - 120) + "px";
}
function hideTip() { tip.style.display = "none"; }

/* ------------------------------------------------ matrix rain */
(function rain() {
  const c = $("#rain"), ctx = c.getContext("2d");
  const glyphs = "アイウエオカキクケコサシスセソタチツテト0123456789ABCDEF$¥€<>{}[]";
  let cols, drops;
  function size() {
    c.width = innerWidth; c.height = innerHeight;
    cols = Math.floor(c.width / 16);
    drops = Array.from({ length: cols }, () => Math.floor(Math.random() * -60));
  }
  size(); addEventListener("resize", size);
  setInterval(() => {
    ctx.fillStyle = "rgba(2,6,4,0.12)";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.font = '14px "Share Tech Mono", monospace';
    for (let i = 0; i < cols; i++) {
      const ch = glyphs[Math.floor(Math.random() * glyphs.length)];
      const y = drops[i] * 16;
      ctx.fillStyle = Math.random() < 0.08 ? "#a8f5b0" : "#1a7a3a";
      ctx.fillText(ch, i * 16, y);
      if (y > c.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }, 66);
})();

/* ------------------------------------------------ canvas helpers */
function setupCanvas(cv) {
  const dpr = devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth;
  const h = parseInt(cv.getAttribute("height")) || cv.clientHeight || 240;
  cv.width = w * dpr; cv.height = h * dpr;
  cv.style.height = h + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

/* multi-series line/area chart with crosshair tooltip */
function lineChart(cv, series, opts = {}) {
  const { ctx, w, h } = setupCanvas(cv);
  const P = { l: 52, r: 14, t: 12, b: 24 };
  const n = series[0].data.length;
  const all = series.flatMap(s => s.data);
  const ymax = (opts.ymax || Math.max(...all)) * 1.08 || 1;
  const X = i => P.l + (i / Math.max(1, n - 1)) * (w - P.l - P.r);
  const Y = v => h - P.b - (v / ymax) * (h - P.t - P.b);

  function draw(hoverI = -1) {
    ctx.clearRect(0, 0, w, h);
    ctx.font = FONT;
    // recessive grid + y labels
    ctx.strokeStyle = GRID; ctx.fillStyle = MUTED; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const v = ymax * g / 4, y = Y(v);
      ctx.beginPath(); ctx.moveTo(P.l, y); ctx.lineTo(w - P.r, y); ctx.stroke();
      ctx.fillText(opts.fmt ? opts.fmt(v) : fmt(v), 4, y + 3);
    }
    // x labels
    const step = Math.ceil(n / 8);
    for (let i = 0; i < n; i += step) {
      ctx.fillStyle = MUTED;
      ctx.fillText(opts.xlab ? opts.xlab(i) : "D" + (i + 1), X(i) - 8, h - 8);
    }
    // shock marker (labeled, not color-alone)
    if (opts.shockAt >= 0) {
      const x = X(opts.shockAt);
      ctx.save(); ctx.strokeStyle = WARN; ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(x, P.t); ctx.lineTo(x, h - P.b); ctx.stroke();
      ctx.fillStyle = WARN; ctx.fillText("⚠ SHOCK", x + 4, P.t + 10); ctx.restore();
    }
    // series
    series.forEach((s) => {
      ctx.save();
      ctx.strokeStyle = s.color; ctx.lineWidth = 2;
      if (s.dash) ctx.setLineDash(s.dash);
      ctx.shadowColor = s.color; ctx.shadowBlur = 6;
      ctx.beginPath();
      s.data.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
      ctx.stroke();
      if (s.fill) {
        ctx.shadowBlur = 0; ctx.globalAlpha = .12; ctx.fillStyle = s.color;
        ctx.lineTo(X(n - 1), Y(0)); ctx.lineTo(X(0), Y(0)); ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    });
    // direct labels at line ends, with vertical collision avoidance
    const placed = [];
    series.forEach((s) => {
      let ly = Y(s.data[n - 1]) - 6;
      while (placed.some(p => Math.abs(p - ly) < 13)) ly -= 13;
      placed.push(ly);
      ctx.fillStyle = s.color;
      ctx.fillText(s.name, Math.min(X(n - 1) - ctx.measureText(s.name).width, w - 90), ly);
    });
    // crosshair
    if (hoverI >= 0) {
      const x = X(hoverI);
      ctx.strokeStyle = "rgba(168,245,176,.4)";
      ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(x, P.t); ctx.lineTo(x, h - P.b); ctx.stroke();
      ctx.setLineDash([]);
      series.forEach(s => {
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.arc(x, Y(s.data[hoverI]), 4, 0, 7); ctx.fill();
        ctx.strokeStyle = "#020604"; ctx.lineWidth = 2; ctx.stroke();
      });
    }
  }
  draw();
  cv.onmousemove = (e) => {
    const r = cv.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left) - P.l) / ((w - P.l - P.r) / (n - 1)));
    if (i < 0 || i >= n) { hideTip(); draw(); return; }
    draw(i);
    const rows = series.map(s =>
      `<span style="color:${s.color}">■</span> ${s.name}: <b>${opts.fmt ? opts.fmt(s.data[i]) : fmt(s.data[i])}</b>`).join("<br>");
    showTip(`<b>${opts.xlab ? opts.xlab(i) : "DAY " + (i + 1)}</b><br>${rows}`, e.clientX, e.clientY);
  };
  cv.onmouseleave = () => { hideTip(); draw(); };
}

/* grouped histogram (two series, side-by-side thin bars) */
function histChart(cv, hist) {
  const { ctx, w, h } = setupCanvas(cv);
  const P = { l: 46, r: 24, t: 12, b: 26 };
  const bins = hist.bins;
  const ymaxRaw = Math.max(...hist.legit, ...hist.fraud);
  const ymax = ymaxRaw * 1.1;
  const bw = (w - P.l - P.r) / bins;
  const Y = v => h - P.b - (Math.sqrt(v) / Math.sqrt(ymax)) * (h - P.t - P.b); // sqrt scale, labeled

  const bars = [];
  function draw(hover = -1) {
    ctx.clearRect(0, 0, w, h); ctx.font = FONT;
    ctx.fillStyle = MUTED;
    ctx.fillText("√count", P.l + 6, P.t - 2);
    ctx.strokeStyle = GRID;
    [0, .25, .5, .75, 1].forEach(g => {
      const v = ymax * g, y = Y(v);
      ctx.beginPath(); ctx.moveTo(P.l, y); ctx.lineTo(w - P.r, y); ctx.stroke();
      ctx.fillText(fmt(v), 4, y + 3);
    });
    for (let i = 0; i <= 10; i += 2) {
      ctx.fillStyle = MUTED;
      ctx.fillText((i / 10).toFixed(1), P.l + (i / 10) * (w - P.l - P.r) - 6, h - 8);
    }
    bars.length = 0;
    for (let i = 0; i < bins; i++) {
      const x = P.l + i * bw;
      // legit: dim fill; fraud: bright fill — plus 2px surface gap between pair
      const wl = Math.max(2, bw / 2 - 3);
      const hL = h - P.b - Y(hist.legit[i]), hF = h - P.b - Y(hist.fraud[i]);
      ctx.fillStyle = i === hover ? "#2ea45c" : RAMP[3];
      ctx.fillRect(x + 1, Y(hist.legit[i]), wl, hL);
      ctx.fillStyle = i === hover ? "#c1ffd0" : RAMP[0];
      ctx.fillRect(x + 1 + wl + 2, Y(hist.fraud[i]), wl, hF);
      bars.push({ x, i });
    }
  }
  draw();
  cv.onmousemove = e => {
    const r = cv.getBoundingClientRect();
    const i = Math.floor(((e.clientX - r.left) - P.l) / bw);
    if (i < 0 || i >= bins) { hideTip(); draw(); return; }
    draw(i);
    const lo = (i / bins).toFixed(2), hi = ((i + 1) / bins).toFixed(2);
    showTip(`<b>RISK ${lo}–${hi}</b><br><span style="color:${RAMP[3]}">■</span> legitimate: <b>${hist.legit[i]}</b><br><span style="color:${RAMP[0]}">■</span> fraud: <b>${hist.fraud[i]}</b>`, e.clientX, e.clientY);
  };
  cv.onmouseleave = () => { hideTip(); draw(); };
}

/* horizontal labeled bars */
function hbars(el, rows, { bright = () => false, denomFmt = (r) => "" } = {}) {
  el.innerHTML = rows.map((r) => `
    <div class="hbar ${bright(r) ? "bright" : ""}" title="">
      <div class="lbl"><span>${r.label}</span><b>${r.value_label}</b></div>
      <div class="track"><div class="fill" style="width:${Math.max(1.5, r.pct * 100)}%"></div></div>
    </div>`).join("");
  [...el.querySelectorAll(".hbar")].forEach((b, i) => {
    b.onmousemove = e => showTip(`<b>${rows[i].label}</b><br>${rows[i].tip || rows[i].value_label}`, e.clientX, e.clientY);
    b.onmouseleave = hideTip;
  });
}

/* half-donut gauge */
function gauge(cv, value) {
  const ctx = cv.getContext("2d");
  const w = cv.width, h = cv.height, cx = w / 2, cy = h - 8, R = 82;
  ctx.clearRect(0, 0, w, h);
  ctx.lineWidth = 13; ctx.lineCap = "butt";
  ctx.strokeStyle = "rgba(14,58,32,.8)";
  ctx.beginPath(); ctx.arc(cx, cy, R, Math.PI, 2 * Math.PI); ctx.stroke();
  const col = value >= 0.65 ? CRIT : value >= 0.45 ? WARN : RAMP[1];
  ctx.strokeStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 10;
  ctx.beginPath(); ctx.arc(cx, cy, R, Math.PI, Math.PI * (1 + value)); ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.font = FONT; ctx.fillStyle = MUTED;
  ctx.fillText("0", cx - R - 4, cy + 2); ctx.fillText("1", cx + R - 4, cy + 2);
  $("#gauge-label").textContent = value.toFixed(2);
  $("#gauge-label").style.color = col;
}

/* sparkline for tiles */
function sparkSVG(data, w = 72, h = 22, color = RAMP[1]) {
  const max = Math.max(...data) || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - (v / max) * (h - 3) - 1}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".9"/></svg>`;
}

/* =====================================================================
   VIEWS
   ===================================================================== */
const state = { overview: null, cases: [], samples: null, graphData: null };

async function loadOverview() {
  const o = await api("/api/overview");
  state.overview = o;
  const s = o.scores, w = o.world, daily = o.daily;
  const prevented = daily.reduce((a, d) => a + d.prevented, 0);
  $("#kpi-tiles").innerHTML = [
    { k: "AUC-ROC", v: s.auc_roc.toFixed(3), s: "ensemble ranking power", spark: daily.map(d => d.caught) },
    { k: "PR-AUC", v: s.pr_auc.toFixed(3), s: "precision–recall area" },
    { k: "RECALL @ 1% FPR", v: (s.recall_at_1pct_fpr * 100).toFixed(1) + "%", s: "fraud caught at 1% noise" },
    { k: "LOSS PREVENTED", v: usd(prevented), s: `of ${usd(w.fraud_volume_usd)} attempted`, spark: daily.map(d => d.prevented) },
    { k: "RING DETECTION F1", v: o.ring_detection.f1.toFixed(2), s: `${o.ring_detection.entities_flagged} entities flagged / P=${o.ring_detection.precision.toFixed(2)}` },
    { k: "DECISION LATENCY", v: o.latency_ms.p99 + "ms", s: "p99 · in-process hot path" },
    { k: "FRAUD PRESSURE", v: (w.fraud_rate * 100).toFixed(1) + "%", s: `${w.fraud_txns}/${w.transactions} txns`, warn: true },
    { k: "OPEN CASES", v: o.cases_open, s: "agentic investigations", warn: false },
  ].map(t => `<div class="tile ${t.warn ? "warn" : ""}">
      <div class="k">${t.k}</div><div class="v">${t.v}</div><div class="s">${t.s}</div>
      ${t.spark ? sparkSVG(t.spark) : ""}</div>`).join("");

  lineChart($("#chart-daily"), [
    { name: "VOLUME", data: daily.map(d => d.volume), color: RAMP[3], fill: true },
    { name: "FRAUD ATTEMPTED", data: daily.map(d => d.fraud_volume), color: RAMP[1], dash: [6, 4] },
    { name: "PREVENTED", data: daily.map(d => d.prevented), color: RAMP[0] },
  ], { fmt: usd });
  $("#legend-daily").innerHTML = `
    <span><i class="sw" style="background:${RAMP[3]}"></i>TOTAL VOLUME (area)</span>
    <span><i class="sw" style="background:${RAMP[1]}"></i>FRAUD ATTEMPTED (dashed)</span>
    <span><i class="sw" style="background:${RAMP[0]}"></i>LOSS PREVENTED (solid)</span>`;

  histChart($("#chart-hist"), o.risk_histogram);
  $("#legend-hist").innerHTML = `
    <span><i class="sw" style="background:${RAMP[3]}"></i>LEGITIMATE (dim, left)</span>
    <span><i class="sw" style="background:${RAMP[0]}"></i>FRAUD (bright, right)</span>
    <span style="color:${MUTED}">√ COUNT SCALE — SEPARATION = MODEL POWER</span>`;

  hbars($("#chart-arch"), Object.entries(o.per_archetype).map(([k, v]) => ({
    label: k.replace(/_/g, " ").toUpperCase(),
    value_label: `${v.caught}/${v.attempts} (${v.rate == null ? "—" : Math.round(v.rate * 100) + "%"})`,
    pct: v.rate || 0,
    tip: `caught ${v.caught} of ${v.attempts} attempts`,
  })), { bright: r => r.pct >= 0.95 });

  const totalActions = Object.values(o.action_mix).reduce((a, b) => a + b, 0);
  hbars($("#chart-actions"), ["approve", "monitor", "step_up", "review", "decline"]
    .filter(a => o.action_mix[a]).map(a => ({
      label: a.toUpperCase(),
      value_label: `${o.action_mix[a]} (${Math.round(o.action_mix[a] / totalActions * 100)}%)`,
      pct: o.action_mix[a] / totalActions,
      tip: `${o.action_mix[a]} decisions`,
    })));

  $("#tbl-communities").innerHTML = `<tr><th>COMMUNITY</th><th class="num">SIZE</th><th class="num">SHARED DEV</th><th class="num">RING OVERLAP</th><th class="num">MAX RISK</th></tr>` +
    o.communities.filter(c => c.size > 3).slice(0, 6).map(c => `<tr>
      <td>${c.community_id}</td><td class="num">${c.size}</td><td class="num">${c.shared_devices}</td>
      <td class="num">${Math.round(c.ring_overlap * 100)}%</td>
      <td class="num"><span class="riskbar"><i style="width:${c.max_risk * 100}%"></i></span>${c.max_risk.toFixed(2)}</td></tr>`).join("");

  const feed = await api("/api/feed?limit=14&min_risk=0");
  $("#tbl-feed").innerHTML = `<tr><th>TXN</th><th>ENTITY</th><th class="num">AMOUNT</th><th>GEO</th><th>RISK</th><th>ACTION</th><th>TRUTH</th><th>TOP SIGNALS</th></tr>` +
    feed.decisions.map(d => `<tr>
      <td>${d.transaction_id}</td><td>${d.consumer_id}</td>
      <td class="num">${usd(d.amount)}</td><td>${d.geo}</td>
      <td><span class="riskbar"><i style="width:${d.risk_score * 100}%"></i></span>${d.risk_score.toFixed(2)}</td>
      <td><span class="badge ${d.decision}">${d.decision.toUpperCase()}</span></td>
      <td><span class="badge ${d.is_fraud ? "fraud" : "legit"}">${d.is_fraud ? "⚠ FRAUD" : "OK"}</span></td>
      <td style="font-size:10px;color:${MUTED}">${d.reasons.join(" · ")}</td></tr>`).join("");
}

/* ------------------------------------------------ graph explorer */
const SHAPES = { consumer: "circle", device: "square", merchant: "diamond" };
function nodeColor(n) {
  if (n.type === "consumer") return RAMP[0];
  if (n.type === "device") return RAMP[1];
  return RAMP[2];
}

async function loadGraphView() {
  if (!state.samples) state.samples = await api("/api/entities/sample");
  const sel = $("#graph-entity");
  if (!sel.options.length) {
    const s = state.samples;
    sel.innerHTML =
      `<optgroup label="RING MEMBERS">` + s.ring_members.map(id => `<option>${id}</option>`).join("") + `</optgroup>` +
      `<optgroup label="FARM DEVICES">` + s.devices.map(id => `<option>${id}</option>`).join("") + `</optgroup>` +
      `<optgroup label="LEGIT CONSUMERS">` + s.legit.map(id => `<option>${id}</option>`).join("") + `</optgroup>`;
  }
  if (!state.graphData) renderGraph(sel.value);
}

async function renderGraph(entityId) {
  const depth = $("#graph-depth").value;
  const g = await api(`/v1/entities/${entityId}/graph?depth=${depth}&limit=120`);
  state.graphData = g;
  $("#graph-stats").textContent = `⟨ ${g.nodes.length} NODES · ${g.edges.length} EDGES · CENTER ${g.center} ⟩`;

  const cv = $("#graph-canvas");
  const dpr = devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight || 520;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // deterministic-ish initial layout + force simulation
  const nodes = g.nodes.map((n, i) => ({
    ...n,
    x: W / 2 + Math.cos(i * 2.399) * (60 + (i % 7) * 38),
    y: H / 2 + Math.sin(i * 2.399) * (50 + (i % 5) * 38),
    vx: 0, vy: 0,
  }));
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const edges = g.edges.filter(e => byId[e.source] && byId[e.target]);
  const center = byId[g.center];
  let selected = center, tick = 0;

  function step() {
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue;
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
        if (d < 320) { const f = 2600 / d2; a.vx += dx / d * f; a.vy += dy / d * f; }
      }
      a.vx += (W / 2 - a.x) * 0.0022; a.vy += (H / 2 - a.y) * 0.0022;
    }
    const rest = Math.min(W, H) / 4.6;
    for (const e of edges) {
      const a = byId[e.source], b = byId[e.target];
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      const f = (d - rest) * 0.015;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of nodes) {
      n.x = Math.max(18, Math.min(W - 18, n.x + (n.vx *= 0.82)));
      n.y = Math.max(18, Math.min(H - 18, n.y + (n.vy *= 0.82)));
    }
  }

  function drawShape(n, r) {
    ctx.beginPath();
    if (SHAPES[n.type] === "square") ctx.rect(n.x - r, n.y - r, r * 2, r * 2);
    else if (SHAPES[n.type] === "diamond") {
      ctx.moveTo(n.x, n.y - r * 1.25); ctx.lineTo(n.x + r * 1.25, n.y);
      ctx.lineTo(n.x, n.y + r * 1.25); ctx.lineTo(n.x - r * 1.25, n.y); ctx.closePath();
    } else ctx.arc(n.x, n.y, r, 0, 7);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(35,181,82,.28)"; ctx.lineWidth = 1;
    for (const e of edges) {
      const a = byId[e.source], b = byId[e.target];
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.font = '9px "Share Tech Mono", monospace';
    for (const n of nodes) {
      const risky = n.risk >= 0.55 || n.ring_id;
      const r = n === center ? 11 : 5 + n.risk * 6;
      if (risky) {  // halo = high risk (also labeled in dossier, not color-alone)
        ctx.save(); ctx.shadowColor = RAMP[0]; ctx.shadowBlur = 14;
        ctx.fillStyle = "rgba(168,245,176,.9)"; drawShape(n, r + 1.5); ctx.fill(); ctx.restore();
      }
      ctx.fillStyle = nodeColor(n);
      ctx.globalAlpha = 0.35 + n.risk * 0.65;
      drawShape(n, r); ctx.fill();
      ctx.globalAlpha = 1;
      if (n === selected) {
        ctx.strokeStyle = INK; ctx.lineWidth = 1.4;
        drawShape(n, r + 4); ctx.stroke();
      }
      if (n === center || risky || n === selected) {
        ctx.fillStyle = n === center ? INK : INK2;
        ctx.fillText(n.id, n.x + r + 3, n.y + 3);
      }
    }
  }

  function loop() {
    if (tick++ < 180) { step(); draw(); requestAnimationFrame(loop); } else draw();
  }
  loop();

  function selectNode(n) {
    selected = n; draw();
    const rows = Object.entries(n)
      .filter(([k]) => !["x", "y", "vx", "vy"].includes(k))
      .map(([k, v]) => `${k.padEnd(20, " ")} ${v === null ? "—" : v}`).join("\n");
    $("#graph-node-info").textContent = rows +
      (n.ring_id ? "\n\n⚠ CONFIRMED RING AFFILIATION — PRIORITY TARGET" : "");
  }
  selectNode(center);

  function pick(e) {
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    return nodes.find(n => Math.hypot(n.x - mx, n.y - my) < 14);
  }
  cv.onmousemove = (e) => {
    const n = pick(e);
    if (n) showTip(`<b>${n.id}</b> · ${n.type.toUpperCase()}<br>risk <b>${n.risk.toFixed(2)}</b>${n.ring_id ? `<br><span style="color:${CRIT}">⚠ RING ${n.ring_id}</span>` : ""}`, e.clientX, e.clientY);
    else hideTip();
  };
  cv.onclick = (e) => {
    const n = pick(e);
    if (n) selectNode(n);
  };
}

/* ------------------------------------------------ cases */
async function loadCases() {
  const data = await api("/api/cases");
  state.cases = data.cases;
  $("#case-count").textContent = `${data.cases.length} ACTIVE`;
  $("#tbl-cases").innerHTML = `<tr><th>CASE</th><th>ENTITY</th><th class="num">RISK</th><th>ARCHETYPE</th><th>VERDICT</th></tr>` +
    data.cases.map(c => `<tr data-case="${c.case_id}">
      <td>${c.case_id}</td><td>${c.entity_id}</td>
      <td class="num">${c.risk_score.toFixed(2)}</td>
      <td style="font-size:10px">${(c.archetype || "—").replace(/_/g, " ")}</td>
      <td><span class="badge ${c.verdict === "confirmed_fraud" ? "fraud" : c.verdict === "cleared" ? "legit" : "review"}">${c.verdict.replace(/_/g, " ").toUpperCase()}</span></td></tr>`).join("");
  [...document.querySelectorAll("#tbl-cases tr[data-case]")].forEach(tr => {
    tr.onclick = () => {
      document.querySelectorAll("#tbl-cases tr").forEach(r => r.classList.remove("sel"));
      tr.classList.add("sel");
      openCase(tr.dataset.case);
    };
  });
  if (data.cases.length) openCase(data.cases[0].case_id);
}

async function openCase(id) {
  const c = await api(`/api/cases/${id}`);
  $("#case-title").textContent = `${c.case_id} · ${c.transaction_id} · ${c.agents_dispatched} AGENTS`;
  $("#case-verdict").innerHTML = `<div class="verdict-banner ${c.verdict}">
    ${c.verdict === "confirmed_fraud" ? "⚠ " : ""}VERDICT: ${c.verdict.replace(/_/g, " ").toUpperCase()}
    · QUEUE ${c.queue.toUpperCase()} · RISK ${c.risk_score.toFixed(2)}</div>`;
  $("#case-trace").innerHTML = c.trace.map(t => `
    <div class="trace-step">
      <div class="agent">[${String(t.step).padStart(2, "0")}] ${t.agent_name.toUpperCase()}
        ${t.verdict === "supporting" ? `<span style="color:${CRIT}">▲ SUPPORTING</span>` : t.verdict === "refuting" ? `<span style="color:${RAMP[1]}">▼ REFUTING</span>` : ""}</div>
      <div class="finding">${t.finding}</div>
      <div class="meta">${t.tools.map(x => `<span class="tooltag">${x}</span>`).join("")}
        <span class="conf">CONF ${t.confidence.toFixed(2)}</span></div>
    </div>`).join("");
  $("#case-report").textContent = c.report;
}

/* ------------------------------------------------ simulation */
async function loadSim() {
  const s = await api("/v1/simulations");
  const sel = $("#sim-scenario");
  if (!sel.options.length) {
    sel.innerHTML = s.scenarios.map(x => `<option value="${x.name}">${x.name.replace(/_/g, " ").toUpperCase()}</option>`).join("");
  }
}

async function runSim() {
  $("#sim-status").textContent = "⟨ EXECUTING DETERMINISTIC CAMPAIGN… ⟩";
  const name = $("#sim-scenario").value, seed = $("#sim-seed").value || 1337;
  const r = await api(`/v1/simulations/${name}:run?seed=${seed}`, { method: "POST" });
  const sum = r.summary, tl = r.timeline;
  $("#sim-status").textContent = `⟨ COMPLETE · SEED ${r.seed} · REPLAYABLE ⟩`;
  $("#sim-desc").textContent = r.scenario.description.toUpperCase();
  $("#sim-tiles").innerHTML = [
    { k: "DETECTION RATE", v: (sum.detection_rate * 100).toFixed(1) + "%", s: `${sum.fraud_caught}/${sum.fraud_attempts} attacks stopped` },
    { k: "LOSS PREVENTED", v: usd(sum.loss_prevented_usd), s: "blocked before settlement" },
    { k: "LOSS INCURRED", v: usd(sum.loss_incurred_usd), s: "slipped through", warn: true },
    { k: "AVG DAILY FPR", v: (sum.avg_daily_fpr * 100).toFixed(2) + "%", s: "friction on legit traffic" },
    { k: "SHOCK", v: "D" + r.scenario.shock_day, s: `${r.scenario.shock_type} ×${r.scenario.multiplier}`, warn: true },
  ].map(t => `<div class="tile ${t.warn ? "warn" : ""}"><div class="k">${t.k}</div><div class="v">${t.v}</div><div class="s">${t.s}</div></div>`).join("");

  lineChart($("#chart-sim"), [
    { name: "FRAUD ATTEMPTS", data: tl.map(d => d.fraud), color: RAMP[1], dash: [6, 4] },
    { name: "CAUGHT", data: tl.map(d => d.caught), color: RAMP[0] },
    { name: "MISSED", data: tl.map(d => d.missed), color: RAMP[3] },
  ], { shockAt: tl.findIndex(d => d.shock), fmt: v => Math.round(v) });
  $("#legend-sim").innerHTML = `
    <span><i class="sw" style="background:${RAMP[1]}"></i>FRAUD ATTEMPTS (dashed)</span>
    <span><i class="sw" style="background:${RAMP[0]}"></i>CAUGHT (solid bright)</span>
    <span><i class="sw" style="background:${RAMP[3]}"></i>MISSED (solid dim)</span>
    <span class="crit">⚠ AMBER DASH = SHOCK INJECTION</span>`;

  $("#tbl-sim").innerHTML = `<tr><th>DAY</th><th class="num">TXNS</th><th class="num">FRAUD</th><th class="num">CAUGHT</th><th class="num">DETECTION</th><th class="num">FPR</th><th class="num">PREVENTED</th><th class="num">INCURRED</th><th></th></tr>` +
    tl.map(d => `<tr>
      <td>D${d.day}</td><td class="num">${d.txns}</td><td class="num">${d.fraud}</td><td class="num">${d.caught}</td>
      <td class="num">${d.detection_rate == null ? "—" : Math.round(d.detection_rate * 100) + "%"}</td>
      <td class="num">${(d.fpr * 100).toFixed(2)}%</td>
      <td class="num">${usd(d.loss_prevented)}</td><td class="num">${usd(d.loss_incurred)}</td>
      <td>${d.shock ? `<span class="badge review">⚠ SHOCK</span>` : ""}</td></tr>`).join("");
}

/* ------------------------------------------------ decision console */
async function loadConsole() {
  if (!state.samples) state.samples = await api("/api/entities/sample");
  const s = state.samples;
  if (!$("#ev-consumer").options.length) {
    $("#ev-consumer").innerHTML =
      `<optgroup label="RING MEMBERS (hostile)">` + s.ring_members.map(id => `<option>${id}</option>`).join("") + `</optgroup>` +
      `<optgroup label="LEGIT">` + s.legit.map(id => `<option>${id}</option>`).join("") + `</optgroup>`;
    $("#ev-merchant").innerHTML = s.merchants.map(id => `<option>${id}</option>`).join("");
    $("#ev-device").innerHTML =
      `<optgroup label="FARM / EMULATOR">` + s.devices.map(id => `<option>${id}</option>`).join("") + `</optgroup>` +
      `<optgroup label="AUTO (entity's own)"><option value="">auto</option></optgroup>`;
    const pol = await api("/api/policies");
    $("#tbl-policies").innerHTML = `<tr><th>ID</th><th>POLICY</th><th class="num">VER</th><th>ACTION</th></tr>` +
      pol.policies.map(p => `<tr><td>${p.id}</td><td title="${p.desc}">${p.name}</td><td class="num">v${p.version}</td><td><span class="badge ${p.action}">${p.action.toUpperCase()}</span></td></tr>`).join("");
  }
}

async function evaluateTxn(e) {
  e.preventDefault();
  const consumer = $("#ev-consumer").value;
  let device = $("#ev-device").value;
  if (!device) device = "dev_0000_0";
  const body = {
    consumer_id: consumer, merchant_id: $("#ev-merchant").value, device_id: device,
    amount: parseFloat($("#ev-amount").value) || 100, geo: $("#ev-geo").value,
  };
  const d = await api("/v1/transactions:evaluate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  gauge($("#gauge"), d.risk_score);
  $("#ev-latency").textContent = `ENGINE ${d.engine} · ${d.latency_ms}ms · CONF ${d.confidence}`;
  const a = $("#ev-action");
  a.textContent = d.decision.toUpperCase();
  a.className = "action-badge " + d.decision;
  $("#ev-explanation").textContent = d.explanation;
  $("#ev-reasons").innerHTML = d.action_reasons.map(r =>
    `<span class="reason-chip ${r.startsWith("policy:") ? "policy" : ""}">${r}</span>`).join("");
  hbars($("#ev-models"), Object.entries(d.model_scores).map(([k, v]) => ({
    label: `${k.toUpperCase()} <span style="color:${MUTED};font-size:9px">${v.version}</span>`,
    value_label: v.score.toFixed(3),
    pct: v.score,
    tip: `weight ${v.weight} · ` + d.attributions[k].map(x => `${x.feature}:${x.weight}`).join(", "),
  })), { bright: r => r.pct > 0.6 });
  $("#ev-cf").textContent = d.counterfactuals.length
    ? d.counterfactuals.map(c => `▸ ${c.narrative} (Δ ${c.risk_delta})`).join("\n")
    : "No single-feature intervention flips this decision.";
}

/* ------------------------------------------------ shell */
document.querySelectorAll("#nav button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#nav button").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".view").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    $("#view-" + b.dataset.view).classList.add("active");
    ({ overview: loadOverview, graph: loadGraphView, cases: loadCases, sim: loadSim, console: loadConsole })[b.dataset.view]();
  };
});
$("#graph-load").onclick = () => renderGraph($("#graph-entity").value);
$("#graph-entity").onchange = () => renderGraph($("#graph-entity").value);
$("#sim-run").onclick = runSim;
$("#eval-form").onsubmit = evaluateTxn;

setInterval(() => {
  $("#sl-clock").textContent = new Date().toISOString().replace("T", " ").slice(0, 19) + "Z";
}, 1000);

(async function boot() {
  try {
    const h = await api("/api/health");
    $("#sysstat").innerHTML = `<span class="dot"></span> ${h.status} · v${h.version} · ${h.decisions_served} DECISIONS SERVED`;
    await loadOverview();
  } catch (err) {
    $("#sysstat").innerHTML = `<span class="dot" style="background:${CRIT}"></span> LINK FAILURE — ${err.message}`;
  }
})();
