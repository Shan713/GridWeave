# Energy Supply Intelligence Architecture (Workstream 3)

**Author:** Person 3 — Lead Energy Systems and Supply-Agent Engineer  
**Workstream:** GridWeave Workstream 3  
**Status:** Production Implementation Complete  
**Contract Version:** 2.0  

---

## 1. Subsystem Overview

Workstream 3 of GridWeave designs and executes the **campus energy supply intelligence** across 15-minute operational market slots (96 slots/day). It coordinates three autonomous energy source agents behind a single aggregation and dispatch abstraction (`CampusSupplyProvider`) implementing the shared `SupplyProvider` protocol.

```mermaid
flowchart TD
    subgraph P3_Supply_Subsystem ["Workstream 3: Energy Supply Intelligence"]
        GA[Grid Supply Agent\n- TOU Tariffs\n- Outages & Ramp Limits\n- Import Metering]
        SA[Solar Energy Agent\n- Solar Geometry & GHI\n- Weather-Aware Forecasters\n- Curtailment Tracking]
        BA[Battery Storage Agent\n- SOC Dynamics\n- Efficiency Losses\n- Reserve Policy & Degradation]

        CSP[CampusSupplyProvider\n- Pure Offer Aggregation\n- SupplyDispatcher (Batch Atomic)\n- SupplyAccountant (Conservation)\n- SupplyEventHandler]

        GA -->|SupplyOffer| CSP
        SA -->|SupplyOffer| CSP
        BA -->|SupplyOffer| CSP
    end

    subgraph External_Integration ["System Integration"]
        P4[P4 Global Coordinator]
        P2[P2 AuctionEngine]
        P1[P1 Building Agents]
    end

    P4 -->|1. Request Offers| CSP
    CSP -->|2. [SupplyOffer]| P2
    P1 -->|3. [Bid]| P2
    P2 -->|4. ClearingResult| P4
    P4 -->|5. [DispatchRequest]| CSP
    CSP -->|6. Physical Dispatch| GA
    CSP -->|6. Physical Dispatch| SA
    CSP -->|6. Physical Dispatch| BA
    CSP -->|7. [DispatchResult]| P4
    CSP -->|8. Accounting & Telemetry| P4
```

---

## 2. Autonomous Source Agents: PEAS Formulation

For academic evaluation (Review 1), each energy source agent is formulated under the **PEAS (Performance measure, Environment, Actuators, Sensors)** framework:

| Agent | Performance Measure (P) | Environment (E) | Actuators (A) | Sensors (S) |
|---|---|---|---|---|
| **Grid Supply Agent** | • Minimize import expenditure during peak tiers<br>• Zero physical capacity violations<br>• Rapid response to blackout events | • External transmission grid<br>• Dynamic time-of-use tariffs<br>• Unplanned network outages | • Import throttle (kW)<br>• Capacity offer (kW)<br>• Emergency disconnect | • Time slot clock (`TimeSlot`)<br>• Grid interconnection voltage meter<br>• Tariff schedule / price feed |
| **Solar Energy Agent** | • Maximize clean renewable utilization (%)<br>• Minimize solar curtailment (kWh)<br>• Minimize forecast error (MAE, RMSE) | • Solar astronomical geometry<br>• Clear-sky atmospheric irradiance<br>• Stochastic cloud cover & weather | • Solar supply offer (kW)<br>• Inverter power dispatch<br>• Solar curtailment control | • Sun zenith / elevation angle<br>• Ambient irradiance (W/m²)<br>• Online clearness index ($k_t$) |
| **Battery Storage Agent** | • Minimize lifecycle degradation ($)<br>• Maximize peak tariff displacement<br>• Maintain reserve floor ($\text{SOC} \ge \text{SOC}_{\text{res}}$) | • Campus load fluctuation<br>• Market clearing price signals<br>• Electrochemical degradation | • Discharge dispatch offer (kW)<br>• Solar surplus charging throttle<br>• Reserve threshold lock | • Battery SOC voltage sensor<br>• Electrochemical temperature meter<br>• Cumulative cycle / throughput counter |

---

## 3. Environment Analysis

The campus energy supply environment exhibits five classic multi-agent properties:

1. **Partially Observable:** The Solar Agent observes local irradiance and past generation, but future cloud cover is stochastic. The Battery Agent observes local SOC and internal degradation, but future clearing prices depend on external building bids.
2. **Dynamic:** Irradiance shifts dynamically with cloud cover; grid tariffs change on time-of-use schedules; unexpected outages can sever grid imports instantaneously.
3. **Sequential:** Actions in slot $t$ directly constrain future slots: discharging the battery now reduces available stored energy during the evening peak.
4. **Stochastic:** Solar generation contains meteorological noise; utility tariffs may exhibit unexpected price spikes; building demand has stochastic components.
5. **Multi-Agent:** The supply agents must interact with P1's autonomous building agents and P2's auction mechanism, maintaining local state privacy and communicating purely via typed protocols.

---

## 4. Subsystem Components & Responsibilities

| Module | Core Responsibility |
|---|---|
| `gridweave.supply.grid_agent` | Models external electrical grid imports, time-of-use tariffs, scheduled maintenance, and unexpected blackouts. |
| `gridweave.supply.solar_agent` | Models physical PV generation based on solar elevation, GHI, cloud cover, and inverter efficiencies. |
| `gridweave.supply.battery_agent` | Models electrochemical battery storage with charge/discharge efficiencies, SOC bounds, and cell degradation. |
| `gridweave.supply.forecasting` | Provides multi-slot lookahead solar forecasting (persistence, seasonal, time-of-day, and weather-aware). |
| `gridweave.supply.optimization` | Provides rule-based heuristic control (Strategy A) and forecast-aware rolling-horizon optimization (Strategy B). |
| `gridweave.supply.dispatcher` | Pre-validates dispatch request batches for atomicity before mutating physical source states. |
| `gridweave.supply.accounting` | Enforces first-law energy conservation ($\sum \text{delivered} = \sum \text{dispatches}$) and financial auditing. |
| `gridweave.supply.events` | Manages dynamic environmental and infrastructure events (thunderstorms, blackouts, price spikes). |
| `gridweave.supply.provider` | Production `CampusSupplyProvider` implementing `interfaces.SupplyProvider`. |
