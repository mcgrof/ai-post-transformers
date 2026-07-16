# Phase 3: Counterfactual Execution Guide

**Status:** Ready to run. Infrastructure complete.

## Quick Start

### 1. Generate Control/Treatment Pairs

```bash
# Generate 3 test pairs (quick validation)
python counterfactual_generator.py generate-batch --count 3
```

This will:
- Load 3 papers from frozen benchmark set
- Generate control version (baseline, no drives)
- Generate treatment version (with SOUL drives enabled)
- Save to `counterfactual_results.yaml`

Expected output:
```
Generating 3 control/treatment pairs from frozen benchmark set...

  [1] 2410.08686: Attention is Not All You Need...
  [Treatment] Generating from https://arxiv.org/pdf/2410.08686.pdf...
  [Control] Generating from https://arxiv.org/pdf/2410.08686.pdf...
  ✓ Pair generated
  
  [2] 2405.14825: LongRoPE...
  ...
  
✓ Generated 3 pairs
✓ Saved to counterfactual_results.yaml

Next: python counterfactual_generator.py compare-batch
```

**Note:** Generating 3 pairs will take ~5–10 minutes (depends on LLM latency). Each generation makes 1 LLM call. Cost: ~$0.20–0.40 per pair.

### 2. Grade Both Versions (Blinded)

```bash
# Score both versions via LLM critic
python counterfactual_generator.py compare-batch
```

This will:
- Read generated pairs from `counterfactual_results.yaml`
- Grade control version (blinded)
- Grade treatment version (blinded)
- Compute delta per dimension
- Save comparison results

Expected output:
```
Grading 3 pairs (blinded)...

  Grading 2410.08686 (blinded)...
    Control...  ✓ 1.9
    Treatment... ✓ 2.6

  Grading 2405.14825 (blinded)...
    Control...  ✓ 2.0
    Treatment... ✓ 2.8

  Grading 2208.01313 (blinded)...
    Control...  ✓ 1.8
    Treatment... ✓ 2.5

✓ Saved comparison results to counterfactual_results.yaml

Next: python counterfactual_generator.py report
```

**Cost:** ~$1–2 for LLM critic grading (3 pairs × 2 versions × 0.30-0.40 per episode).

### 3. View Results

```bash
# Display final report with decision
python counterfactual_generator.py report
```

Expected output:
```
======================================================================
PHASE 3: COUNTERFACTUAL TEST RESULTS
======================================================================

Generated: 3 control/treatment pairs
Generation date: 2026-07-16T...

Comparison Results:

  2410.08686          1.9 → 2.6 ↑ +0.7 [PASS]
  2405.14825          2.0 → 2.8 ↑ +0.8 [PASS]
  2208.01313          1.8 → 2.5 ↑ +0.7 [PASS]

Summary Statistics:
  Average aggregate delta:      +0.7
  Average evidence delta:       +0.5
  Average character delta:      +0.4
  Pairs with improvement:       3/3

Decision:
✓ PASS: Drives significantly improve authenticity
  → Promote SOUL drives to production

======================================================================
```

## What Each Command Does

### generate-batch [--count N]

Regenerates N benchmark papers twice:
1. **Control:** Standard generation (baseline)
2. **Treatment:** Generation with `soul_drives_enabled=True`

**How drives are injected:**
```python
# In generate_episode_with_drives():
if drives_enabled:
    config["soul_drives"] = {
        "Hal": "Focus on: What would break this? What evidence? (+ more)",
        "Ada": "Focus on: What must be true? Prove it? (+ more)",
        "VERA": "Focus on: What's the frame? Stakes? (+ more)",
    }
```

These instructions are passed to the LLM during script generation, steering hosts toward character-specific reasoning.

**Output:** `counterfactual_results.yaml` containing:
```yaml
pairs:
  - paper_id: arxiv_id
    control:
      mode: control_baseline
      transcript: "Hal: ...\nAda: ..."
      status: GENERATED
    treatment:
      mode: treatment_with_drives
      transcript: "Hal: ...\nAda: ..."
      status: GENERATED
```

### compare-batch

Grades control and treatment versions using LLM critic (blinded):
- Critic doesn't see which is which
- Scores all 7 dimensions (0-4 scale)
- Computes deltas

**Primary metrics:**
- Evidence Contingency delta (target: > +0.3)
- Character Appraisal delta (target: > +0.3)
- Aggregate delta (target: > +0.5)

**Output:** Appends comparison_results to YAML:
```yaml
comparison_results:
  - paper_id: arxiv_id
    control_avg: 1.9
    treatment_avg: 2.6
    delta: +0.7
    evidence_delta: +0.5
    character_delta: +0.4
    decision: PASS
```

### report

Analyzes comparison results and makes go/no-go decision:

**PASS criteria (all must be true):**
- Average aggregate delta > 0.5
- Average evidence delta > 0.3
- Average character delta > 0.3
- Majority of pairs show improvement

**If PASS:**
```
✓ PASS: Drives significantly improve authenticity
  → Promote SOUL drives to production
```

**If MARGINAL (0 < delta ≤ 0.5):**
```
⚠ MARGINAL: Drives help slightly
  → Iterate prompts and retest
```

**If FAIL (delta ≤ 0):**
```
✗ FAIL: Drives don't improve authenticity
  → Diagnose: evidence graphs? prompts? calibration?
```

## Architecture: How It Works

### Control Generation

```
Paper URL
    ↓
Extract text
    ↓
generate_podcast_script(text, config)
    ├─ Pass 0: Topic classification
    ├─ Pass 1: Background research
    ├─ Pass 2: Concept analysis
    ├─ Pass 3: Script generation (NO drives)
    └─ Output: Transcript
```

### Treatment Generation

```
Paper URL
    ↓
Extract text
    ↓
generate_podcast_script(text, config + soul_drives)
    ├─ Pass 0: Topic classification
    ├─ Pass 1: Background research
    ├─ Pass 2: Concept analysis
    ├─ Pass 3: Script generation (WITH drive instructions)
    └─ Output: Transcript
```

**Drive instructions injected at Pass 3 (script generation):**
```python
if config["drives_enabled"]:
    for character in ["Hal", "Ada", "VERA"]:
        prompt += config["soul_drives"][character]
```

This tells the LLM to think like each character, grounding reasoning in specific evidence per their epistemic policies.

### Blinded Grading

```
Control transcript + Treatment transcript
    ↓
Shuffle order (rater doesn't see which is which)
    ↓
grade_episode_full() for each
    ├─ Evidence Contingency: Does dialogue depend on paper?
    ├─ Character Appraisal: Distinct reasoning per host?
    ├─ Conversational Causality: Turns affect each other?
    ├─ Belief Continuity: Hosts remember and update?
    ├─ Agency/Asymmetry: Participation from relevance?
    ├─ Anti-Caricature: Values across domains?
    └─ Naturalism: Sounds like conversation?
    ↓
Compute delta = treatment - control
```

## Example Execution Timeline

```
13:00 — Start
  $ python counterfactual_generator.py generate-batch --count 3
  Generating 3 pairs...
  [5-10 minutes, depending on LLM latency]

13:10 — Pairs generated
  ✓ 3 control/treatment pairs ready
  
13:10 — Grade
  $ python counterfactual_generator.py compare-batch
  Grading 6 transcripts via LLM critic...
  [3-5 minutes]
  
13:15 — Results ready
  $ python counterfactual_generator.py report
  
  ✓ PASS: Drives improve authenticity
  → Next: Promote to Phase 4

13:15 — Done. Phase 3 complete.
Total time: ~15 minutes
Total cost: ~$2–3
```

## Expected Results (Hypothesis)

Based on Phase 2 findings (all episodes scored 0 on evidence contingency):

**If drives work:**
- Control: Evidence = 0.0–0.5, Character = 2.0
- Treatment: Evidence = 1.5–2.5, Character = 2.5–3.0
- Delta: +0.5–2.0 on evidence, +0.3–0.5 on character
- Decision: PASS → Promote drives

**If drives don't work:**
- Control: Evidence = 0.0–0.5
- Treatment: Evidence = 0.0–0.5 (no change)
- Delta: ~0.0
- Decision: FAIL → Diagnose problem

## Troubleshooting

### "No papers found in benchmark set"
- Run: `python measure_authenticity.py benchmark-load` first

### "ModuleNotFoundError: phase3_counterfactual_harness"
- Make sure `phase3_counterfactual_harness.py` is in same directory
- Or: `export PYTHONPATH=$(pwd):$PYTHONPATH`

### "ERROR: Could not extract text from URL"
- PDF might be corrupted or unreachable
- Try a different paper or download manually

### "ERROR: LLM call failed"
- Check API credentials (ANTHROPIC_API_KEY, etc.)
- Check rate limits
- Try with fewer pairs initially

### Results look unchanged (delta ≈ 0)
- Drives may not be strong enough
- Try increasing drive instructions in `build_soul_drive_segment()`
- Or: evidence graphs might be incomplete (need per-paper analysis)

## Next After Phase 3

### If PASS (drives work):
1. Promote to Phase 4
2. Wire into gen-podcast.py (auto-measure every episode)
3. Deploy monitoring dashboard
4. Start weekly iteration loop

### If FAIL (drives don't work):
1. Diagnose root cause
2. Iterate Phase 3b (improve drive prompts) or 3c (better grading)
3. Rerun counterfactual test
4. Once validated, promote to Phase 4

## Files

**Core:**
- `phase3_counterfactual_harness.py` — Generation + grading integration
- `counterfactual_generator.py` — CLI for experiments
- `PHASE3_EXECUTION_GUIDE.md` — This file

**Output:**
- `counterfactual_results.yaml` — Generated pairs + scores

**Dependencies:**
- `frozen_benchmark_set.yaml` — Papers to test on
- `elevenlabs_client.py` — Real generation pipeline
- `llm_critic_grader.py` — LLM-based scorer
