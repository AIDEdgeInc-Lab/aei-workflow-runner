# KPI Integrity Principle — "Raw counters first"

Applies to every AEI SDK library, every Velorona analysis, and every analysis prompt.

## Principle

A correct calculation does not guarantee a correct operational conclusion.
Never trust an aggregated KPI without checking its raw counters, its calculation logic, and its operational meaning.

Origin: operator feedback from a senior RF engineer. Two failure types with very different customer impact (voice drops and packet-session drops) were blended into one drop rate. The blended KPI looked acceptable while voice drops, which customers notice immediately, were getting worse. That is false calm.

## Three layers — check them in order

### Layer 1 — Measurement integrity (is the raw data real?)
- Are the expected counters present for every object and interval?
- Was any counter reset (e.g. after a software upgrade or reboot)?
- Is the data stale (repeated last-known values, frozen timestamps)?
- Are values inside valid physical/reporting ranges?
- Did the tooling itself alter the data (clipping, filling, dropping rows)? If so, report it. Never hide it.

### Layer 2 — KPI integrity (is the metric honest?)
- Is the formula the documented one, using the correct counters?
- Are different failure types or services blended into one number?
- Does aggregation (across time, sites, links, directions, bands) average away a local failure?
- Is the denominator meaningful (enough attempts/samples)?

### Layer 3 — Decision integrity (can an operator act on it?)
- Does the KPI reflect what the customer or the service actually experiences?
- Would the raw counters lead to a different conclusion than the KPI?
- What would an operations team do differently based on this number?

## Microwave links (hypotheses to verify per vendor, not facts)
A link reported as "up" with acceptable overall availability can still hide:
- a slow RSL / CINR decline toward the fade margin
- time spent in downshifted ACM modulation
- rising error counters in one direction only
- one weak hop inside an averaged path or region
Available counters differ by vendor and NMS. Confirm what exists in the real data before claiming detection.

## Rules for code and analysis
1. Show raw counters next to any derived KPI in outputs.
2. Flag blended or aggregated KPIs explicitly.
3. Never silently clip, fill, or drop data. Count it, report it, keep the original.
4. A missing input is an error or a visible flag, never a quiet pass.
5. Do not claim a library checks something unless a test proves it.

## Known gap — aei-3gpp-kpi-validator 0.1.2
- Clips out-of-range values (a Layer 1 risk: bad values can become valid-looking ones).
- `validate_handover` / `validate_nbiot_power_profile` return data unchanged when a column is missing.
- No formula checks, no counter consistency checks, no blending detection.
- 3GPP spec citations in `config/*.yml` are not yet verified.
