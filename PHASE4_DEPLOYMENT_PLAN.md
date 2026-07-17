# Phase 4: Deployment Plan

**Status:** Ready to deploy immediately upon Phase 3 validation (PASS gate)

## Deployment Sequence

### 1. Integration with Generation Pipeline

**File:** `podcast.py` (after episode generation)

```python
# Around line where episode is saved to database:
if podcast_id:
    from phase4_active_optimization import measure_episode
    try:
        measurement = measure_episode(podcast_id)
        print(f"[Optimization] Episode {podcast_id} measured: avg={measurement['avg_score']:.1f}")
    except Exception as e:
        print(f"[Optimization] Measurement failed: {e}")
```

**Effect:** Every new episode automatically measured and scored.

### 2. Wire Monitoring Dashboard

**Setup:**
```bash
# Run daily monitoring (add to cron or systemd timer)
0 9 * * * cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py batch-measure --count 10 && \
  python phase4_active_optimization.py monitor > logs/daily-monitoring.txt
```

**Output:** Daily dashboard shows authenticity trends by dimension

### 3. Weekly Iteration Suggestions

**Setup:**
```bash
# Run every Friday
0 17 * * 5 cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py suggest-iteration > logs/iteration-suggestions.txt
```

**Output:** Automated suggestions for SOUL policy changes

### 4. Benchmark Regression Detection

**Setup:**
```bash
# Run weekly, fail-safe
0 0 * * 0 cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py benchmark-check > logs/regression-check.txt && \
  [ $? -ne 0 ] && echo "REGRESSION DETECTED" | mail -s "Authenticity Regression" admin@example.com
```

**Output:** Weekly regression test, alert on failures

### 5. Manual Iteration Workflow

When suggestions are generated:

1. **Review suggestion:**
   ```bash
   cat logs/iteration-suggestions.txt
   ```

2. **Implement SOUL change** (e.g., edit `SOUL_LENS_POLICY.yaml`)

3. **Test on 3-5 episodes:**
   ```bash
   python phase4_active_optimization.py batch-measure --count 5
   ```

4. **Check improvement:**
   ```bash
   python phase4_active_optimization.py report
   ```

5. **Release gate decision:**
   - If gates pass (all criteria met) → promote to production
   - If gates fail → revert and iterate

### 6. Monitoring Dashboard (Admin Interface)

**Create simple HTML dashboard** showing:
- Current scores by dimension (bar chart)
- Trend lines (7-day, 30-day)
- Regression alerts (red flag if any dimension drops > 0.5)
- Recent suggestions
- Last benchmark test results

**Template:**
```html
<h2>Authenticity Monitoring Dashboard</h2>

<div class="metrics-grid">
  <div class="metric">
    <h3>Evidence Contingency</h3>
    <div class="score">0.0</div> <!-- To be filled by Phase 3 validation -->
    <div class="trend">↑ +0.3 (7-day)</div>
  </div>
  <!-- Repeat for 7 dimensions -->
</div>

<div class="recent-suggestions">
  <h3>Latest Iteration Suggestions</h3>
  <!-- Populated from logs/iteration-suggestions.txt -->
</div>

<div class="regression-status">
  <h3>Regression Status</h3>
  <p>✓ All dimensions within tolerance</p>
</div>
```

---

## Release Gate Workflow

### Before Each Promotion

```
Suggested SOUL change
    ↓
Implement change (edit YAML)
    ↓
Test on 5 episodes
    ↓
Measure deltas
    ↓
Release gate check:
  ✓ Primary metrics ≥ baseline + 0.3?
  ✓ No regression on secondary metrics?
  ✓ Majority of episodes improved?
    ↓
  If YES → Promote to production
  If NO  → Revert and iterate
```

---

## SOUL Policy Evolution Strategy

### Iteration Priority (by Phase 2 findings)

1. **Evidence Contingency (CRITICAL)**
   - Target: 0.0 → 3.0+
   - Layer: SOUL_LENS_POLICY
   - Change: Increase evidence_drives, guide to ground in specific claims

2. **Character-Contingent Appraisal (HIGH)**
   - Target: 2.0 → 3.0+
   - Layer: SOUL_LENS_POLICY
   - Change: Increase distinct evidence selection per host

3. **Belief Continuity (HIGH)**
   - Target: 2.0 → 3.0+
   - Layer: SOUL_CONVERSATION_POLICY
   - Change: Add explicit belief-update instructions

4. **Others (MEDIUM)**
   - Target: 2.0 → 2.5+
   - Layer: SOUL_VOICE_REALIZATION or CONVERSATION_POLICY
   - Change: Reduce templates, increase naturalism

### Change Tracking

Every SOUL change tracked in git:
```
Commit message format:
  soul_lens_policy.yaml: Increase evidence-grounding drives

  Test: 5 episodes, average delta +0.8 on evidence contingency
  Gate result: PASS (all criteria met)
  Deployed to production 2026-07-17

  Generated-by: Claude AI
  Signed-off-by: Luis Chamberlain <mcgrof@kernel.org>
```

---

## Success Metrics (Phase 4)

### Primary
- Evidence contingency: 0.0 → 2.0+ (first month)
- Character appraisal: 2.0 → 2.5+ (maintain or improve)
- Belief continuity: 2.0 → 2.5+ (maintain or improve)

### Secondary
- No dimension drops > 0.5 from baseline
- 70%+ of iterations pass release gates
- Trend lines show consistent improvement

### Long-term
- Evidence contingency: 3.0+ (sustainable)
- Character appraisal: 3.0+
- Belief continuity: 3.0+
- All dimensions: 3.0+ (authentic episodes)

---

## Fallback Plan (If Phase 3 Fails)

If counterfactual test shows drives don't help:

1. **Pause deployment**
2. **Diagnose failure:**
   - Evidence graphs incomplete?
   - Drive prompts too weak?
   - Rubric miscalibrated?
   - Generation pipeline issue?
3. **Iterate Phase 3:**
   - Fix identified issue
   - Rerun counterfactual test
   - Get new decision
4. **Once fixed, resume Phase 4 deployment**

---

## Operations Playbook

### Daily (9 AM)
```bash
python phase4_active_optimization.py batch-measure --count 10
python phase4_active_optimization.py monitor
```

### Weekly (Friday 5 PM)
```bash
python phase4_active_optimization.py suggest-iteration
# Review suggestions, implement if ready
```

### Weekly (Sunday midnight)
```bash
python phase4_active_optimization.py benchmark-check
# Alert on regression
```

### Per Iteration (as needed)
```bash
# Implement change
# Test: python phase4_active_optimization.py batch-measure --count 5
# Decide: python phase4_active_optimization.py report
# Promote if gates pass
```

---

## Integration Checkpoints

- [ ] Phase 3 validation: PASS gate cleared
- [ ] Measurement integrated into podcast.py
- [ ] Daily monitoring cron installed
- [ ] Weekly suggestions cron installed
- [ ] Benchmark regression check running
- [ ] Admin dashboard deployed
- [ ] First SOUL change tested and promoted
- [ ] 4-week success metrics established

---

## Conclusion

Phase 4 deployment is ready. Upon Phase 3 validation (PASS gate), activate monitoring and begin weekly iteration cycle. System will continuously measure authenticity and guide evolution toward 3.0+ on all 7 dimensions.

**Timeline:** Phase 3 decision → Phase 4 deployment (same day)  
**Success criteria:** Evidence contingency improves from 0.0 to 2.0+ within one month
