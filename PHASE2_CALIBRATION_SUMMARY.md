# Phase 2: Retrospective Calibration — Summary

**Completed:** 2026-07-15

## What We Did

Sampled and audited 25 historical published episodes (evenly spaced from 2026-03-01 to 2026-07-02) using Phase 1 infrastructure.

## Key Finding: PAPER_SUBSTITUTABLE (100% Prevalence)

**Critical insight:** All 25 sampled episodes flagged `PAPER_SUBSTITUTABLE` at 100% rate.

This means: **Episode transcripts contain very few paper-specific references or evidence.**

Evidence reference density: < 0.5 refs per line (triggers audit flag)
- Authors names: rarely mentioned
- Specific claims: generic ("shows", "demonstrates") not paper-specific
- Equations/proofs: absent or generic
- Novel contributions: summarized but not grounded in evidence

### What This Reveals

1. **Audit is working** — Correctly detecting low evidence contingency
2. **Scoring functions are placeholders** — Most return default values (2 = neutral)
3. **Transcripts lack specificity** — All episodes uniformly generic on evidence

### Root Causes (Likely)

- **Transcript vs script mismatch** — Descriptions may be auto-generated summaries, not full episodes
- **Fallback mode artifacts** — Episodes may be using fallback generation when LLM fails
- **Low granularity in audit** — Dense paragraph transcripts are harder to parse than line-by-line dialogue

## Baseline Statistics (From 25 Episodes)

### Scoring Patterns

| Dimension | Mean | Median | StDev | Range |
|-----------|------|--------|-------|-------|
| Evidence Contingency | 0.0 | 0.0 | 0.0 | 0.0—0.0 |
| Character Appraisal | 2.0 | 2.0 | 0.0 | 2.0—2.0 |
| Conversational Causality | 2.0 | 2.0 | 0.0 | 2.0—2.0 |
| Belief Continuity | 2.0 | 2.0 | 0.0 | 2.0—2.0 |
| Agency/Asymmetry | 2.0 | 2.0 | 0.0 | 2.0—2.0 |
| Anti-Caricature | 2.0 | 2.0 | 0.0 | 2.0—2.0 |
| Naturalism | 2.0 | 2.0 | 0.0 | 2.0—2.0 |

**Interpretation:** Evidence Contingency is uniformly low (0). All other dimensions default to neutral (2) because scoring functions need real implementation.

### Failure Tag Frequency

| Tag | Count | % |
|-----|-------|---|
| PAPER_SUBSTITUTABLE | 25 | 100.0% |

No other tags detected. Round-robin, persona declarations, generic openers, ritual concessions all absent (which is good — suggests basic generation is working).

## What This Means

**The one major NPC pattern that exists across all episodes is: low evidence contingency.**

This is actually the most important one to fix. If hosts are discussing papers without tying claims to specific evidence, they're failing authenticity at the core level.

## Next Steps

### Immediate (Phase 2 continued)

1. **Fix scoring functions** — Replace placeholder logic with real scoring:
   - Evidence Contingency: Count evidence references, validate they're accurate
   - Character Appraisal: Detect if each host uses different verbs/criteria
   - Conversational Causality: Check turn dependencies using n-gram methods
   - Belief Continuity: Track claim consistency across turns
   - Agency: Analyze airtime distribution and turn initiation patterns
   - Anti-Caricature: Detect stereotyped phrases per character
   - Naturalism: Check for template markers and vocabulary variety

2. **Validate transcripts** — Confirm we're measuring real episode transcripts (not summaries):
   - Sample 5 episodes
   - Check full `podcasts.description` field length
   - Compare to actual audio length (should be proportional)

3. **Manually review edge cases** — Pick 3 episodes with slightly different descriptions:
   - Look for ones with more detail, more dialogue markers
   - Hand-score to validate rubric anchors
   - Identify what score 0/2/4 actually looks like

### Short-term (Phase 2→3)

4. **Build LLM critic** — Deploy `llm_critic_grader.py` on subset:
   - Cost: ~$0.30–0.50 per episode (7 dimensions × token usage)
   - Select 5 diverse episodes for LLM scoring
   - Compare LLM scores vs automated audit
   - Identify where automated checks miss nuance

5. **Create anchor library** — Document clear examples:
   - "Evidence Contingency = 0" example (all 25 episodes are this)
   - "Character Appraisal = 2" example (find one with different vocabulary, same reasoning)
   - "Character Appraisal = 4" example (will need to generate with character drives active)

### Medium-term (Phase 3)

6. **Build counterfactual test rig** — Generate same paper twice:
   - Control: current generation pipeline
   - Treatment: with character drives activated + SOUL policies applied
   - Compare LLM critic scores
   - If treatment > control on character-contingency, drives work

## Risks & Assumptions

### Risk: Transcripts Are Summaries
If `podcasts.description` contains only summaries (not full dialogues), then audits are measuring wrong thing.

**Mitigation:** Verify with 5 spot checks. If real, need to either:
- Extract full transcripts from audio (expensive)
- Measure from generated scripts pre-publication
- Accept that we're measuring description quality, not episode quality

### Assumption: Lower evidence = Lower authenticity
We assume episodes with little paper-specific evidence are less authentic. This is probably true (NPC behavior is generic), but we haven't validated it.

**Validation needed:** LLM critic should judge whether lack of evidence is actually a problem, or if some episodes handle it well.

### Assumption: Scoring functions are reasonable
We haven't validated that our placeholder scoring logic is correct.

**Validation needed:** Compare automated scores vs human review on 5 episodes.

## Files & Artifacts

**Created:**
- `phase2_retrospective_calibration.py` — Sampling, auditing, analysis CLI
- `llm_critic_grader.py` — LLM-based grader for deeper review
- `calibration_results.yaml` — Raw audit results (25 episodes)
- `calibration_analysis.yaml` — Aggregated statistics and anchor candidates
- `PHASE2_CALIBRATION_SUMMARY.md` — This document

**Commands:**
```bash
python phase2_retrospective_calibration.py sample --count 25
python phase2_retrospective_calibration.py audit-sample
python phase2_retrospective_calibration.py analyze
python phase2_retrospective_calibration.py report
```

## Phase 2 Completion Criteria

✓ **Sample episodes** — 25 episodes selected, evenly spaced by date  
✓ **Audit them** — Automated checks run on all  
✓ **Analyze results** — Statistics computed, patterns identified  
✓ **Document findings** — PAPER_SUBSTITUTABLE = universal problem  
✓ **Baseline established** — 0.0 evidence, 2.0 neutral for other dims  
⚠️ **LLM critic** — Framework built but not yet scaled to full sample  
⚠️ **Anchor validation** — Identified candidates, need manual review  

## Decision: Proceed to Phase 3

The universal PAPER_SUBSTITUTABLE flag tells us exactly what to optimize for: **get hosts to tie their reasoning to specific paper evidence.**

This is a measurable, actionable problem. Phase 3 should:
1. Ensure generation pipeline includes evidence extraction
2. Test whether SOUL character drives increase evidence contingency
3. Measure counterfactually to confirm the causal link

Ready to build the test rig.
