"""Lightweight, interactive web dashboard for the GridWeave campus simulation.

Standard library only (``http.server``) plus Chart.js from a CDN.

Two entry points:

* :func:`serve_dashboard` shows one finished run (read-only).
* :func:`serve_interactive_dashboard` adds controls: pick a scenario and mode
  and re-run, compare all modes on a scenario, and replay the run slot by slot.

All numbers come from :class:`MetricsAggregator` (the single metrics source)
or directly from the recorded slot data; the page computes nothing itself.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlparse

from gridweave.coordinator.metrics import MetricsAggregator

if TYPE_CHECKING:
    from gridweave.coordinator.modes import RunOutput
    from gridweave.coordinator.records import SimulationResult


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GridWeave Campus Energy Simulation Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-color: #0b0f19; --card-bg: #151c2c; --border-color: #2a364f; --text-main: #e2e8f0;
      --text-muted: #94a3b8; --accent-blue: #3b82f6; --accent-green: #10b981; --accent-amber: #f59e0b;
      --accent-red: #ef4444; --accent-purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    body { background: var(--bg-color); color: var(--text-main); padding: 20px; line-height: 1.5; }
    header { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; padding-bottom: 16px; border-bottom: 1px solid var(--border-color); margin-bottom: 16px; }
    h1 { font-size: 1.5rem; color: #fff; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .badge { font-size: 0.75rem; padding: 4px 8px; border-radius: 4px; background: var(--accent-blue); color: white; text-transform: uppercase; font-weight: bold; }
    .badge.mode { background: var(--accent-purple); }
    .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .controls label { font-size: 0.8rem; color: var(--text-muted); }
    select, button { background: var(--card-bg); color: var(--text-main); border: 1px solid var(--border-color); border-radius: 6px; padding: 6px 10px; font-size: 0.9rem; }
    button { cursor: pointer; }
    button.primary { background: var(--accent-blue); border-color: var(--accent-blue); color: #fff; font-weight: 600; }
    button:disabled { opacity: 0.5; cursor: wait; }
    #status { font-size: 0.85rem; color: var(--text-muted); }
    #scen-desc { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 16px; }
    .grid-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .kpi-card { background: var(--card-bg); padding: 14px; border-radius: 8px; border: 1px solid var(--border-color); }
    .kpi-title { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; }
    .kpi-val { font-size: 1.5rem; font-weight: bold; color: #fff; }
    .kpi-sub { font-size: 0.78rem; color: var(--text-muted); margin-top: 4px; }
    .charts-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 20px; }
    @media (max-width: 900px) { .charts-grid { grid-template-columns: 1fr; } }
    .chart-box { background: var(--card-bg); padding: 18px; border-radius: 8px; border: 1px solid var(--border-color); min-width: 0; overflow-x: auto; }
    .chart-box.full { margin-bottom: 20px; }
    .chart-box h2 { font-size: 1rem; margin-bottom: 12px; color: var(--text-main); }
    table { width: 100%; border-collapse: collapse; margin-top: 6px; }
    th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--border-color); font-size: 0.85rem; white-space: nowrap; }
    th { color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.72rem; }
    td.num, th.num { text-align: right; }
    tr.best td { color: var(--accent-green); font-weight: 600; }
    .event-log { max-height: 250px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; }
    .event-item { padding: 8px 12px; border-bottom: 1px solid var(--border-color); display: flex; gap: 12px; align-items: center; }
    .event-tag { color: var(--accent-amber); font-weight: bold; }
    .replay-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
    .replay-bar input[type=range] { flex: 1; min-width: 200px; }
    #slot-label { font-family: monospace; font-size: 0.95rem; min-width: 190px; }
    .status-full { color: var(--accent-green); } .status-partial { color: var(--accent-amber); }
    .status-short { color: var(--accent-red); font-weight: 600; }
    .pill { display: inline-block; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; background: #2a364f; margin-right: 4px; }
  </style>
</head>
<body>
  <header>
    <h1>GridWeave Campus Dashboard <span class="badge" id="scen-name">Loading...</span><span class="badge mode" id="mode-name"></span></h1>
    <div id="sim-info" style="color: var(--text-muted); font-size: 0.9rem;"></div>
  </header>
  <div class="controls" id="controls" style="display:none">
    <label for="sel-scenario">Scenario</label><select id="sel-scenario"></select>
    <label for="sel-mode">Mode</label><select id="sel-mode"></select>
    <button class="primary" id="btn-run">Run</button>
    <button id="btn-compare">Compare all modes</button>
    <span id="status"></span>
  </div>
  <div id="scen-desc"></div>

  <div class="chart-box full" id="compare-box" style="display:none">
    <h2 id="compare-title">Mode comparison</h2>
    <table id="compareTable"><thead><tr>
      <th>Mode</th><th class="num">Service</th><th class="num">Critical served</th><th class="num">Critical shortfall (kWh)</th>
      <th class="num">Expired (kWh)</th><th class="num">Fairness</th><th class="num">Cost</th></tr></thead><tbody></tbody></table>
    <div class="kpi-sub" style="margin-top:8px">Same campus, same demand, same events; only the decision-making differs. Green = lowest critical shortfall.</div>
  </div>

  <section class="grid-kpis">
    <div class="kpi-card"><div class="kpi-title">Total Demand</div><div class="kpi-val" id="kpi-demand">0 kWh</div><div class="kpi-sub" id="kpi-served-sub">Served: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Service Ratio</div><div class="kpi-val" id="kpi-svc-ratio">0%</div><div class="kpi-sub" id="kpi-unserved-sub">Unserved: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Critical Load Served</div><div class="kpi-val" id="kpi-crit-ratio">0%</div><div class="kpi-sub" id="kpi-crit-sub">Shortfall: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Jain's Fairness Index</div><div class="kpi-val" id="kpi-fairness">0.0000</div><div class="kpi-sub">Min 0.0 — Max 1.0</div></div>
    <div class="kpi-card"><div class="kpi-title">Battery</div><div class="kpi-val" id="kpi-battery">0 kWh</div><div class="kpi-sub" id="kpi-battery-sub">Discharged · recharged from grid</div></div>
    <div class="kpi-card"><div class="kpi-title">Re-Auctions</div><div class="kpi-val" id="kpi-reauctions">0</div><div class="kpi-sub" id="kpi-events-sub">Events: 0</div></div>
  </section>

  <div class="chart-box full">
    <h2>Replay: step through the run one 15-minute slot at a time</h2>
    <div class="replay-bar">
      <button id="btn-play">&#9654; Play</button>
      <input type="range" id="slot-slider" min="0" max="0" value="0">
      <span id="slot-label">—</span>
    </div>
    <div class="charts-grid" style="margin-bottom:0">
      <div style="min-width:0; overflow-x:auto">
        <table id="replayBuildings"><thead><tr><th>Building</th><th class="num">Bid (kW)</th><th class="num">of which critical</th>
          <th class="num">Allocated</th><th class="num">Actually needed</th><th class="num">Served</th><th class="num">Critical short</th>
          <th class="num">Deferred</th><th>Status</th></tr></thead><tbody></tbody></table>
      </div>
      <div style="min-width:0; overflow-x:auto">
        <table id="replaySources"><thead><tr><th>Source</th><th class="num">Offered (kW)</th><th class="num">Price</th><th class="num">Delivered</th></tr></thead><tbody></tbody></table>
        <div class="kpi-sub" id="replay-market" style="margin-top:8px"></div>
      </div>
    </div>
  </div>

  <section class="charts-grid">
    <div class="chart-box"><h2>Demand & Supply over Time (kW)</h2><canvas id="timeSeriesChart" height="120"></canvas></div>
    <div class="chart-box"><h2>Energy Served vs Shortfall by Building</h2><canvas id="buildingChart" height="240"></canvas></div>
  </section>

  <section class="charts-grid" id="battery-section">
    <div class="chart-box"><h2>Battery: State of Charge &amp; Discharge</h2>
      <canvas id="batteryChart" height="70"></canvas>
      <canvas id="dischargeChart" height="55" style="margin-top:10px"></canvas></div>
    <div class="chart-box">
      <h2>When Is the Battery Used? Offer Price vs Grid Price</h2>
      <canvas id="priceChart" height="240"></canvas>
      <div class="kpi-sub" style="margin-top: 8px;">The battery offers at its wear cost plus the cost of its stored energy. The market uses it when the grid price is higher (evening peak), or when the grid is out.</div>
    </div>
  </section>

  <section class="charts-grid">
    <div class="chart-box">
      <h2>Per-Building Performance Breakdown</h2>
      <table id="bldgTable"><thead><tr>
        <th>Building ID</th><th class="num">Demand (kWh)</th><th class="num">Served (kWh)</th><th class="num">Critical Short (kWh)</th>
        <th class="num">Curtailed (kWh)</th><th class="num">Expired (kWh)</th><th class="num">Deferred* (kWh)</th><th class="num">Service Ratio</th>
      </tr></thead><tbody></tbody></table>
      <div class="kpi-sub" style="margin-top: 8px;">* Deferred is a flow, not an outcome: deferred energy is later served, expires, or is still queued. Demand = Served + Critical Short + Curtailed + Expired + Still Queued.</div>
    </div>
    <div class="chart-box"><h2>Scenario Event Log</h2><div class="event-log" id="eventLog">No events recorded.</div></div>
  </section>

  <script>
    const charts = {};
    let frames = [];
    let currentSlot = null;
    let playTimer = null;
    const f1 = v => (v || 0).toFixed(1);
    const pct = v => ((v ?? 0) * 100).toFixed(1) + '%';
    const gridColor = { color: '#2a364f' };
    const legend = { labels: { color: '#e2e8f0' } };

    // Draws a vertical line on time-series charts at the replayed slot.
    const slotMarker = {
      id: 'slotMarker',
      afterDatasetsDraw(chart) {
        if (currentSlot === null || !chart.scales.x || chart.config.type === 'bar' && chart.canvas.id === 'buildingChart') return;
        const x = chart.scales.x.getPixelForValue(currentSlot);
        const { top, bottom } = chart.chartArea;
        const ctx = chart.ctx;
        ctx.save(); ctx.strokeStyle = '#fbbf24'; ctx.lineWidth = 2; ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
      }
    };
    Chart.register(slotMarker);

    function draw(id, config) {
      if (charts[id]) charts[id].destroy();
      charts[id] = new Chart(document.getElementById(id), config);
    }

    async function getJSON(url) {
      const res = await fetch(url);
      if (!res.ok) throw new Error((await res.text()) || res.statusText);
      return res.json();
    }

    async function init() {
      const opts = await getJSON('/api/options');
      if (opts.interactive) {
        document.getElementById('controls').style.display = 'flex';
        const sSel = document.getElementById('sel-scenario'), mSel = document.getElementById('sel-mode');
        opts.scenarios.forEach(s => sSel.add(new Option(s.name, s.name)));
        opts.modes.forEach(m => mSel.add(new Option(m.label, m.name)));
        sSel.value = opts.current.scenario; mSel.value = opts.current.mode;
        document.getElementById('btn-run').onclick = runSelected;
        document.getElementById('btn-compare').onclick = compareModes;
      }
      await loadAll();
    }

    function busy(on, msg) {
      ['btn-run', 'btn-compare'].forEach(id => document.getElementById(id).disabled = on);
      document.getElementById('status').innerText = msg || '';
    }

    async function runSelected() {
      const s = document.getElementById('sel-scenario').value, m = document.getElementById('sel-mode').value;
      busy(true, 'Running simulation…');
      try {
        await getJSON(`/api/run?scenario=${encodeURIComponent(s)}&mode=${encodeURIComponent(m)}`);
        await loadAll();
        busy(false, 'Done.');
      } catch (e) { busy(false, 'Error: ' + e.message); }
    }

    async function compareModes() {
      const s = document.getElementById('sel-scenario').value;
      busy(true, 'Running every mode on ' + s + '…');
      try {
        const rows = await getJSON(`/api/compare?scenario=${encodeURIComponent(s)}`);
        const best = Math.min(...rows.map(r => r.critical_shortfall_kwh));
        const tb = document.querySelector('#compareTable tbody'); tb.innerHTML = '';
        rows.forEach(r => {
          const tr = document.createElement('tr');
          if (r.critical_shortfall_kwh === best) tr.className = 'best';
          tr.innerHTML = `<td>${r.label}</td><td class="num">${pct(r.service_ratio)}</td><td class="num">${pct(r.critical_service_ratio)}</td>
            <td class="num">${f1(r.critical_shortfall_kwh)}</td><td class="num">${f1(r.expired_kwh)}</td>
            <td class="num">${(r.fairness ?? 1).toFixed(3)}</td><td class="num">${Math.round(r.cost).toLocaleString()}</td>`;
          tb.appendChild(tr);
        });
        document.getElementById('compare-title').innerText = 'Mode comparison: ' + s;
        document.getElementById('compare-box').style.display = 'block';
        busy(false, '');
      } catch (e) { busy(false, 'Error: ' + e.message); }
    }

    async function loadAll() {
      const metrics = await getJSON('/api/metrics');
      frames = await getJSON('/api/replay');
      renderSummary(metrics);
      renderCharts(metrics);
      const slider = document.getElementById('slot-slider');
      slider.max = Math.max(0, frames.length - 1);
      const firstEvent = frames.findIndex(f => f.events.length > 0 || f.scarcity > 0);
      slider.value = firstEvent >= 0 ? firstEvent : 0;
      renderFrame(+slider.value);
    }

    function renderSummary(m) {
      document.getElementById('scen-name').innerText = m.scenario_name || 'Simulation';
      document.getElementById('mode-name').innerText = m.mode_label || '';
      document.getElementById('scen-desc').innerText = m.scenario_description || '';
      document.getElementById('sim-info').innerText = `${m.n_buildings} Buildings | ${m.n_slots} Slots (${m.resolution_minutes}m) | Seed ${m.seed}`;
      document.getElementById('kpi-demand').innerText = f1(m.total_demand_kwh) + ' kWh';
      document.getElementById('kpi-served-sub').innerText = `Served: ${f1(m.total_served_kwh)} kWh`;
      document.getElementById('kpi-svc-ratio').innerText = pct(m.overall_service_ratio);
      document.getElementById('kpi-unserved-sub').innerText =
        `Unserved ${f1(m.total_unserved_kwh)} kWh: critical ${f1(m.total_critical_shortfall_kwh)}, ` +
        `curtailed ${f1(m.total_curtailed_kwh)}, expired ${f1(m.total_expired_kwh)}, queued ${f1(m.total_backlog_remaining_kwh)}`;
      document.getElementById('kpi-crit-ratio').innerText = pct(m.critical_service_ratio ?? 1);
      document.getElementById('kpi-crit-sub').innerText =
        `Shortfall: ${f1(m.total_critical_shortfall_kwh)} kWh in ${m.total_critical_shortfall_events || 0} events`;
      document.getElementById('kpi-fairness').innerText = (m.fairness ? m.fairness.jains_index_final : 1.0).toFixed(4);
      document.getElementById('kpi-reauctions').innerText = m.market ? m.market.re_auction_slots : 0;
      document.getElementById('kpi-events-sub').innerText = `Events: ${m.n_events || 0}`;
      const hasBattery = ((m.series || {}).battery_soc || []).some(v => v !== null);
      document.getElementById('kpi-battery').innerText = `${f1(m.total_battery_discharge_kwh)} kWh`;
      document.getElementById('kpi-battery-sub').innerText = hasBattery
        ? `Discharged · ${f1(m.total_grid_to_battery_kwh)} kWh recharged from grid off-peak` : 'No battery in this run';

      const tbody = document.querySelector('#bldgTable tbody'); tbody.innerHTML = '';
      Object.entries(m.building_summaries || {}).forEach(([id, b]) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><strong>${id}</strong></td><td class="num">${f1(b.demand_kwh)}</td><td class="num">${f1(b.served_kwh)}</td>
          <td class="num">${f1(b.critical_shortfall_kwh)} (${b.critical_shortfall_events || 0})</td><td class="num">${f1(b.curtailed_kwh)}</td>
          <td class="num">${f1(b.expired_kwh)}</td><td class="num">${f1(b.deferred_kwh)}</td><td class="num"><strong>${pct(b.service_ratio)}</strong></td>`;
        tbody.appendChild(tr);
      });

      const evLog = document.getElementById('eventLog');
      if ((m.event_log || []).length) {
        evLog.innerHTML = '';
        m.event_log.forEach(e => {
          const div = document.createElement('div'); div.className = 'event-item';
          div.innerHTML = `<span class="event-tag">[Slot ${e.slot_index}]</span> <span><strong>${e.event_type}</strong> — ${e.description || ''}</span>`;
          evLog.appendChild(div);
        });
      } else { evLog.innerText = 'No events recorded.'; }
    }

    function renderCharts(m) {
      const labels = frames.map(f => f.i);
      const tickCb = { callback: (v, i) => (frames[i] ? frames[i].t : v), maxTicksLimit: 12, color: '#94a3b8' };
      const xAxis = { grid: gridColor, ticks: tickCb };
      draw('timeSeriesChart', { type: 'line', data: { labels, datasets: [
          { label: 'Requested kW', data: frames.map(f => f.requested), borderColor: '#ef4444', borderWidth: 2, pointRadius: 0 },
          { label: 'Served kW', data: frames.map(f => f.served), borderColor: '#10b981', borderWidth: 2, pointRadius: 0 },
          { label: 'Total Supply kW', data: frames.map(f => f.supply), borderColor: '#f59e0b', borderDash: [4, 4], pointRadius: 0 } ] },
        options: { responsive: true, animation: false, scales: { y: { beginAtZero: true, grid: gridColor }, x: xAxis }, plugins: { legend } } });

      const names = Object.keys(m.building_summaries || {});
      const seg = key => names.map(k => m.building_summaries[k][key] || 0);
      draw('buildingChart', { type: 'bar', data: { labels: names, datasets: [
          { label: 'Served', data: seg('served_kwh'), backgroundColor: '#10b981' },
          { label: 'Critical shortfall', data: seg('critical_shortfall_kwh'), backgroundColor: '#ef4444' },
          { label: 'Curtailed', data: seg('curtailed_kwh'), backgroundColor: '#f59e0b' },
          { label: 'Expired', data: seg('expired_kwh'), backgroundColor: '#8b5cf6' },
          { label: 'Still queued', data: seg('final_backlog_kwh'), backgroundColor: '#3b82f6' } ] },
        options: { responsive: true, animation: false, scales: { y: { stacked: true, beginAtZero: true, grid: gridColor }, x: { stacked: true, grid: gridColor } }, plugins: { legend } } });

      const s = m.series || {};
      const hasBattery = (s.battery_soc || []).some(v => v !== null);
      document.getElementById('battery-section').style.display = hasBattery ? '' : 'none';
      if (!hasBattery) return;
      // Two single-axis charts (charge % and discharge kW) instead of one dual-axis chart.
      draw('batteryChart', { type: 'line', data: { labels, datasets: [
          { label: 'State of charge (%)', data: s.battery_soc.map(v => v === null ? null : v * 100), borderColor: '#8b5cf6', borderWidth: 2, pointRadius: 0 } ] },
        options: { responsive: true, animation: false, scales: {
          y: { min: 0, max: 100, title: { display: true, text: 'Charge %', color: '#94a3b8' }, grid: gridColor },
          x: { grid: gridColor, ticks: { display: false } } }, plugins: { legend } } });
      draw('dischargeChart', { type: 'bar', data: { labels, datasets: [
          { label: 'Battery discharge (kW)', data: s.battery_discharge_kw, backgroundColor: '#f59e0b' } ] },
        options: { responsive: true, animation: false, scales: {
          y: { beginAtZero: true, title: { display: true, text: 'kW', color: '#94a3b8' }, grid: gridColor },
          x: xAxis }, plugins: { legend } } });
      draw('priceChart', { type: 'line', data: { labels, datasets: [
          { label: 'Grid price', data: s.grid_price, borderColor: '#3b82f6', borderWidth: 2, pointRadius: 0, stepped: true },
          { label: 'Battery offer price', data: s.battery_offer_price, borderColor: '#8b5cf6', borderWidth: 2, pointRadius: 0, stepped: true } ] },
        options: { responsive: true, animation: false, scales: { y: { beginAtZero: true, title: { display: true, text: 'per kWh', color: '#94a3b8' }, grid: gridColor }, x: xAxis }, plugins: { legend } } });
    }

    function renderFrame(i) {
      const f = frames[i];
      if (!f) return;
      currentSlot = i;
      document.getElementById('slot-slider').value = i;
      document.getElementById('slot-label').innerText = `Slot ${f.i} · ${f.t}`;
      const bt = document.querySelector('#replayBuildings tbody'); bt.innerHTML = '';
      f.buildings.forEach(b => {
        const cls = b.critical_short > 0.05 ? 'status-short' : (b.status === 'full' ? 'status-full' : 'status-partial');
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${b.id}</td><td class="num">${f1(b.requested)}</td><td class="num">${f1(b.critical)}</td><td class="num">${f1(b.allocated)}</td>
          <td class="num">${f1(b.needed)}</td><td class="num">${f1(b.served)}</td><td class="num">${f1(b.critical_short)}</td>
          <td class="num">${f1(b.deferred)}</td><td class="${cls}">${b.status.replace('_', ' ')}</td>`;
        bt.appendChild(tr);
      });
      const st = document.querySelector('#replaySources tbody'); st.innerHTML = '';
      f.sources.forEach(s => {
        const tr = document.createElement('tr');
        const soc = s.soc !== null && s.soc !== undefined ? ` <span class="pill">SOC ${(s.soc * 100).toFixed(0)}%</span>` : '';
        tr.innerHTML = `<td>${s.id}${soc}</td><td class="num">${f1(s.offered)}</td><td class="num">${s.price.toFixed(2)}</td><td class="num">${f1(s.delivered)}</td>`;
        st.appendChild(tr);
      });
      const parts = [`Supply ${f1(f.supply)} kW vs requested ${f1(f.requested)} kW.`];
      if (f.rounds > 1) parts.push(`Supply short: buildings re-bid with scarcity ${(f.scarcity * 100).toFixed(0)}% (demand-response round).`);
      if (f.events.length) parts.push('Events: ' + f.events.join(', ') + '.');
      if (f.failure) parts.push('Market failure: ' + f.failure);
      document.getElementById('replay-market').innerText = parts.join(' ');
      Object.values(charts).forEach(c => c.draw());
    }

    document.getElementById('slot-slider').addEventListener('input', e => renderFrame(+e.target.value));
    document.getElementById('btn-play').addEventListener('click', () => {
      const btn = document.getElementById('btn-play');
      if (playTimer) { clearInterval(playTimer); playTimer = null; btn.innerHTML = '&#9654; Play'; return; }
      btn.innerHTML = '&#10074;&#10074; Pause';
      playTimer = setInterval(() => {
        const next = (currentSlot ?? -1) + 1;
        if (next >= frames.length) { clearInterval(playTimer); playTimer = null; btn.innerHTML = '&#9654; Play'; return; }
        renderFrame(next);
      }, 150);
    });
    init();
  </script>
</body>
</html>
"""


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server so requests don't block."""
    daemon_threads = True


def replay_frames(result: SimulationResult) -> list[dict[str, Any]]:
    """Compact per-slot view for the replay panel (raw recorded data, no metrics)."""
    frames = []
    for r in result.slots:
        allocated = {a.building_id: a.allocated_power_kw for a in r.clearing.allocations} if r.clearing else {}
        delivered = {d.source_id: d.delivered_kw for d in r.dispatch_results}
        buildings = []
        for building_id, bid in r.bids.items():
            s = r.settlements.get(building_id)
            buildings.append({
                "id": building_id,
                "requested": round(bid.requested_power_kw, 2),
                "critical": round(bid.critical_power_kw, 2),
                "allocated": round(allocated.get(building_id, 0.0), 2),
                "needed": round(s.actual_total_kw, 2) if s else None,
                "served": round(s.served_kw, 2) if s else None,
                "critical_short": round(s.critical_shortfall_kw, 2) if s else 0.0,
                "deferred": round(s.deferred_kw, 2) if s else 0.0,
                "status": s.status.value if s else "not settled",
            })
        frames.append({
            "i": r.slot_index,
            "t": r.time_slot.start.strftime("%a %H:%M"),
            "supply": round(r.supply_kw, 2),
            "requested": round(r.requested_kw, 2),
            "served": round(r.served_kw, 2),
            "scarcity": round(r.scarcity, 4),
            "rounds": r.rounds,
            "events": list(r.events_triggered),
            "failure": r.failure,
            "sources": [{
                "id": o.source_id,
                "type": o.source_type.value,
                "offered": round(o.available_kw, 2),
                "price": round(o.marginal_price, 2),
                "delivered": round(delivered.get(o.source_id, 0.0), 2),
                "soc": o.constraints.get("soc"),
            } for o in r.offers],
            "buildings": buildings,
        })
    return frames


def _metrics_payload(result: SimulationResult, agents: Any = None) -> dict[str, Any]:
    metrics = MetricsAggregator.compute(result, agents)
    payload = metrics.to_dict()
    payload["building_summaries"] = {bid: bs.to_dict() for bid, bs in result.building_summaries.items()}
    payload["event_log"] = [e.to_dict() for e in result.event_log]
    payload["series"] = metrics.series_dict()
    payload["mode"] = result.metadata.get("mode")
    payload["mode_label"] = result.metadata.get("mode_label", "")
    payload["scenario_description"] = result.metadata.get("scenario_description", "")
    return payload


class _DashboardState:
    """The run currently shown. Swapped atomically when the user re-runs."""

    def __init__(self, result: SimulationResult, agents: Any = None,
                 current: dict[str, str] | None = None) -> None:
        self.lock = threading.Lock()
        self.current = current or {}
        self._set(result, agents)

    def _set(self, result: SimulationResult, agents: Any) -> None:
        self.result = result
        self.metrics_json = _metrics_payload(result, agents)
        self.replay_json = replay_frames(result)
        self._slot_json: list[dict[str, Any]] | None = None

    def update(self, result: SimulationResult, agents: Any, current: dict[str, str]) -> None:
        with self.lock:
            self._set(result, agents)
            self.current = current

    @property
    def slot_json(self) -> list[dict[str, Any]]:
        if self._slot_json is None:  # full slot records are large; build only if asked for
            self._slot_json = [sr.to_dict() for sr in self.result.slots]
        return self._slot_json


def make_handler(result: SimulationResult, agents: Any = None,
                 runner: Callable[..., RunOutput] | None = None,
                 current: dict[str, str] | None = None):
    """Build the request handler. With ``runner`` the page offers run/compare controls."""
    state = _DashboardState(result, agents, current)

    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path
            if path in ("/", "/index.html"):
                body = _DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/metrics":
                self.send_json(state.metrics_json)
            elif path == "/api/replay":
                self.send_json(state.replay_json)
            elif path == "/api/data":
                self.send_json(state.slot_json)
            elif path == "/api/summary":
                self.send_json(state.result.summary_dict())
            elif path == "/api/options":
                self.send_json(_options(runner is not None, state.current))
            elif path == "/api/run" and runner is not None:
                self._run(query)
            elif path == "/api/compare" and runner is not None:
                self._compare(query)
            else:
                self.send_response(404)
                self.end_headers()

        def _run(self, query: dict[str, str]) -> None:
            from gridweave.coordinator.modes import get_mode
            from gridweave.coordinator.scenarios import get_scenario
            try:
                scenario, mode = get_scenario(query.get("scenario", "normal")), get_mode(query.get("mode", ""))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            out = runner(scenario.name, mode.name)
            _annotate(out)
            state.update(out.result, out.agents, {"scenario": scenario.name, "mode": mode.name})
            self.send_json({"ok": True, "scenario": scenario.name, "mode": mode.name})

        def _compare(self, query: dict[str, str]) -> None:
            from gridweave.coordinator.modes import MODES
            from gridweave.coordinator.scenarios import get_scenario
            try:
                scenario = get_scenario(query.get("scenario", "normal"))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            rows = []
            for name, mode in MODES.items():
                out = runner(scenario.name, name)
                m = MetricsAggregator.compute(out.result, out.agents)
                rows.append({
                    "mode": name, "label": mode.label,
                    "service_ratio": m.overall_service_ratio,
                    "critical_service_ratio": m.critical_service_ratio,
                    "critical_shortfall_kwh": round(m.total_critical_shortfall_kwh, 2),
                    "expired_kwh": round(m.total_expired_kwh, 2),
                    "fairness": m.fairness.jains_index_final,
                    "cost": m.total_procurement_cost,
                })
            self.send_json(rows)

    return DashboardHandler


def _options(interactive: bool, current: dict[str, str]) -> dict[str, Any]:
    from gridweave.coordinator.modes import MODES
    from gridweave.coordinator.scenarios import SCENARIOS
    return {
        "interactive": interactive,
        "current": current,
        "scenarios": [{"name": s.name, "description": s.description} for s in SCENARIOS.values()],
        "modes": [{"name": m.name, "label": m.label, "description": m.description} for m in MODES.values()],
    }


def _annotate(out: RunOutput) -> None:
    out.result.metadata["mode_label"] = out.mode.label
    out.result.metadata["scenario_description"] = out.scenario.description


def serve_dashboard(result: SimulationResult, host: str = "127.0.0.1", port: int = 8050) -> HTTPServer:
    """Serve a read-only dashboard for one finished run."""
    return ThreadedHTTPServer((host, port), make_handler(result))


def serve_interactive_dashboard(run: RunOutput, host: str = "127.0.0.1", port: int = 8050) -> HTTPServer:
    """Serve the dashboard with scenario/mode controls, mode comparison and replay.

    ``run`` is the initial run to display (from :func:`gridweave.coordinator.modes.run_simulation`).
    """
    from gridweave.coordinator.modes import run_simulation

    _annotate(run)

    def runner(scenario: str, mode: str) -> RunOutput:
        return run_simulation(scenario, mode)

    handler = make_handler(run.result, run.agents, runner=runner,
                           current={"scenario": run.scenario.name, "mode": run.mode.name})
    return ThreadedHTTPServer((host, port), handler)
