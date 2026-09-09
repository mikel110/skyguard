/* ═══════════════════════════════════════════════════════════
   SkyGuard AI — Live Dashboard
   Fetches from Flask backend: GET /api/data
   Falls back gracefully if the server is still initializing.
   ═══════════════════════════════════════════════════════════ */

const API_BASE       = 'http://localhost:5001';  // Flask backend — always explicit
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
async function triggerRefresh() {
  const btn = document.getElementById('btn-refresh');
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.5';
    btn.innerHTML = '⏳ Fetching...';
  }
  
  try {
    await fetch(`${API_BASE}/api/refresh`, { method: 'POST' });
    setStatus('initializing', '⏳ Pipeline running...');
    // Start fast-polling until the server finishes the refresh
    g_pollTimer = setTimeout(loadData, 2000);
  } catch(e) {
    console.error(e);
    setStatus('error', '❌ Cannot reach server');
  }
}

async function loadData() {
  try {
    let json;

    // ── Fast path: Flask injects data directly into the page ──
    // window.__SKYGUARD__ is set by the server before app.js runs,
    // so this works with zero fetch() calls, zero CORS issues.
    if (window.__SKYGUARD__) {
      json = window.__SKYGUARD__;
      window.__SKYGUARD__ = null;
    } else {
      // ── Fallback: poll the API ────────────────────────────
      const res = await fetch(`${API_BASE}/api/data`);
      json = await res.json();
    }

    // ── Handle server states ────────────────────────────────
    if (json.status === 'initializing' || json.status === 'refreshing') {
      setStatus('initializing', '⏳ Pipeline running...');
      g_pollTimer = setTimeout(loadData, 2000);
      return;
    }
    if (json.status === 'error') {
      setStatus('error', '❌ ' + (json.message || json.error || 'Server error'));
      const btn = document.getElementById('btn-refresh');
      if (btn) {
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.5l1.75 1.93"></path></svg> Fetch Fresh Data';
      }
      return;
    }

    // Only re-render if the data actually changed (avoid unnecessary redraws)
    if (json.last_run_utc === g_lastRunUtc && g_results.length > 0) {
      return;
    }

    g_lastRunUtc = json.last_run_utc;
    g_results    = json.detection_results || [];
    g_health     = json.sensor_health     || [];
    g_metrics    = json.evaluation_metrics || {};

    const dr      = json.data_range || {};
    const lastRun = json.last_run_utc ? new Date(json.last_run_utc).toLocaleString() : '—';
    const nextRun = json.next_run_utc ? new Date(json.next_run_utc).toLocaleString() : '—';

    setStatus('live', `🟢 Live · Updated ${lastRun}`);
    document.getElementById('footer-timestamp').textContent =
      `Data: ${dr.start ? dr.start.slice(0,10) : '?'} → ${dr.end ? dr.end.slice(0,10) : '?'} · Next refresh: ${nextRun}`;

    renderOverview();
    buildStationSidebar();
    renderExplainability();

    if (g_currentStation) selectStation(g_currentStation);

    // Re-enable the refresh button
    const btn = document.getElementById('btn-refresh');
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.5l1.75 1.93"></path></svg> Fetch Fresh Data';
    }

  } catch(e) {
    setStatus('error', '❌ Cannot reach server');
    console.error(e);
    g_pollTimer = setTimeout(loadData, 8000);
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
  const flagged = g_results.filter(r => r.is_flagged);
  const total   = g_results.length;
  const pct     = total ? (flagged.length / total * 100).toFixed(1) : '0';

  const t2  = g_metrics.tier2_confirmed_alert || {};
  const far2 = t2.fp != null && t2.tn != null
    ? ((t2.fp / (t2.fp + t2.tn)) * 100).toFixed(2) + '%' : '—';

  el('kpi-total-val').textContent     = total.toLocaleString();
  el('kpi-anomalies-val').textContent = flagged.length.toLocaleString();
  el('kpi-anomalies-pct').textContent = pct + '% of readings';
  el('kpi-precision-val').textContent = t2.precision != null
    ? (t2.precision * 100).toFixed(1) + '%' : '—';
  el('kpi-far-val').textContent = far2;
  el('alert-count-badge').textContent = flagged.length + ' alerts';

  renderRootCausePie(flagged);
  renderHealthBar();
  renderAlertFeed(flagged);
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

function renderAlertFeed(flagged) {
  const feed = el('alert-feed');
  const sorted = [...flagged]
    .sort((a,b) => new Date(b.timestamp) - new Date(a.timestamp))
    .slice(0, 100);

  feed.innerHTML = sorted.map(r => {
    const sev   = r.severity || 'Low';
    const ts    = (r.timestamp||'').slice(0,16).replace('T',' ');
    const cause = r.root_cause_label || 'Unknown';
    const name  = STATION_DISPLAY[r.station_id] || r.station_id;
    return `<div class="alert-item">
      <span class="alert-time">${ts}</span>
      <span class="alert-station">${name}</span>
      <span class="alert-cause" title="${cause}">${cause}</span>
      <span class="severity-badge sev-${sev.toLowerCase()}">${sev}</span>
    </div>`;
  }).join('');
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
  const flagged = rows.filter(r => r.is_flagged);

  const ts     = rows.map(r => r.timestamp);
  const flagTs = flagged.map(r => r.timestamp);

  const makeTraces = (raw, corr, name, color, chartId, h) => {
    Plotly.newPlot(chartId, [
      { x:ts, y:rows.map(r => parseFloat(r[raw])), name, mode:'lines',
        line:{ color, width:1.5 },
        hovertemplate:`%{x}<br><b>${name}:</b> %{y:.1f}<extra></extra>` },
      { x:ts, y:rows.map(r => parseFloat(r[corr])), name:'Corrected', mode:'lines',
        line:{ color:'#34d399', width:1, dash:'dot' }, opacity:0.7,
        hovertemplate:`%{x}<br><b>Corrected:</b> %{y:.1f}<extra></extra>` },
      { x:flagTs, y:flagged.map(r => parseFloat(r[raw])), name:'Flagged ❌', mode:'markers',
        marker:{ color:'#f87171', size:7, symbol:'x' },
        hovertemplate:`%{x}<br><b>ALERT:</b> %{y:.1f}<extra></extra>` },
    ], { ...PLOTLY_DARK, height:h }, { responsive:true, displayModeBar:false });
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
  renderAttribution();
  renderWorstAnomaly();
  renderMetricsGrid();
}

function renderAttribution() {
  const flagged = g_results.filter(r => r.is_flagged)
    .sort((a,b) => b.anomaly_score - a.anomaly_score);
  if (!flagged.length) return;

  const top         = flagged[0];
  const stationRows = g_results.filter(r => r.station_id === top.station_id);
  const medT = median(stationRows.map(r => parseFloat(r.temperature)));
  const medP = median(stationRows.map(r => parseFloat(r.pressure)));
  const medH = median(stationRows.map(r => parseFloat(r.humidity)));
  const sc   = parseFloat(top.anomaly_score);

  const raw = {
    temperature:     Math.abs(parseFloat(top.temperature) - medT),
    pressure:        Math.abs(parseFloat(top.pressure)    - medP),
    humidity:        Math.abs(parseFloat(top.humidity)    - medH),
    'temp roc':      sc * 0.30,
    'pressure roc':  sc * 0.20,
    'humidity roc':  sc * 0.10,
    'dew point gap': sc * 0.15,
  };
  const total = Object.values(raw).reduce((a,b) => a+b, 0.001);
  const feats = Object.entries(raw)
    .map(([k,v]) => ({ name:k, val:+(v/total*100).toFixed(1) }))
    .sort((a,b) => b.val - a.val);

  const palette = ['#f87171','#fbbf24','#5b8df8','#a78bfa','#38bdf8','#34d399','#fb923c'];
  Plotly.newPlot('chart-attribution', [{
    type:'bar', x:feats.map(f=>f.val), y:feats.map(f=>f.name),
    orientation:'h',
    marker:{ color:feats.map((_,i)=>palette[i]), opacity:0.88 },
    text:feats.map(f=>f.val+'%'), textposition:'outside',
    textfont:{ size:10, color:'#9ca3af' },
    hovertemplate:'<b>%{y}</b><br>Contribution: %{x}%<extra></extra>',
  }], {
    ...PLOTLY_DARK, height:250,
    xaxis:{ ...PLOTLY_DARK.xaxis, title:{ text:'Attribution (%)', font:{ size:10 } } },
    annotations:[{ x:0.5, y:-0.18, xref:'paper', yref:'paper', showarrow:false,
      text:`Worst anomaly: ${STATION_DISPLAY[top.station_id]||top.station_id} · Score: ${parseFloat(top.anomaly_score).toFixed(3)}`,
      font:{ size:9, color:'#6b7280' } }],
  }, { responsive:true, displayModeBar:false });
}

function renderWorstAnomaly() {
  const flagged = g_results.filter(r => r.is_flagged)
    .sort((a,b) => b.anomaly_score - a.anomaly_score);
  const box = el('worst-anomaly-box');
  if (!flagged.length) { box.innerHTML = 'No flagged anomalies found.'; return; }
  const t = flagged[0];
  const sevColor = t.severity==='Critical'?'#f87171':t.severity==='High'?'#fbbf24':
    t.severity==='Medium'?'#38bdf8':'#9ca3af';

  box.innerHTML = `
    ${row('Station',    STATION_DISPLAY[t.station_id]||t.station_id)}
    ${row('Timestamp',  (t.timestamp||'').slice(0,16))}
    ${row('Anomaly Score', `<span style="color:#f87171;font-weight:700">${parseFloat(t.anomaly_score).toFixed(4)}</span>`)}
    ${row('Severity',   `<span style="color:${sevColor};font-weight:700">${t.severity}</span>`)}
    ${row('Root Cause', t.root_cause_label||'—')}
    ${row('Temp (raw→fixed)',     `${parseFloat(t.temperature).toFixed(1)}°C → ${parseFloat(t.temperature_corrected).toFixed(1)}°C`)}
    ${row('Pressure (raw→fixed)',`${parseFloat(t.pressure).toFixed(1)} → ${parseFloat(t.pressure_corrected).toFixed(1)} hPa`)}
    ${row('Humidity (raw→fixed)',`${parseFloat(t.humidity).toFixed(1)} → ${parseFloat(t.humidity_corrected).toFixed(1)}%`)}
  `;
}

function renderMetricsGrid() {
  const t1 = g_metrics.tier1_any_flag || {};
  const t2 = g_metrics.tier2_confirmed_alert || {};
  const far1 = t1.fp!=null&&t1.tn!=null ? ((t1.fp/(t1.fp+t1.tn))*100).toFixed(1)+'%':'—';
  const far2 = t2.fp!=null&&t2.tn!=null ? ((t2.fp/(t2.fp+t2.tn))*100).toFixed(1)+'%':'—';

  el('metrics-grid').innerHTML = `
    ${metricBox('Tier 1 · Any Flag','High-recall QC screen', t1.precision, '#38bdf8', `FAR ${far1}`)}
    ${metricBox('Tier 2 · Confirmed','Operational alert (Med+)', t2.precision, '#34d399', `FAR ${far2}`)}
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
