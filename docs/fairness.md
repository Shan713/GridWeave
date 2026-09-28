# GridWeave: Fairness Metrics, Deprivation Tracking, and Starvation Prevention

## 1. The Fairness Challenge in Energy Microgrids

In an unconstrained competitive market, buildings with high willingness-to-pay (e.g., well-funded executive laboratories or data centers) could outbid low-budget facilities (e.g., student hostels or general libraries) across consecutive time slots.
This results in **starvation**:
* Low-budget facilities suffer prolonged outages.
* Backlogged deferred energy accumulates beyond allowable deadlines.
* Occupant comfort and operational continuity are severely violated.

To ensure academic and operational rigor, GridWeave implements formal equity metrics and an active anti-starvation mechanism.

---

## 2. Jain's Fairness Index

Service equity across all participating buildings is quantitatively evaluated using **Jain's Fairness Index**:

$$J(s_1, s_2, \dots, s_n) = \frac{\left(\sum_{i=1}^n s_i\right)^2}{n \sum_{i=1}^n s_i^2}$$

Where $s_i \in [0, 1]$ is the satisfaction ratio for building $i$:
$$s_i = \frac{x_i}{R_i}$$

### Mathematical Properties:
* **Boundedness**: $J \in \left[\frac{1}{n}, 1.0\right]$.
* **Perfect Equality**: If all buildings receive an identical satisfaction ratio (e.g., all get $80\%$), $J = 1.0$.
* **Extreme Inequality**: If 1 out of $n$ buildings receives power while $n-1$ buildings receive $0$, $J = \frac{1}{n}$ ($0.25$ for $4$ buildings).
* **Continuous & Differentiable**: Sensitive to any marginal transfer of power between buildings.

---

## 3. Multi-Slot Deprivation Accounting

The `FairnessTracker` maintains a persistent historical state across consecutive time slots:

1. **Starvation Event**: A building is flagged as starved in slot $t$ if:
   $$\frac{x_i(t)}{R_i(t)} < \text{threshold} \quad (\text{default } 0.50)$$
2. **Consecutive Starved Slots ($k_i$)**: Increments by $1$ each slot the building remains below the threshold; resets to $0$ as soon as the building receives $\ge 50\%$ service.
3. **Normalized Deprivation Factor ($D_i$)**:
   $$D_i = \min\left(1.0, \frac{k_i}{K_{\text{max}}}\right) \quad (\text{where } K_{\text{max}} = 5 \text{ slots})$$

---

## 4. Anti-Starvation Mechanism: Dynamic Priority Boosting

The calculated deprivation factor $D_i$ feeds directly into `BidScorer`:

$$\text{Score}_i = w_{\text{crit}} \cdot C_i + w_{\text{prio}} \cdot P_i + w_{\text{wtp}} \cdot \tilde{W}_i + \mathbf{w_{\text{fair}} \cdot D_i} - w_{\text{flex}} \cdot F_i$$

With $w_{\text{fair}} = 0.15$:
* In slot 1 of a shortage, a low-WTP building ($D_i = 0$) may lose flexible power to a high-WTP building.
* By slot 3 of repeated shortages, $D_i = 0.60$, adding $0.09$ to its composite score.
* By slot 5, $D_i = 1.0$, adding $0.15$, effectively elevating its ranking above purely economic bidders.
* **Result**: Starvation is broken autonomously without human administrative intervention.

---

## 5. Empirical Starvation Test Results

In Scenario 7 of our controlled testbed:
* Hostel C (low priority $0.45$, low WTP $6.5$) was subjected to consecutive campus scarcity.
* **Without Deprivation Boost**: Hostel C is starved repeatedly (0 kW flexible power; Jain's Index drops to $0.73$).
* **With Deprivation Boost ($D_i = 0.80$)**: Hostel C's composite score rises from $0.461$ to $0.581$, successfully winning flexible allocation; Jain's Index recovers to **$0.9845$**.
