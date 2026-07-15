# Phase 3: Counterfactual Test Rig Design

The goal: Measure causally whether SOUL character drives improve authenticity scores.

## The Problem (Phase 2 Finding)

All 25 sampled episodes scored 0 on "Evidence Contingency" — they don't tie reasoning to specific paper evidence.

Hypothesis: This is because generation doesn't incentivize hosts to ground reasoning in evidence.

**Causal question:** If we activate SOUL character drives (e.g., Hal's "what breaks this?" drive, Ada's "prove it" drive, VERA's "what does this mean?" drive), will episodes improve on evidence contingency and character-contingent appraisal?

We can't answer this by looking at existing episodes (they were all generated without drives). We need counterfactual experiments.

## Counterfactual Design

### Setup
**Same paper, same infrastructure, different generation:**
- **Control:** Generate with current pipeline (baseline generation)
- **Treatment:** Generate with SOUL character drives activated in prompts
- **Blinded comparison:** Raters score both without knowing which is which

### Papers
Use frozen benchmark set (24 papers).
- Regenerate each twice (control + treatment)
- Blind: raters see transcript without metadata
- Score: LLM critic on 7 dimensions
- Result: treatment > control on primary metrics = drives work

### Primary Metrics
- **Evidence Contingency** (we know this is broken; drives should fix)
- **Character-Contingent Appraisal** (drives should activate distinct reasoning)
- **Belief Continuity** (drives should make hosts reference prior reasoning)

### Secondary Metrics
- Character-Specific Question Count (do hosts ask different questions?)
- Evidence Reference Density (count mentions of specific claims)
- Disagreement Quality (disagreement from policy, not forced)

## Implementation Phases

### Phase 3a: Evidence Graph Extraction
For each benchmark paper, extract:
- **Central claims** (main contributions)
- **Key evidence** (experiments, proofs, comparisons)
- **Assumptions** (what must be true for claims to hold?)
- **Novel vs prior** (what's new)

Extract via:
1. **Automated:** Parse abstract, structured metadata
2. **LLM-assisted:** Summarize paper structure for LLM access
3. **Manual:** (5 papers) hand-annotate as gold standard

**Output:** Evidence graph YAML per paper
```yaml
paper_id: arxiv/2405.14825
title: "LongRoPE"
central_claims:
  - claim: "RoPE can extend context window beyond 2M tokens"
    evidence: [exp_1, exp_2, exp_3]
    assumptions: [positional_extrapolation_works]

key_evidence:
  - id: exp_1
    type: benchmark
    description: "96K context eval on LongBench"
    result: "99.1% performance at 2M tokens"

assumptions:
  - position_extrapolation works at OOD scales
  - attention patterns generalize
```

### Phase 3b: Drive-Activated Prompt Engineering
Integrate SOUL drives into generation prompts.

**Current:** Generic generation prompt (current llm_reviewer.py)

**Treatment:** Add drive-specific instructions to each host:

```yaml
# For Hal (pragmatist)
drive_hal: |
  Focus on: What would break this in production?
  Ask: What are the resource/scale limits?
  Look for: Hidden assumptions about deployment

# For Ada (mathematician)
drive_ada: |
  Focus on: What must be true for the proof to hold?
  Ask: What is sufficient vs necessary?
  Look for: Missing assumptions or gaps in rigor

# For VERA (narrator)
drive_vera: |
  Focus on: What story does this evidence tell?
  Ask: Who benefits? What's at stake?
  Look for: Frames and implications
```

Then wire into `elevenlabs_client.py` Part 1 prompt:
```python
# Current: uses opening_reason (stopgap)
# Treatment: add SOUL character drive instructions

hal_drive = load_soul_drive("Hal")
prompt = f"""
{existing_prompt}

{hal_drive}
"""
```

### Phase 3c: Paired Generation Harness
Build `counterfactual_generator.py`:

```python
def generate_pair(paper_id, arxiv_url, control=True, treatment=True):
    """Generate control and treatment versions of paper."""
    
    control_script = None
    treatment_script = None
    
    if control:
        control_script = generate_episode(
            paper_id=paper_id,
            urls=[arxiv_url],
            drives={}  # No drives
        )
    
    if treatment:
        treatment_script = generate_episode(
            paper_id=paper_id,
            urls=[arxiv_url],
            drives={
                "hal": load_soul_drive("Hal"),
                "ada": load_soul_drive("Ada"),
                "vera": load_soul_drive("VERA"),
            }
        )
    
    return {
        "paper_id": paper_id,
        "control": control_script,
        "treatment": treatment_script,
        "timestamp": now(),
    }
```

Cost per paper: 2 generations → ~$0.50–1.00 at scale
Cost for 24 papers: $12–24 total

### Phase 3d: Blind Comparison Workflow

1. **Generation:** Regenerate 5 benchmark papers (control + treatment)
   - Cost: ~$2.50–5.00
   - Artifact: 10 transcripts (5 pairs)

2. **Blinding:** Strip metadata, shuffle order
   - Remove episode ID, generation date, drive flags
   - Randomize order (control and treatment mixed)
   - Create rating sheet with just transcript

3. **Scoring:** LLM critic grades both on primary metrics
   - Same LLM critic for both (consistency)
   - Blinded from which is control/treatment
   - Cost: ~$0.30–0.50 per transcript × 10 = $3–5

4. **Analysis:** Statistical test
   - Paired t-test: treatment > control on primary metrics?
   - Minimum detectable effect: 0.5 points (on 0-4 scale)
   - Power: 80% at n=5 pairs

5. **Decision:** Does treatment pass release gates?
   - Evidence Contingency: treatment > control + 0.5?
   - Character Appraisal: treatment > control + 0.5?
   - Belief Continuity: treatment > control + 0.5?
   - If yes → drives work, promote to production
   - If no → drives don't help, diagnostic needed

## Timeline & Cost

| Phase | Task | Time | Cost | Prerequisite |
|-------|------|------|------|--------------|
| 3a | Evidence graph extraction (5 papers) | 1 day | $0 | None |
| 3b | Drive-activated prompts | 2 days | $0 | Phase 3a |
| 3c | Paired generation harness | 1 day | $0 | Phase 3b |
| 3d | Blind comparison (5 pairs) | 1 day | $5–10 | Phase 3c |
| 3e | Analysis + decision | 1 day | $0 | Phase 3d |

**Total:** 1 week, ~$5–10 spend

## Success Criteria

✓ **Phase 3 complete when:**
1. Evidence graph extraction working on ≥5 papers
2. SOUL drive instructions integrated into prompts
3. Paired generation harness functional
4. Counterfactual test run on 5 papers
5. Blind comparison scores collected
6. Statistical test shows treatment > control on ≥2 primary metrics
7. Release gate decision documented (promote or iterate)

## What Can Go Wrong

### Risk: Drives Don't Help
If treatment scores ≈ control, drives may be ineffective or prompt engineering is wrong.

**Mitigation:** Test iteratively on 1 paper first. If treatment > control, scale to 5. If not, adjust prompts and retest.

### Risk: Drive Interference
Adding drives might degrade other dimensions (e.g., naturalism drops).

**Mitigation:** Monitor all 7 dimensions. Release gates require no regression.

### Risk: LLM Critic Is Inconsistent
Same paper scored differently by LLM critic on different runs.

**Mitigation:** Use low temperature (0.3) for consistency. Test reliability on 2 rescored papers.

### Risk: Blinding Fails
Rater can guess which is control/treatment by quality patterns.

**Mitigation:** Use multiple LLM critics (different prompts). Aggregate scores.

## Artifacts & Output

**After Phase 3:**
- `counterfactual_generator.py` — Paired generation harness
- `evidence_graph_*.yaml` — 5 annotated papers
- `counterfactual_results.yaml` — Raw scores + statistical test
- `PHASE3_COUNTERFACTUAL_REPORT.md` — Full analysis + decision
- Decision: Promote drives to production, or iterate

## Next: Phase 4

If drives work:
- Roll out SOUL drives to production generation
- Monitor authenticity scores on new episodes
- Iterate SOUL policies based on feedback

If drives don't work:
- Diagnose why (evidence graphs? prompts? rubric?)
- Test alternative hypotheses
- Iterate Phase 3b or 3c
