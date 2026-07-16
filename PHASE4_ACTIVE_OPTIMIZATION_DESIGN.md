# Phase 4: Active Optimization Loop — Design

**Goal:** Continuously measure authenticity in production, detect underperforming dimensions, iterate SOUL policies, and track progress toward 100% authentic character reasoning.

## Architecture

### High-Level Flow

```
New Episode Generated
    ↓
Automatic Measurement (phase4_active_optimization.py measure)
    ├→ Run automated audit (17 checks)
    ├→ Score 7 dimensions
    └→ Record in episode_evaluation.db
    ↓
Daily Monitoring Dashboard (phase4_active_optimization.py monitor)
    ├→ Display current performance by dimension
    ├→ Show trend arrows (improving/declining)
    └→ Alert on regressions (vs frozen benchmark)
    ↓
Weekly Iteration Suggestions (phase4_active_optimization.py suggest-iteration)
    ├→ Identify underperforming dimensions (mean < 2.0)
    ├→ Propose SOUL layer changes
    ├→ Prioritize by severity
    └→ Log to soul_iteration_log.yaml
    ↓
Test & Promote
    ├→ Implement suggested SOUL change
    ├→ Test on 3–5 episodes
    ├→ Check: does change improve target dimension?
    ├→ If yes: promote to production
    └→ If no: iterate or revert
    ↓
Benchmark Regression Check (phase4_active_optimization.py benchmark-check)
    ├→ Weekly test against frozen benchmark set
    ├→ Alert if ANY dimension drops > 0.5 points
    └→ Halt optimization if regression found (investigate cause)
```

## CLI Commands

### Measurement
```bash
# Measure single episode after generation
python phase4_active_optimization.py measure 42

# Batch measure 10 most recent episodes
python phase4_active_optimization.py batch-measure --count 10
```

Output: Scores per dimension, failure tags, artifact stored in `episode_evaluation.db` and `optimization_monitoring.yaml`.

### Monitoring
```bash
# Display dashboard
python phase4_active_optimization.py monitor
```

Shows:
- Average score per dimension (sorted)
- Trend direction (↑ improving, ↓ declining, → stable)
- Delta vs baseline
- Alerts on regressions

### Analysis
```bash
# Show trend for specific dimension
python phase4_active_optimization.py trend "evidence contingency"

# Suggest iterations based on underperformance
python phase4_active_optimization.py suggest-iteration

# Check vs frozen benchmark
python phase4_active_optimization.py benchmark-check

# Full report
python phase4_active_optimization.py report
```

## Iteration Workflow

### 1. Identify Problem (Automated)

After 5–10 episodes measured, analysis detects underperforming dimension:

```
Evidence Contingency: mean 0.5 (target ≥ 3.0)
→ "Hosts still not grounding reasoning in paper evidence"
```

### 2. Propose Change (Automated Suggestion)

Based on dimension and problem:

| Dimension | Low Score | Suggested Layer | Action |
|-----------|-----------|-----------------|--------|
| Evidence Contingency | < 2.0 | LENS_POLICY | Increase engagement_drives; guide to ground in specific claims |
| Character Appraisal | < 2.0 | LENS_POLICY | Increase evidence_trust_ranking differences per host |
| Belief Continuity | < 2.0 | CONVERSATION_POLICY | Add explicit belief-update instructions |
| Naturalism | < 2.0 | VOICE_REALIZATION | Reduce template markers; vary sentence structure |
| Agency/Asymmetry | < 2.0 | CONVERSATION_POLICY | Reduce round-robin scheduling instructions |

### 3. Implement Change (Manual)

Edit the SOUL layer YAML:

**Example:** Evidence Contingency is low.

```yaml
# SOUL_LENS_POLICY.yaml
Hal:
  engagement_drives:
    # OLD: just 4 drives
    # NEW: add more specific evidence-grounding drives
    - "Finding the specific assumption that breaks this design"
    - "What evidence validates this claim vs alternatives?"
    - "Where's the weakest evidence in their chain?"
```

### 4. Test Change (Automated)

Generate 3–5 episodes with new SOUL version. Measure and compare:

```
Before: Evidence Contingency = 0.5
After:  Evidence Contingency = 1.8

Improvement: +1.3 points ✓ Promote to production
```

### 5. Promote & Monitor (Automated)

If test passes:
- Increment SOUL version (e.g., v1 → v2)
- Deploy to production
- Continue monitoring
- Update baseline for regression detection

If test fails:
- Revert change
- Document failure
- Propose alternative
- Retry

## Measurement Strategy

### What Gets Measured

Every new episode:
1. **Automated audit** — 17 pattern checks
2. **Rubric scoring** — 7 dimensions (0-4 scale)
3. **Metadata recording** — Title, date, SOUL version, pipeline version, failure tags

### When & How

**Triggered:** After episode generation completes
```python
# In podcast.py or gen-podcast.py
if episode_published:
    import phase4_active_optimization
    phase4_active_optimization.measure_episode(episode_id)
```

**Optional:** Weekly batch measurement of recent episodes
```bash
python phase4_active_optimization.py batch-measure --count 10
```

### Storage

Two formats (complementary):
- **Database:** `episode_evaluation.db` (structured, queryable)
- **YAML log:** `optimization_monitoring.yaml` (human-readable, append-only)

## Regression Detection

### Release Gate: Benchmark Check

Every week, test frozen benchmark set against historical baseline:

```bash
python phase4_active_optimization.py benchmark-check
```

**Pass criteria:**
- No dimension drops > 0.5 points
- No new failure tags appear on benchmark papers
- Average score stable or improving

**Fail criteria:**
- Any dimension regression > 0.5 points → ALERT
- Investigate root cause:
  - Did recent SOUL change break something?
  - Did pipeline change introduce bug?
  - Did data quality shift?

**Action on regression:**
- Pause optimization
- Revert recent SOUL change
- Run diagnostics
- Resume after fix

## Iteration Priorities

Dimensions to optimize in order:

1. **Evidence Contingency** (CRITICAL)
   - Current: 0.0 (all episodes)
   - Target: ≥ 3.5
   - Why: Foundational; hosts must ground reasoning

2. **Character-Contingent Appraisal** (HIGH)
   - Current: 2.0 (neutral)
   - Target: ≥ 3.5
   - Why: Character drives only work if manifest in different reasoning

3. **Belief Continuity** (HIGH)
   - Current: 2.0 (neutral)
   - Target: ≥ 3.5
   - Why: Authenticity requires hosts to remember and update

4. **Anti-Caricature Coherence** (MEDIUM)
   - Current: 2.0 (neutral)
   - Target: ≥ 3.0
   - Why: Prevent stereotyping

5. **Conversational Causality** (MEDIUM)
   - Current: 2.0 (neutral)
   - Target: ≥ 3.0
   - Why: Improve dialogue quality

6. **Agency/Asymmetry** (MEDIUM)
   - Current: 2.0 (neutral)
   - Target: ≥ 3.0
   - Why: Natural participation patterns

7. **Naturalism** (LOW)
   - Current: 2.0 (neutral)
   - Target: ≥ 2.5 (less critical)
   - Why: Refinement; not foundational to authenticity

## SOUL Version Management

Each SOUL layer tracks versions:

```yaml
soul_version_hal: "hal_v2_2026-07-15"
soul_version_ada: "ada_v1_2026-07-14"
soul_version_vera: "vera_v1_2026-07-14"
```

Structure:
```
<character>_v<N>_<date>
├─ v1 = Original (baseline)
├─ v2 = First evidence-grounding iteration
├─ v3 = Character-appraisal refinement
└─ vN = Production version
```

Each iteration documented:

```yaml
iteration_log:
  - version: hal_v2
    date: 2026-07-15
    dimension_target: evidence_contingency
    change: "Added 3 evidence-grounding engagement drives"
    test_result: "Improved 0.0 → 1.8 (+1.8 points)"
    decision: "PROMOTED"
    
  - version: hal_v3
    date: 2026-07-20
    dimension_target: character_appraisal
    change: "Adjusted lens_policy evidence_trust_ranking"
    test_result: "No improvement, 2.0 → 2.0"
    decision: "REVERTED"
```

## Timeline & Cadence

| Interval | Activity | Command |
|----------|----------|---------|
| Per episode | Automatic measurement | `measure <id>` (auto-triggered) |
| Daily | Monitor dashboard | `monitor` |
| Weekly | Batch measure + suggest | `batch-measure` + `suggest-iteration` |
| Weekly | Benchmark regression check | `benchmark-check` |
| Monthly | Full report + archive | `report` |
| Ongoing | Test & promote | Manual (based on suggestions) |

## Success Metrics

### Primary
- Evidence Contingency: improve from 0.0 to ≥ 3.5
- Character Appraisal: improve from 2.0 to ≥ 3.5
- Belief Continuity: improve from 2.0 to ≥ 3.5

### Secondary
- No regressions on benchmark set
- Average score trending up over time
- Failure tag frequency decreasing

### Ultimate
- Podcast episodes measurably more authentic
- Character reasoning evident in transcripts
- Human listeners prefer treatment over control
- Authenticity becomes sustainable (not brittle)

## Fail-Safe Mechanisms

### 1. Benchmark Gate
If any benchmark dimension drops > 0.5, optimization pauses.

### 2. Revert Policy
Any SOUL change that doesn't improve target dimension or degrades others: auto-revert.

### 3. Monitoring Alert
If failure tag frequency increases or new tags appear, investigate immediately.

### 4. Human Approval
SOUL_CORE changes require explicit review (changes to identity/values).
LENS/CONVERSATION/VOICE changes can auto-promote if test passes.

## Integration Points

To wire Phase 4 into production:

### 1. In `podcast.py` (after episode generation):
```python
from phase4_active_optimization import measure_episode

# After writing episode to database
if podcast_id:
    try:
        measure_episode(podcast_id)
    except Exception as e:
        logger.warning(f"Measurement failed: {e}")
```

### 2. In `gen-podcast.py` (CLI):
```bash
# New command
gen-podcast.py monitor          # Show dashboard
gen-podcast.py suggest-iteration # Propose SOUL changes
gen-podcast.py benchmark-check   # Test vs frozen set
```

### 3. In cron/systemd (weekly):
```bash
# Weekly batch measurement + report
systemctl start podcast-weekly-measurement.service
```

## What Makes Phase 4 Different

### vs. Manual Tuning
- Automated detection (no guesswork)
- Data-driven (measured, not intuitive)
- Trackable (every change logged with before/after)

### vs. Single Experiment
- Continuous feedback loop (not one-off test)
- Regression detection (prevents degradation)
- Adaptive (responds to actual performance)

### vs. Unreliable Metrics
- Multi-dimensional (can't game single score)
- Anchored (behavioral reference points)
- Release gated (all criteria must pass)

## Known Limitations

1. **Scoring functions are placeholders** — Once real LLM critic deployed, scores will be more nuanced
2. **Frozen benchmark may become stale** — Periodically refresh with new papers
3. **SOUL changes are manual** — Could automate prompt generation from rubric suggestions
4. **No multi-agent testing** — Each SOUL version tested in isolation; no interaction effects

## Next Steps (After Phase 3 Validation)

1. **Integrate into gen-podcast.py** — Wire `measure_episode()` trigger
2. **Deploy monitoring** — Weekly dashboard + alerts
3. **Test first SOUL change** — Implement evidence-grounding improvement
4. **Measure effect** — Compare before/after on target dimension
5. **Promote or iterate** — If improvement > 0.5, promote; else retry
6. **Monitor regression** — Weekly benchmark check for 4 weeks
7. **Publish results** — Counterfactual + optimization results paper
