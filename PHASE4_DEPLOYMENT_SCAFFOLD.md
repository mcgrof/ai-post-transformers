# Phase 4 Deployment Scaffold

**Status:** Ready to deploy immediately upon Phase 3 PASS decision

## One-Line Decision Rule

If Phase 3 report shows: `Decision: PASS`

Then immediately execute:
1. Run deployment integration script
2. Deploy monitoring cron
3. Start production optimization loop

## Deployment Steps (Execute if PASS)

### Step 1: Integrate Measurement into Generation

File: `podcast.py` (after episode is saved to database)

```python
# Around line where podcast_id is assigned and podcast is saved
if podcast_id:
    try:
        from phase4_active_optimization import measure_episode
        measurement = measure_episode(podcast_id)
        print(f"[Optimization] Episode {podcast_id} measured: "
              f"avg={measurement.get('avg_score', 'N/A'):.1f}")
    except ImportError:
        pass  # Skip if phase4 module not available
    except Exception as e:
        print(f"[Optimization] Measurement failed (non-blocking): {e}")
```

### Step 2: Deploy Daily Monitoring Cron

Add to user crontab (`crontab -e`):

```cron
# Daily authenticity monitoring (9 AM)
0 9 * * * cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py batch-measure --count 10 \
  >> logs/daily-measure.txt 2>&1

0 9 * * * cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py monitor \
  >> logs/daily-dashboard.txt 2>&1
```

### Step 3: Deploy Weekly Suggestions Cron

```cron
# Weekly iteration suggestions (Friday 5 PM)
0 17 * * 5 cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py suggest-iteration \
  >> logs/weekly-suggestions.txt 2>&1
```

### Step 4: Deploy Regression Detection

```cron
# Weekly regression check (Sunday midnight)
0 0 * * 0 cd /home/mcgrof/devel/ai-post-transformers && \
  python phase4_active_optimization.py benchmark-check \
  >> logs/regression-check.txt 2>&1 && \
  [ $? -ne 0 ] && echo "REGRESSION DETECTED" | \
  mail -s "Authenticity Regression Alert" mcgrof@gmail.com
```

### Step 5: Verify Deployment

After running crons, verify:

```bash
# Check that crontab entries exist
crontab -l | grep "phase4_active_optimization"

# Check that logs are being generated
ls -la logs/daily-*.txt logs/weekly-*.txt logs/regression-*.txt

# Test one manual run
python phase4_active_optimization.py batch-measure --count 1
python phase4_active_optimization.py monitor
```

## Manual Iteration Workflow (Weekly)

1. **Review suggestions:**
   ```bash
   tail -20 logs/weekly-suggestions.txt
   ```

2. **Pick one suggestion** (e.g., "Increase evidence-grounding drives")

3. **Implement SOUL change** (edit `SOUL_LENS_POLICY.yaml`):
   ```yaml
   Hal:
     lens_policy:
       engagement_drives:
         - "What's the evidence for this?"  # NEW
         - "Where does this break?"          # INCREASED
   ```

4. **Test on 5 episodes:**
   ```bash
   python phase4_active_optimization.py batch-measure --count 5
   ```

5. **Check improvement:**
   ```bash
   python phase4_active_optimization.py report
   ```

6. **Release gate decision:**
   - If gates pass → commit and deploy
   - If gates fail → revert and iterate

## Commit Format for SOUL Changes

```
soul_lens_policy.yaml: Increase evidence-grounding drives

Test: 5 episodes, average delta +0.8 on evidence contingency
Gate result: PASS (all criteria met)
Deployed to production 2026-07-16

Generated-by: Claude AI
Signed-off-by: Luis Chamberlain <mcgrof@kernel.org>
```

## Success Indicators (First Month)

- Evidence contingency: 0.0 → 1.5+ (measurable improvement)
- Character appraisal: 2.0 → 2.2+ (maintained or improved)
- Belief continuity: 2.0 → 2.2+ (maintained or improved)
- 70%+ of suggestions pass release gates
- Trend lines showing consistent improvement

## Fallback (If FAIL)

If Phase 3 report shows: `Decision: FAIL` or `Decision: MARGINAL`

Then:
1. Diagnose the failure (check Phase 3 report for which dimension failed)
2. Iterate Phase 3 (modify prompts, rubric, or drives)
3. Re-run Phase 3 test
4. Get new decision
5. Resume Phase 4 deployment once PASS

## Rollback (If Regression Detected)

If regression check alerts on production:

```bash
# 1. Identify the SOUL change that caused regression
git log --oneline | head -5

# 2. Revert the change
git revert <commit-hash>

# 3. Test revert
python phase4_active_optimization.py batch-measure --count 10
python phase4_active_optimization.py report

# 4. Commit revert
git commit -am "Revert SOUL change (regression detected)"
```

---

## Decision Timestamp

Phase 3 result will be in: `/home/mcgrof/devel/ai-post-transformers/counterfactual_results.yaml`

Look for line: `decision: PASS | FAIL | MARGINAL`

Once visible → execute deployment steps immediately if PASS.
