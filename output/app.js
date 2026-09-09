/* ═══════════════════════════════════════════════════════════
   SkyGuard AI — Live Dashboard
   Data is baked into the page by Flask at load time.
   No fetch() calls — zero CORS, zero race conditions.
   ═══════════════════════════════════════════════════════════ */

const NCR_STATIONS   = ['AWS_DELHI','AWS_GURGAON','AWS_NOIDA'];
const ISO_STATIONS   = ['AWS_KOCHI','AWS_BLR','AWS_SHIMLA','AWS_JAISALMER'];

const STATION_DISPLAY = {
  AWS_DELHI:    'Delhi',
  AWS_GURGAON:  'Gurgaon',
  AWS_NOIDA:    'Noida',
  AWS_KOCHI:    'Kochi',
  AWS_BLR:      'Bangalore',
  AWS_SHIMLA:   'Shimla',
  AWS_JAISALMER:'Jaisalmer',
};

const PLOTLY_DARK = {
  paper_bgcolor:'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font:{ color:'#9ca3af', family:'Inter, sans-serif', size:11 },
  xaxis:{ gridcolor:'rgba(255,255,255,0.05)', linecolor:'rgba(255,255,255,0.08)', zerolinecolor:'rgba(255,255,255,0.05)' },
  yaxis:{ gridcolor:'rgba(255,255,255,0.05)', linecolor:'rgba(255,255,255,0.08)', zerolinecolor:'rgba(255,255,255,0.05)' },
  margin:{ t:10, r:16, b:40, l:44 },
  legend:{ bgcolor:'rgba(0,0,0,0)', font:{ size:10 } },
  hoverlabel:{ bgcolor:'#1e2436', bordercolor:'rgba(255,255,255,0.15)', font:{ color:'#e8eaf0', family:'Inter' } },
};

let g_results        = [];
let g_health         = [];
let g_metrics        = {};
let g_currentStation = null;
let g_lastRunUtc     = null;
let g_pollTimer      = null;
let g_showTier1      = false;  // Low-severity alerts hidden by default

// Severity ordering for filter
const SEV_ORDER = { Critical:4, High:3, Medium:2, Low:1, Normal:0 };
function isAlertVisible(r) {
  if (!r.is_flagged) return false;
  if (!g_showTier1 && r.severity === 'Low') return false;
  return true;
}

/* ── TAB SWITCHING ──────────────────────────────────────── */
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('content-' + name).classList.add('active');
  if (name === 'station' && !g_currentStation && g_results.length) {
    selectStation(NCR_STATIONS[0]);
  }
}

/* ── DATA LOADING ───────────────────────────────────────── */
function loadData() {
  // Flask bakes the current pipeline results into window.__SKYGUARD__
  // before sending the HTML. We just read it — no fetch() needed.
  const json = window.__SKYGUARD__;
  window.__SKYGUARD__ = null;

  if (!json) {
    setStatus('error', '❌ No data — try refreshing the page');
    return;
  }

  if (json.status === 'initializing' || json.status === 'refreshing') {
    setStatus('initializing', '⏳ Pipeline running for the first time (~60s)…');
    // Poll the status endpoint every 3 s until the pipeline finishes,
    // then reload the page to get the freshly baked data.
    setTimeout(waitForReady, 3000);
    return;
  }

  if (json.status === 'error') {
    setStatus('error', '❌ Pipeline error — ' + (json.message || 'check server logs'));
    return;
  }

  // ── Happy path: render everything ────────────────────────────────
  g_lastRunUtc = json.last_run_utc;
  g_results    = json.detection_results || [];
  g_health     = json.sensor_health     || [];
  g_metrics    = json.evaluation_metrics || {};

  const dr      = json.data_range || {};
  const lastRun = json.last_run_utc ? new Date(json.last_run_utc).toLocaleString() : '—';
  const nextRun = json.next_run_utc ? new Date(json.next_run_utc).toLocaleString() : '—';

  setStatus('live', `🟢 Live · Updated ${lastRun}`);
  document.getElementById('footer-timestamp').textContent =
    `Data: ${dr.start ? dr.start.slice(0,10) : '?'} → ${dr.end ? dr.end.slice(0,10) : '?'} · Next auto-refresh: ${nextRun}`;

  renderOverview();
  buildStationSidebar();
  renderExplainability();
  if (g_currentStation) selectStation(g_currentStation);

  // Schedule an automatic page reload when the next pipeline run is due.
  // This means the dashboard updates itself without any user action.
  if (json.next_run_utc) {
    const msUntilNext = new Date(json.next_run_utc) - Date.now() + 10_000; // +10s buffer
    if (msUntilNext > 0 && msUntilNext < 8 * 3600 * 1000) {
      setTimeout(() => window.location.reload(), msUntilNext);
    }
  }
}

async function waitForReady() {
  // Called only during the first-run initializing state.
  // Polls /api/status (same-origin, no CORS) until ready, then reloads.
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    if (data.status === 'ready') {
      window.location.reload();
    } else {
      setTimeout(waitForReady, 3000);
    }
  } catch(e) {
    // Server may still be starting — try again in 5s
    setTimeout(waitForReady, 5000);
  }
}


function setStatus(type, text) {
  const pill = document.getElementById('data-status');
  const dot  = pill.querySelector('.pulse-dot');
  const span = document.getElementById('data-status-text');
  span.textContent = text;
  pill.style.background = type === 'live'          ? 'rgba(52,211,153,0.10)'
                        : type === 'initializing'  ? 'rgba(251,191,36,0.10)'
                        : 'rgba(248,113,113,0.10)';
  pill.style.borderColor = type === 'live'         ? 'rgba(52,211,153,0.25)'
                         : type === 'initializing' ? 'rgba(251,191,36,0.25)'
                         : 'rgba(248,113,113,0.25)';
  dot.style.background   = type === 'live'         ? '#34d399'
                         : type === 'initializing' ? '#fbbf24'
                         : '#f87171';
  span.style.color       = type === 'live'         ? '#34d399'
                         : type === 'initializing' ? '#fbbf24'
                         : '#f87171';
}

/* ═══════════════════════════════════════════════════════════
   TAB 1: NETWORK OVERVIEW
   ═══════════════════════════════════════════════════════════ */
function renderOverview() {
  const allFlagged = g_results.filter(r => r.is_flagged);
  const visible    = allFlagged.filter(r => isAlertVisible(r));
  const total      = g_results.length;
  const pct        = total ? (allFlagged.length / total * 100).toFixed(1) : '0';

  const t2  = g_metrics.tier2_confirmed_alert || {};
  const far2 = t2.fp != null && t2.tn != null
    ? ((t2.fp / (t2.fp + t2.tn)) * 100).toFixed(2) + '%' : '—';

  el('kpi-total-val').textContent     = total.toLocaleString();
  el('kpi-anomalies-val').textContent = allFlagged.length.toLocaleString();
  el('kpi-anomalies-pct').textContent = pct + '% of readings';
  el('kpi-precision-val').textContent = t2.precision != null
    ? (t2.precision * 100).toFixed(1) + '%' : '—';
  el('kpi-far-val').textContent = far2;
  el('alert-count-badge').textContent = visible.length + ' alerts';

  // Update toggle button label
  const toggleBtn = el('tier1-toggle');
  if (toggleBtn) {
    toggleBtn.textContent = g_showTier1 ? '🔽 Hide Low-Severity' : '🔼 Show Low-Severity';
    toggleBtn.style.opacity = g_showTier1 ? '1' : '0.6';
  }

  renderRootCausePie(visible);
  renderHealthBar();
  renderAlertFeed(visible);
}

function toggleTier1() {
  g_showTier1 = !g_showTier1;
  renderOverview();
}

function renderRootCausePie(flagged) {
  const counts = {};
  flagged.forEach(r => {
    const k = r.root_cause_label || 'Unknown';
    counts[k] = (counts[k] || 0) + 1;
  });
  const labels = Object.keys(counts);
  const values = Object.values(counts);
  const colors = ['#5b8df8','#a78bfa','#34d399','#fbbf24','#f87171','#38bdf8','#fb923c'];

  Plotly.newPlot('chart-root-cause', [{
    type:'pie', labels, values, hole:0.52,
    marker:{ colors, line:{ color:'rgba(0,0,0,0.4)', width:1.5 } },
    textinfo:'percent',
    hovertemplate:'<b>%{label}</b><br>%{value} alerts (%{percent})<extra></extra>',
    textfont:{ size:10, color:'#e8eaf0' },
  }], {
    ...PLOTLY_DARK, height:280, showlegend:true,
    legend:{ font:{ size:9, color:'#9ca3af' }, x:1, y:0.5 },
    annotations:[{ text:`<b>${flagged.length}</b><br>alerts`, showarrow:false,
      font:{ color:'#e8eaf0', size:13 }, x:0.5, y:0.5 }],
  }, { responsive:true, displayModeBar:false });
}

function renderHealthBar() {
  const sorted = [...g_health].sort((a,b) => b.current_anomaly_rate - a.current_anomaly_rate);
  const stations = sorted.map(h => STATION_DISPLAY[h.station_id] || h.station_id);
  const rates    = sorted.map(h => +(parseFloat(h.current_anomaly_rate) * 100).toFixed(1));
  const colors   = rates.map(r =>
    r >= 80 ? '#f87171' : r >= 20 ? '#fbbf24' : r >= 10 ? '#38bdf8' : '#34d399');

  Plotly.newPlot('chart-health', [{
    type:'bar', x:rates, y:stations, orientation:'h',
    marker:{ color:colors, opacity:0.85 },
    text:rates.map(r => r + '%'), textposition:'outside',
    textfont:{ size:10, color:'#9ca3af' },
    hovertemplate:'<b>%{y}</b>: %{x}% anomaly rate<extra></extra>',
  }], {
    ...PLOTLY_DARK, height:280,
    xaxis:{ ...PLOTLY_DARK.xaxis, title:{ text:'Anomaly Rate (%)', font:{ size:10 } } },
    bargap:0.35,
  }, { responsive:true, displayModeBar:false });
}

function renderAlertFeed(visible) {
  const feed = el('alert-feed');
  const sorted = [...visible]
    .sort((a,b) => (SEV_ORDER[b.severity]||0) - (SEV_ORDER[a.severity]||0)
               || new Date(b.timestamp) - new Date(a.timestamp))
    .slice(0, 80);

  const tier1Hidden = g_results.filter(r => r.is_flagged && r.severity === 'Low').length;
  const banner = !g_showTier1 && tier1Hidden > 0
    ? `<div style="text-align:center;padding:8px;font-size:0.75rem;color:#6b7280;border-bottom:1px solid rgba(255,255,255,0.06)">
        ${tier1Hidden} Low-severity alerts hidden · <button onclick="toggleTier1()" style="background:none;border:none;color:#5b8df8;cursor:pointer;font-size:0.75rem;">Show all</button>
      </div>` : '';

  feed.innerHTML = banner + sorted.map(r => {
    const sev   = r.severity || 'Low';
    const ts    = (r.timestamp||'').slice(0,16).replace('T',' ');
    const cause = r.root_cause_label || 'Unknown';
    const name  = STATION_DISPLAY[r.station_id] || r.station_id;
    const expl  = r.explanation ? `<div class="alert-explanation">${r.explanation.split(' | ')[0]}</div>` : '';
    return `<div class="alert-item alert-item-${sev.toLowerCase()}">
      <div class="alert-item-header">
        <span class="alert-time">${ts}</span>
        <span class="alert-station">${name}</span>
        <span class="severity-badge sev-${sev.toLowerCase()}">${sev}</span>
      </div>
      <span class="alert-cause">${cause}</span>
      ${expl}
    </div>`;
  }).join('');

  if (!sorted.length) {
    feed.innerHTML = '<div style="text-align:center;padding:24px;color:#6b7280;">✅ No significant anomalies in current window</div>';
  }
}

/* ═══════════════════════════════════════════════════════════
   TAB 2: STATION DEEP-DIVE
   ═══════════════════════════════════════════════════════════ */
function buildStationSidebar() {
  const healthMap = {};
  g_health.forEach(h => healthMap[h.station_id] = h);

  const makeBtn = sid => {
    const h   = healthMap[sid] || {};
    const pct = h.current_anomaly_rate != null
      ? (parseFloat(h.current_anomaly_rate)*100).toFixed(0) + '% anomalies' : '';
    const dotColor = parseFloat(h.current_anomaly_rate) >= 0.8  ? '#f87171'
      : parseFloat(h.current_anomaly_rate) >= 0.2  ? '#fbbf24' : '#34d399';
    return `<button class="station-btn" id="sbtn-${sid}" onclick="selectStation('${sid}')">
      <span class="station-btn-name">
        <span style="color:${dotColor};margin-right:5px">●</span>${STATION_DISPLAY[sid]||sid}
      </span>
      <span class="station-btn-health">${pct}</span>
    </button>`;
  };

  el('station-list-ncr').innerHTML = NCR_STATIONS.map(makeBtn).join('');
  el('station-list-iso').innerHTML = ISO_STATIONS.map(makeBtn).join('');
}

function selectStation(sid) {
  g_currentStation = sid;
  document.querySelectorAll('.station-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('sbtn-' + sid);
  if (btn) btn.classList.add('active');

  el('station-chart-title').textContent =
    `📍 ${STATION_DISPLAY[sid]||sid} — Temperature (°C)`;

  const rows    = g_results.filter(r => r.station_id === sid)
    .sort((a,b) => new Date(a.timestamp) - new Date(b.timestamp));
  // Tier 2+ markers by default, all if toggle on
  const flagged = rows.filter(r => isAlertVisible(r));
  const flaggedAll = rows.filter(r => r.is_flagged);

  const ts     = rows.map(r => r.timestamp);
  const flagTs = flagged.map(r => r.timestamp);

  const safeNum = v => { const n = parseFloat(v); return isNaN(n) ? null : n; };

  const makeTraces = (raw, corr, name, color, chartId, h) => {
    Plotly.newPlot(chartId, [
      { x:ts, y:rows.map(r => safeNum(r[raw])), name, mode:'lines',
        line:{ color, width:1.5 },
        hovertemplate:`%{x}<br><b>${name}:</b> %{y:.1f}<extra></extra>` },
      { x:ts, y:rows.map(r => safeNum(r[corr])), name:'Corrected', mode:'lines',
        line:{ color:'#34d399', width:1, dash:'dot' }, opacity:0.7,
        hovertemplate:`%{x}<br><b>Corrected:</b> %{y:.1f}<extra></extra>` },
      { x:flagTs, y:flagged.map(r => safeNum(r[raw])), name:'Flagged ❌', mode:'markers',
        marker:{ color:'#f87171', size:7, symbol:'x' },
        hovertemplate:`%{x}<br><b>ALERT:</b> %{y:.1f}<extra></extra>` },
    ], { ...PLOTLY_DARK, height:h, yaxis:{ ...PLOTLY_DARK.yaxis, type: 'linear' } }, { responsive:true, displayModeBar:false });
    
    // Add click listener to explain any point
    const chart = document.getElementById(chartId);
    chart.on('plotly_click', function(data) {
      if (!data.points || !data.points[0]) return;
      const clickedTs = data.points[0].x;
      const row = rows.find(r => r.timestamp === clickedTs);
      if (row) {
        switchTab('explainability');
        renderAnomaly(row);
      }
    });
  };

  makeTraces('temperature','temperature_corrected','Temperature (°C)','#5b8df8','chart-temp',260);
  makeTraces('pressure','pressure_corrected','Pressure (hPa)','#a78bfa','chart-pressure',200);
  makeTraces('humidity','humidity_corrected','Humidity (%)','#38bdf8','chart-humidity',200);

  // Anomaly score
  Plotly.newPlot('chart-score', [{
    x:ts, y:rows.map(r => parseFloat(r.anomaly_score)), name:'Fused Score', mode:'lines',
    fill:'tozeroy', fillcolor:'rgba(248,113,113,0.08)',
    line:{ color:'#f87171', width:1.5 },
    hovertemplate:'%{x}<br><b>Score:</b> %{y:.3f}<extra></extra>',
  }], {
    ...PLOTLY_DARK, height:190,
    shapes:[
      { type:'line', x0:ts[0], x1:ts[ts.length-1], y0:0.4, y1:0.4,
        line:{ color:'#fbbf24', dash:'dash', width:1 } },
      { type:'line', x0:ts[0], x1:ts[ts.length-1], y0:0.65, y1:0.65,
        line:{ color:'#f87171', dash:'dash', width:1 } },
    ],
    annotations:[
      { x:ts[Math.floor(ts.length*0.96)], y:0.42, text:'Medium', showarrow:false,
        font:{ color:'#fbbf24', size:9 } },
      { x:ts[Math.floor(ts.length*0.96)], y:0.67, text:'High', showarrow:false,
        font:{ color:'#f87171', size:9 } },
    ],
    yaxis:{ ...PLOTLY_DARK.yaxis, range:[0,1] },
  }, { responsive:true, displayModeBar:false });
}

/* ═══════════════════════════════════════════════════════════
   TAB 3: AI EXPLAINABILITY
   ═══════════════════════════════════════════════════════════ */
function renderExplainability() {
  renderTopAlerts();
  renderLayerBreakdown();
  renderAnomaly();
  renderMetricsGrid();
}

/* ── Section A: Top Alerts (real explanation strings) ─────── */
function renderTopAlerts() {
  const flagged = g_results
    .filter(r => r.is_flagged && (r.severity === 'Critical' || r.severity === 'High' || r.severity === 'Medium'))
    .sort((a,b) => b.anomaly_score - a.anomaly_score)
    .slice(0, 6);

  const box = el('top-alerts-box');
  if (!flagged.length) {
    box.innerHTML = '<p style="color:#6b7280;text-align:center;padding:16px">No Medium+ severity anomalies found.</p>';
    return;
  }

  box.innerHTML = flagged.map(r => {
    const sev   = r.severity || 'Medium';
    const name  = STATION_DISPLAY[r.station_id] || r.station_id;
    const ts    = (r.timestamp||'').slice(0,16).replace('T',' ');
    const score = parseFloat(r.anomaly_score).toFixed(3);
    const conf  = r.confidence ? Math.round(parseFloat(r.confidence)*100) + '%' : '—';
    // Full explanation from fusion.py — split into bullets on " | "
    const parts = (r.explanation || r.root_cause_label || 'Unknown').split(' | ');
    const bullets = parts.map(p => `<li>${p}</li>`).join('');
    // Show correction delta if any
    const tempDelta = Math.abs(parseFloat(r.temperature||0) - parseFloat(r.temperature_corrected||r.temperature||0));
    const corrNote  = tempDelta > 0.05
      ? `<div class="alert-corr">🔧 Temp corrected by ${tempDelta.toFixed(1)}°C</div>` : '';
    return `
    <div class="top-alert-card sev-card-${sev.toLowerCase()}">
      <div class="top-alert-header">
        <div>
          <span class="severity-badge sev-${sev.toLowerCase()}">${sev}</span>
          <strong style="color:#e8eaf0;margin-left:6px">${name}</strong>
          <span style="color:#6b7280;font-size:0.75rem;margin-left:8px">${ts}</span>
        </div>
        <div style="text-align:right">
          <span style="color:#f87171;font-weight:700;font-size:1rem">${score}</span>
          <span style="color:#6b7280;font-size:0.7rem;margin-left:4px">score · ${conf} conf</span>
        </div>
      </div>
      <ul class="top-alert-bullets">${bullets}</ul>
      ${corrNote}
    </div>`;
  }).join('');
}

/* ── Section B: Real Layer Breakdown Chart ────────────────── */
function renderLayerBreakdown() {
  // Find the highest-scoring anomaly to use as the subject
  const top = g_results
    .filter(r => r.is_flagged)
    .sort((a,b) => b.anomaly_score - a.anomaly_score)[0];

  if (!top) return;

  // Build actual per-layer scores from the exported columns
  const getMax = (...cols) => Math.max(...cols.map(c => parseFloat(top[c]||0)));

  const layers = [
    { name: 'Range Check',     score: getMax('range_temperature','range_pressure','range_humidity'),   weight: 1.00, color: '#f87171', type:'rule'    },
    { name: 'Frozen Sensor',   score: getMax('frozen_temperature','frozen_pressure','frozen_humidity'), weight: 0.85, color: '#fb923c', type:'rule'    },
    { name: 'Spike Detector',  score: getMax('rate_temperature','rate_pressure','rate_humidity'),       weight: 0.55, color: '#fbbf24', type:'rule'    },
    { name: 'Drift (Z-score)', score: getMax('zscore_temperature','zscore_pressure','zscore_humidity'), weight: 0.35, color: '#38bdf8', type:'rule'    },
    { name: 'Physics Check',   score: parseFloat(top.multivariate_score||0),                           weight: 0.55, color: '#a78bfa', type:'rule'    },
    { name: 'Isolation Forest',score: parseFloat(top.ml_score||0),                                     weight: 0.45, color: '#c084fc', type:'ml'      },
    { name: 'Spatial Check',   score: parseFloat(top.spatial_score||0),                                weight: 0.65, color: '#34d399', type:'spatial' },
  ].sort((a,b) => b.score*b.weight - a.score*a.weight);

  const name = STATION_DISPLAY[top.station_id] || top.station_id;

  Plotly.newPlot('chart-attribution', [{
    type:'bar', orientation:'h',
    x: layers.map(l => +(l.score * l.weight).toFixed(3)),
    y: layers.map(l => l.name),
    marker:{ color: layers.map(l => l.color), opacity:0.85 },
    text: layers.map(l => (l.score*100).toFixed(0) + '%'),
    textposition:'outside',
    textfont:{ size:10, color:'#9ca3af' },
    hovertemplate:'<b>%{y}</b><br>Raw score: ' + '%{text}' + '<br>Weighted: %{x:.3f}<extra></extra>',
  }], {
    ...PLOTLY_DARK, height:260,
    xaxis:{ ...PLOTLY_DARK.xaxis, title:{ text:'Weighted Contribution (raw score × fusion weight)', font:{size:10} }, range:[0,1.05] },
    annotations:[{ x:0.5, y:-0.22, xref:'paper', yref:'paper', showarrow:false,
      text:`Subject: ${name} · Score ${parseFloat(top.anomaly_score).toFixed(3)} · ${top.severity}`,
      font:{ size:9, color:'#6b7280' } }],
  }, { responsive:true, displayModeBar:false });
}

function renderAnomaly(t = null) {
  if (!t) {
    const flagged = g_results.filter(r => r.is_flagged)
      .sort((a,b) => b.anomaly_score - a.anomaly_score);
    if (!flagged.length) { 
      el('worst-anomaly-box').innerHTML = 'No anomalies found.'; 
      return; 
    }
    t = flagged[0];
  }

  const box = el('worst-anomaly-box');
  const sevColor = t.severity==='Critical'?'#f87171':t.severity==='High'?'#fbbf24':
    t.severity==='Medium'?'#38bdf8':'#9ca3af';
  const confPct = t.confidence ? Math.round(parseFloat(t.confidence)*100) + '%' : '—';
  // Confidence explanation
  const confDesc = parseFloat(t.confidence) >= 0.75 ? 'Multiple independent detectors agreed'
    : parseFloat(t.confidence) >= 0.5 ? 'Two or more detectors fired'
    : 'Single detector signal — treat as low-confidence';

  // Full explanation from fusion.py — split on " | " for readability
  const explParts = (t.explanation || '').split(' | ').filter(Boolean);
  const explHTML = explParts.length
    ? `<div class="explain-callout"><strong>🔍 Why this was flagged:</strong><ul>${explParts.map(p=>`<li>${p}</li>`).join('')}</ul></div>`
    : '';

  box.innerHTML = `
    ${row('Station',    STATION_DISPLAY[t.station_id]||t.station_id)}
    ${row('Timestamp',  (t.timestamp||'').slice(0,16).replace('T',' '))}
    ${row('Anomaly Score', `<span style="color:#f87171;font-weight:700">${parseFloat(t.anomaly_score).toFixed(4)}</span>`)}
    ${row('Severity',   `<span style="color:${sevColor};font-weight:700">${t.severity}</span>`)}
    ${row('Confidence', `<span style="color:#34d399">${confPct}</span> <span style="color:#6b7280;font-size:0.75rem">(${confDesc})</span>`)}
    ${row('Root Cause', t.root_cause_label||'—')}
    ${row('Temp (raw→fixed)',     `${parseFloat(t.temperature).toFixed(1)}°C → ${parseFloat(t.temperature_corrected||t.temperature).toFixed(1)}°C`)}
    ${row('Pressure (raw→fixed)',`${parseFloat(t.pressure).toFixed(1)} → ${parseFloat(t.pressure_corrected||t.pressure).toFixed(1)} hPa`)}
    ${row('Humidity (raw→fixed)',`${parseFloat(t.humidity).toFixed(1)} → ${parseFloat(t.humidity_corrected||t.humidity).toFixed(1)}%`)}
    ${t.dew_point != null ? row('Dew Point', `${parseFloat(t.dew_point).toFixed(1)}°C ${parseFloat(t.dew_point) > parseFloat(t.temperature) ? '<span style="color:#f87171">⚠ Exceeds air temp</span>' : ''}`) : ''}
    ${explHTML}
  `;
}

function renderMetricsGrid() {
  const t1 = g_metrics.tier1_any_flag || {};
  const t2 = g_metrics.tier2_confirmed_alert || {};
  const far1 = t1.fp!=null&&t1.tn!=null ? ((t1.fp/(t1.fp+t1.tn))*100).toFixed(1)+'%':'—';
  const far2 = t2.fp!=null&&t2.tn!=null ? ((t2.fp/(t2.fp+t2.tn))*100).toFixed(1)+'%':'—';

  el('metrics-grid').innerHTML = `
    ${metricBox('Tier 1 · Precision','High-recall QC screen', t1.precision, '#38bdf8', `False Alarm Rate: ${far1}`)}
    ${metricBox('Tier 2 · Precision','Operational alert (Med+)', t2.precision, '#34d399', `False Alarm Rate: ${far2}`)}
    ${metricBox('Tier 1 · Recall','True positives caught', t1.recall, '#a78bfa', `TP ${t1.tp||'—'} · FN ${t1.fn||'—'}`)}
    ${metricBox('Tier 1 · F1','Harmonic mean', t1.f1, '#fbbf24', `TP ${t1.tp||'—'} · FP ${t1.fp||'—'}`)}
  `;
}

/* ── UTILS ──────────────────────────────────────────────── */
const el = id => document.getElementById(id);

function row(label, val) {
  return `<div class="anomaly-row">
    <span class="anomaly-row-label">${label}</span>
    <span class="anomaly-row-val">${val}</span>
  </div>`;
}

function metricBox(label, title, val, color, sub) {
  const pct = val != null ? (val * 100).toFixed(0) + '%' : '—';
  return `<div class="metric-box">
    <div class="metric-box-label">${label}</div>
    <div class="metric-box-title">${title}</div>
    <div class="metric-box-val" style="color:${color}">${pct}</div>
    <div style="font-size:0.65rem;color:#6b7280;margin-top:4px">${sub}</div>
  </div>`;
}

function median(arr) {
  const s = arr.filter(v => !isNaN(v)).sort((a,b) => a-b);
  if (!s.length) return 0;
  const m = Math.floor(s.length/2);
  return s.length % 2 ? s[m] : (s[m-1]+s[m])/2;
}

/* ── BOOT ───────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', loadData);
