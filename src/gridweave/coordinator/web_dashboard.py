"""Lightweight, interactive Web Dashboard for GridWeave Campus Energy Simulation.

Uses Python standard library (`http.server`) to serve a modern responsive HTML/JS interface
with Chart.js visualization, real-time KPI metrics, per-building breakdown, and scenario event logs.
Zero third-party runtime dependencies required.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import TYPE_CHECKING, Any

from gridweave.coordinator.metrics import MetricsAggregator

if TYPE_CHECKING:
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
      --bg-color: #0b0f19;
      --card-bg: #151c2c;
      --border-color: #2a364f;
      --text-main: #e2e8f0;
      --text-muted: #94a3b8;
      --accent-blue: #3b82f6;
      --accent-green: #10b981;
      --accent-amber: #f59e0b;
      --accent-red: #ef4444;
      --accent-purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    body { background: var(--bg-color); color: var(--text-main); padding: 20px; line-height: 1.5; }
    header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid var(--border-color); margin-bottom: 24px; }
    h1 { font-size: 1.5rem; color: #fff; display: flex; align-items: center; gap: 10px; }
    .badge { font-size: 0.75rem; padding: 4px 8px; border-radius: 4px; background: var(--accent-blue); color: white; text-transform: uppercase; font-weight: bold; }
    .grid-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .kpi-card { background: var(--card-bg); padding: 18px; border-radius: 8px; border: 1px solid var(--border-color); }
    .kpi-title { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 6px; }
    .kpi-val { font-size: 1.6rem; font-weight: bold; color: #fff; }
    .kpi-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; }
    .charts-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }
    @media (max-width: 900px) { .charts-grid { grid-template-columns: 1fr; } }
    .chart-box { background: var(--card-bg); padding: 20px; border-radius: 8px; border: 1px solid var(--border-color); min-width: 0; overflow-x: auto; }
    .chart-box h2 { font-size: 1rem; margin-bottom: 16px; color: var(--text-main); }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid var(--border-color); font-size: 0.9rem; }
    th { color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }
    .event-log { max-height: 250px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; }
    .event-item { padding: 8px 12px; border-bottom: 1px solid var(--border-color); display: flex; gap: 12px; align-items: center; }
    .event-tag { color: var(--accent-amber); font-weight: bold; }
  </style>
</head>
<body>
  <header>
    <h1>GridWeave Campus Dashboard <span class="badge" id="scen-name">Loading...</span></h1>
    <div id="sim-info" style="color: var(--text-muted); font-size: 0.9rem;"></div>
  </header>

  <section class="grid-kpis">
    <div class="kpi-card"><div class="kpi-title">Total Demand</div><div class="kpi-val" id="kpi-demand">0 kWh</div><div class="kpi-sub" id="kpi-served-sub">Served: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Service Ratio</div><div class="kpi-val" id="kpi-svc-ratio">0%</div><div class="kpi-sub" id="kpi-unserved-sub">Unserved: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Critical Load Served</div><div class="kpi-val" id="kpi-crit-ratio">0%</div><div class="kpi-sub" id="kpi-crit-sub">Shortfall: 0 kWh</div></div>
    <div class="kpi-card"><div class="kpi-title">Jain's Fairness Index</div><div class="kpi-val" id="kpi-fairness">0.0000</div><div class="kpi-sub">Min 0.0 — Max 1.0</div></div>
    <div class="kpi-card"><div class="kpi-title">Battery</div><div class="kpi-val" id="kpi-battery">0 kWh</div><div class="kpi-sub" id="kpi-battery-sub">Discharged · recharged from grid</div></div>
    <div class="kpi-card"><div class="kpi-title">Re-Auctions</div><div class="kpi-val" id="kpi-reauctions">0</div><div class="kpi-sub" id="kpi-events-sub">Events: 0</div></div>
  </section>

  <section class="charts-grid">
    <div class="chart-box">
      <h2>Demand & Generation Profile over Time (kW)</h2>
      <canvas id="timeSeriesChart" height="120"></canvas>
    </div>
    <div class="chart-box">
      <h2>Energy Served vs Shortfall by Building</h2>
      <canvas id="buildingChart" height="240"></canvas>
    </div>
  </section>

  <section class="charts-grid" id="battery-section">
    <div class="chart-box">
      <h2>Battery: State of Charge &amp; Discharge</h2>
      <canvas id="batteryChart" height="120"></canvas>
    </div>
    <div class="chart-box">
      <h2>When Is the Battery Used? Offer Price vs Grid Price</h2>
      <canvas id="priceChart" height="240"></canvas>
      <div class="kpi-sub" style="margin-top: 8px;">The battery offers at its wear cost plus the cost of its stored energy. The market uses it when the grid price is higher (evening peak), or when the grid is out.</div>
    </div>
  </section>

  <section class="charts-grid">
    <div class="chart-box">
      <h2>Per-Building Performance Breakdown</h2>
      <table id="bldgTable">
        <thead>
          <tr>
            <th>Building ID</th>
            <th>Demand (kWh)</th>
            <th>Served (kWh)</th>
            <th>Critical Short (kWh)</th>
            <th>Curtailed (kWh)</th>
            <th>Expired (kWh)</th>
            <th>Deferred* (kWh)</th>
            <th>Service Ratio</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="kpi-sub" style="margin-top: 8px;">* Deferred is a flow, not an outcome: deferred energy is later served, expires, or is still queued. Demand = Served + Critical Short + Curtailed + Expired + Still Queued.</div>
    </div>
    <div class="chart-box">
      <h2>Scenario Event Log</h2>
      <div class="event-log" id="eventLog">No events recorded.</div>
    </div>
  </section>

  <script>
    async function loadData() {
      const resM = await fetch('/api/metrics');
      const metrics = await resM.json();
      const resD = await fetch('/api/data');
      const slotData = await resD.json();

      document.getElementById('scen-name').innerText = metrics.scenario_name || 'Simulation';
      document.getElementById('sim-info').innerText = `${metrics.n_buildings} Buildings | ${metrics.n_slots} Slots (${metrics.resolution_minutes}m) | Seed ${metrics.seed}`;

      document.getElementById('kpi-demand').innerText = (metrics.total_demand_kwh || 0).toFixed(1) + ' kWh';
      document.getElementById('kpi-served-sub').innerText = `Served: ${(metrics.total_served_kwh || 0).toFixed(1)} kWh`;
      document.getElementById('kpi-svc-ratio').innerText = ((metrics.overall_service_ratio || 0) * 100).toFixed(1) + '%';
      const f1 = v => (v || 0).toFixed(1);
      document.getElementById('kpi-unserved-sub').innerText =
        `Unserved ${f1(metrics.total_unserved_kwh)} kWh: critical ${f1(metrics.total_critical_shortfall_kwh)}, ` +
        `curtailed ${f1(metrics.total_curtailed_kwh)}, expired ${f1(metrics.total_expired_kwh)}, ` +
        `queued ${f1(metrics.total_backlog_remaining_kwh)}`;
      document.getElementById('kpi-crit-ratio').innerText = ((metrics.critical_service_ratio ?? 1) * 100).toFixed(1) + '%';
      document.getElementById('kpi-crit-sub').innerText =
        `Shortfall: ${f1(metrics.total_critical_shortfall_kwh)} kWh in ${metrics.total_critical_shortfall_events || 0} events`;
      document.getElementById('kpi-fairness').innerText = (metrics.fairness ? metrics.fairness.jains_index_final : 1.0).toFixed(4);
      document.getElementById('kpi-reauctions').innerText = metrics.market ? metrics.market.re_auction_slots : 0;
      document.getElementById('kpi-events-sub').innerText = `Events: ${metrics.n_events || 0}`;

      // Building table
      const tbody = document.querySelector('#bldgTable tbody');
      tbody.innerHTML = '';
      if (metrics.building_summaries) {
        Object.entries(metrics.building_summaries).forEach(([id, bm]) => {
          const tr = document.createElement('tr');
          const demand = bm.demand_kwh || 0;
          const served = bm.served_kwh || 0;
          const svcRatio = bm.service_ratio ?? (demand > 0 ? served / demand : 1.0);

          tr.innerHTML = `<td><strong>${id}</strong></td>
                          <td>${demand.toFixed(1)}</td>
                          <td>${served.toFixed(1)}</td>
                          <td>${(bm.critical_shortfall_kwh || 0).toFixed(1)} (${bm.critical_shortfall_events || 0})</td>
                          <td>${(bm.curtailed_kwh || 0).toFixed(1)}</td>
                          <td>${(bm.expired_kwh || 0).toFixed(1)}</td>
                          <td>${(bm.deferred_kwh || 0).toFixed(1)}</td>
                          <td><strong>${(svcRatio * 100).toFixed(1)}%</strong></td>`;
          tbody.appendChild(tr);
        });
      }

      // Time series chart
      const labels = slotData.map(s => `Slot ${s.slot_index}`);
      const reqKW = slotData.map(s => s.requested_kw || 0);
      const srvKW = slotData.map(s => s.served_kw || 0);
      const supplyKW = slotData.map(s => s.supply_kw || 0);

      new Chart(document.getElementById('timeSeriesChart'), {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            { label: 'Requested kW', data: reqKW, borderColor: '#ef4444', borderWidth: 2, fill: false, pointRadius: 0 },
            { label: 'Served kW', data: srvKW, borderColor: '#10b981', borderWidth: 2, fill: false, pointRadius: 0 },
            { label: 'Total Supply kW', data: supplyKW, borderColor: '#f59e0b', borderDash: [4, 4], fill: false, pointRadius: 0 }
          ]
        },
        options: {
          responsive: true,
          scales: { y: { beginAtZero: true, grid: { color: '#2a364f' } }, x: { grid: { color: '#2a364f' } } },
          plugins: { legend: { labels: { color: '#e2e8f0' } } }
        }
      });

      // Building Chart
      if (metrics.building_summaries) {
        const bldgNames = Object.keys(metrics.building_summaries);
        // Segments are end states that partition each building's demand exactly.
        const seg = key => bldgNames.map(k => metrics.building_summaries[k][key] || 0);

        new Chart(document.getElementById('buildingChart'), {
          type: 'bar',
          data: {
            labels: bldgNames,
            datasets: [
              { label: 'Served', data: seg('served_kwh'), backgroundColor: '#10b981' },
              { label: 'Critical shortfall', data: seg('critical_shortfall_kwh'), backgroundColor: '#ef4444' },
              { label: 'Curtailed', data: seg('curtailed_kwh'), backgroundColor: '#f59e0b' },
              { label: 'Expired', data: seg('expired_kwh'), backgroundColor: '#8b5cf6' },
              { label: 'Still queued', data: seg('final_backlog_kwh'), backgroundColor: '#3b82f6' }
            ]
          },
          options: {
            responsive: true,
            scales: { y: { stacked: true, beginAtZero: true, grid: { color: '#2a364f' } }, x: { stacked: true, grid: { color: '#2a364f' } } },
            plugins: { legend: { labels: { color: '#e2e8f0' } } }
          }
        });
      }

      // Battery section
      const series = metrics.series || {};
      const hasBattery = (series.battery_soc || []).some(v => v !== null);
      document.getElementById('kpi-battery').innerText = `${f1(metrics.total_battery_discharge_kwh)} kWh`;
      document.getElementById('kpi-battery-sub').innerText = hasBattery
        ? `Discharged · ${f1(metrics.total_grid_to_battery_kwh)} kWh recharged from grid off-peak`
        : 'No battery in this run';
      if (!hasBattery) {
        document.getElementById('battery-section').style.display = 'none';
      } else {
        const axisColor = { color: '#2a364f' };
        new Chart(document.getElementById('batteryChart'), {
          data: {
            labels: labels,
            datasets: [
              { type: 'line', label: 'State of charge (%)', data: series.battery_soc.map(v => v === null ? null : v * 100),
                borderColor: '#8b5cf6', borderWidth: 2, pointRadius: 0, yAxisID: 'soc' },
              { type: 'bar', label: 'Battery discharge (kW)', data: series.battery_discharge_kw,
                backgroundColor: '#f59e0b', yAxisID: 'kw' }
            ]
          },
          options: {
            responsive: true,
            scales: {
              soc: { position: 'left', min: 0, max: 100, title: { display: true, text: 'SOC %', color: '#94a3b8' }, grid: axisColor },
              kw: { position: 'right', beginAtZero: true, title: { display: true, text: 'kW', color: '#94a3b8' }, grid: { drawOnChartArea: false } },
              x: { grid: axisColor }
            },
            plugins: { legend: { labels: { color: '#e2e8f0' } } }
          }
        });
        new Chart(document.getElementById('priceChart'), {
          type: 'line',
          data: {
            labels: labels,
            datasets: [
              { label: 'Grid price', data: series.grid_price, borderColor: '#3b82f6', borderWidth: 2, pointRadius: 0, stepped: true },
              { label: 'Battery offer price', data: series.battery_offer_price, borderColor: '#8b5cf6', borderWidth: 2, pointRadius: 0, stepped: true }
            ]
          },
          options: {
            responsive: true,
            scales: { y: { beginAtZero: true, title: { display: true, text: 'per kWh', color: '#94a3b8' }, grid: axisColor }, x: { grid: axisColor } },
            plugins: { legend: { labels: { color: '#e2e8f0' } } }
          }
        });
      }

      // Event Log
      const evLogEl = document.getElementById('eventLog');
      if (metrics.event_log && metrics.event_log.length > 0) {
        evLogEl.innerHTML = '';
        metrics.event_log.forEach(e => {
          const div = document.createElement('div');
          div.className = 'event-item';
          div.innerHTML = `<span class="event-tag">[Slot ${e.slot_index}]</span> <span><strong>${e.event_type}</strong> — ${e.description || ''}</span>`;
          evLogEl.appendChild(div);
        });
      } else {
        evLogEl.innerText = 'No events recorded.';
      }
    }
    loadData();
  </script>
</body>
</html>
"""


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server so requests don't block."""
    daemon_threads = True


def make_handler(result: SimulationResult):
    metrics = MetricsAggregator.compute(result)
    slot_json = [sr.to_dict() for sr in result.slots]
    metrics_json = metrics.to_dict()
    metrics_json["building_summaries"] = {
        bid: bs.to_dict() for bid, bs in result.building_summaries.items()
    }
    metrics_json["event_log"] = [e.to_dict() for e in result.event_log]
    metrics_json["series"] = metrics.series_dict()

    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send_json(self, data: Any) -> None:
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                body = _DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/metrics":
                self.send_json(metrics_json)
            elif self.path == "/api/data":
                self.send_json(slot_json)
            elif self.path == "/api/summary":
                self.send_json(result.summary_dict())
            else:
                self.send_response(404)
                self.end_headers()

    return DashboardHandler


def serve_dashboard(result: SimulationResult, host: str = "127.0.0.1", port: int = 8050) -> HTTPServer:
    """Create and return an HTTPServer serving the simulation dashboard for *result*."""
    handler_cls = make_handler(result)
    server = ThreadedHTTPServer((host, port), handler_cls)
    return server
