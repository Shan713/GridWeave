# GridWeave: Market Model & Economic Clearing Principles

## 1. Academic Scope & Honest Distinction

GridWeave is an **auction-inspired multi-agent energy management simulation** for a university campus. It is **not** a real-world commercial electricity wholesale market (such as Nord Pool or CAISO).

In our campus context:
* The market provides a **coordination and negotiation mechanism** to arbitrate scarce physical energy.
* **Willingness to pay (WTP)** represents an internal administrative valuation/budget rather than fiat currency.
* The auction guarantees that physical life-safety loads are never sacrificed for commercial profit.

---

## 2. Separation of Physical Quantities from Strategic Signals

A foundational tenet of GridWeave's P1-P2 contract is the strict separation between:
1. **Physical Engineering Quantities**:
   * `requested_power_kw`: Total forecasted power needed.
   * `critical_power_kw`: Non-negotiable life-safety, server, or experiment equipment load.
   * `minimum_power_kw`: Critical load plus basic operational lighting and baseline HVAC.
   * `flexible_power_kw`: Curtailable or deferrable load ($\text{requested} - \text{critical}$).
2. **Strategic Economic Signals**:
   * `priority_score`: Calculated urgency and institutional importance $\in [0, 1]$.
   * `willingness_to_pay`: Valuation per kWh $\in [0, \text{maximum\_price}]$.
   * `voluntary_reduction_kw`: Demand response reduction offered during scarcity rounds.

**The market mechanism never invents or alters physical demand quantities.** It only determines the allocated power $x_i \in [0, \text{requested}_i]$.

---

## 3. Economic Merit-Order Supply Dispatch

Campus supply is provided by heterogeneous sources (P3) with different capacities and marginal generation costs:
* **Solar PV**: Marginal cost $\approx 0.0$ currency/kWh.
* **Battery Storage**: Marginal cost $\approx 7.0$ currency/kWh (reflecting cycle degradation and reserve opportunity cost).
* **Campus Main Grid**: Marginal cost $\approx 10.0$ to $12.0$ currency/kWh (tariff reflecting time-of-day utility rates).

When total market allocation $A = \sum x_i$ is determined, `merit_order_dispatch` sorts available offers by `marginal_price` ascending:
$$\text{Source}_1 \to \text{Source}_2 \to \dots \to \text{Source}_k$$
Cheapest sources are exhausted first. Dispatched power is proportionally allocated to building `supply_mix` mappings, ensuring exact energy conservation ($\sum \text{Allocated} == \sum \text{Dispatched}$).

---

## 4. Market Pricing Mechanisms

GridWeave supports two transparent pricing models:
1. **Pay-as-Bid Pricing**: Each building's clearing price is set to its offered `willingness_to_pay` (or source blend cost). This mirrors internal cost-center accounting.
2. **Uniform Marginal Clearing Price**: All winning bids clear at the marginal supply offer price (the price of the last dispatched kilowatt). If total demand $\le$ total zero-cost solar generation, the clearing price is $0.0$.

---

## 5. Re-Auction & Demand Response Semantics

When initial demand $\sum \text{requested}_i > \sum \text{available}_j$:
1. The Coordinator detects scarcity ratio $s = 1 - \frac{\text{Supply}}{\text{Demand}}$.
2. Buildings receive $s$ and invoke P1's `generate_bid(BidContext(slot, scarcity=s))`.
3. Flexible loads are trimmed according to the building's voluntary reduction policy, creating revision $r_1$.
4. The auction executes `re_auction(revised_bids=...)`, replacing revision $r_0$ with $r_1$ and settling against the refined demand.
