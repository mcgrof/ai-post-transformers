# Authenticity Measurement Framework

This system measures and evolves the authenticity of podcast episodes across 7 dimensions of "non-NPC behavior."

## Why This Exists

The podcast was showing NPC patterns: stale phrases (Hal repeatedly saying "This really caught my attention"), scheduled round-robin turns, generic openings, and rigid character stereotypes.

**Single 0-100 score trap:** If we graded authenticity as one number, the system would learn to optimize for cosmetic markers (interruptions, ritual concessions) without actually becoming more authentic.

**Solution:** Measure 7 independent dimensions, grade from multiple perspectives (automated checks + LLM critics + human reviewers), and use counterfactual experiments to validate that drive combinations actually improve quality.

## Architecture

### Phase 1: Measurement Infrastructure ✓

**Files created:**
- `episode_evaluation_db.py` — SQLite schema for runs, annotations, experiments
- `authenticity_audit.py` — Deterministic audit checks (templates, persona declarations, round-robin, etc)
- `grading_rubric.py` — Scoring logic for 7 dimensions (0-4 scale)
- `SOUL_CORE.yaml` — Enduring values, blind spots, standards of evidence
- `SOUL_LENS_POLICY.yaml` — What each host notices and how they appraise evidence
- `SOUL_CONVERSATION_POLICY.yaml` — How hosts interact, when they defer, update, disagree
- `SOUL_VOICE_REALIZATION.yaml` — Cadence, vocabulary, tone (changes frequently)
- `soul_loader.py` — Load and apply SOUL personality layers
- `frozen_benchmark_set.yaml` — 24-36 stratified papers for calibration
- `measure_authenticity.py` — CLI for auditing, grading, reporting
- `AUTHENTICITY_MEASUREMENT_PLAN.md` — Full project plan

### Phase 2: Retrospective Calibration (Next)

Sample 20-30 published episodes, grade them to calibrate rubric anchors and establish baseline variance.

### Phase 3: Counterfactual Test Rig (Next)

Build infrastructure to regenerate episodes with/without specific drives, compare blindly.

### Phase 4: Active Optimization Loop (Next)

Iterate SOUL policies, run paired experiments, track effectiveness.

## The 7 Dimensions (0-4 scale)

Each dimension has clear behavioral anchors at 0 (worst), 2 (mixed), 4 (best).

### 1. Evidence Contingency
**Question:** Would the same dialogue work for a different paper?

- **0:** Generic statements; paper substitution barely matters
- **2:** Paper-specific summary; hosts don't interpret evidence
- **4:** Claims tied to specific evidence, assumptions, omissions; different paper → different discussion

**Red flags:** "This paper is fascinating" (works for any paper) vs "The rank-doubling result contradicts attention-is-all-you-need in a specific way" (doesn't work for other papers)

### 2. Character-Conditioned Appraisal
**Question:** Do hosts appraise evidence based on distinct cognitive policies?

- **0:** Hosts interchangeable or only different by catchphrases ("As a pragmatist...")
- **2:** Different vocabulary and emphasis; same reasoning
- **4:** Different evidence selection, values, and doubt criteria; disagreement stems from policy, not personality

**Red flags:** "Persona declaration" (Hal explicitly saying he's pragmatic) vs "Hal asks about operational burden" (shows pragmatism through reasoning)

### 3. Conversational Causality
**Question:** Does each turn depend on previous turns?

- **0:** Turns can be shuffled without damage; no responses
- **2:** Some replies; many monologues could reorder
- **4:** Each turn changes question, position, or direction; causality runs forward

**Red flags:** Round-robin turn order ("Hal speaks, Ada speaks, VERA speaks") vs natural responses to what was just said

### 4. Belief Continuity
**Question:** Do hosts remember and update on prior exchanges?

- **0:** Positions reset; objections repeat after being answered
- **2:** Consistent but static; no explicit updates
- **4:** Concessions, distinctions, confidence changes, unresolved objections persist

**Red flags:** Ritual concession ("That's a fair point") followed by forgetting it next turn vs "I wasn't considering this constraint until you mentioned..."

### 5. Agency/Asymmetry
**Question:** Does participation emerge from relevance or scheduling?

- **0:** Equal airtime, mandatory contributions, scheduled disagreement
- **2:** Some organic ownership; some filler
- **4:** Silence, deference, topic ownership emerge from relevance

**Red flags:** "Hal, Ada, VERA, your thoughts?" (round-robin cue) vs Hal volunteering when engineering matters

### 6. Anti-Caricature Coherence
**Question:** Do hosts preserve values across domains?

- **0:** Hal deployed to deploy, Ada to prove, VERA to narrate (by command)
- **2:** Recognizable but stereotyped
- **4:** Cross domains naturally; values apply to different content

**Red flags:** "Ada, do the math here" (summoning her stereotype) vs Ada engaging with engineering because it reveals theoretical tensions

### 7. Naturalism
**Question:** Does it sound like thinking aloud?

- **0:** Obviously templated; unnatural phrasing
- **2:** Mostly listenable; artifacts visible
- **4:** Varied, economical, socially plausible

**Red flags:** "Generated by LLM" markers, repetitive structures, filler

## Failure Tags (Automated Detection)

`authenticity_audit.py` identifies these at parse time:

| Tag | Meaning |
|-----|---------|
| GENERIC_OPENER | Phrase works for any paper |
| PERSONA_DECLARATION | Host explicitly claiming their identity |
| ROUND_ROBIN | Strict alternating host order |
| FORCED_DISAGREEMENT | Manufactured disagreement without cause |
| BELIEF_RESET | Position contradicts prior statement |
| QUESTION_NOT_ANSWERED | Host asked question, later ignored |
| PAPER_SUBSTITUTABLE | Dialogue has <0.5 paper refs per line |
| SPEAKER_INTERCHANGEABLE | Any host could say it |
| PRIVATE_STATE_LEAKAGE | Reference to operator's private research |
| FALLBACK_TEMPLATE | Explicit fallback mode marker |
| UNSUPPORTED_CLAIM | Claim marked with "obviously" / "clearly" |
| ORNAMENTAL_HOST | Host <10% airtime but present |
| PREMATURE_CONSENSUS | Disagreement resolved too easily |
| REPETITIVE_ROLE | Same pattern across episodes |
| FILLER_EXPOSITION | Filler disguised as dialogue |
| RITUAL_CONCESSION | Agreement with no state change |
| CARICATURE_ACTIVATION | Host deployed on stereotype |

## SOUL Personality System (4 Layers)

### Layer 1: SOUL_CORE
Enduring identity (changes rarely, human approval required)

**Hal:** Systems engineer, pragmatist
- Values: engineering pragmatism, shipping, failure modes, scale
- Blind spots: dismissing rigor, conflating pragmatic with good
- Defers to Ada when: mathematical correctness matters; to VERA when: framing matters

**Ada:** Mathematician, theorist
- Values: mathematical elegance, formal rigor, generality, simplicity
- Blind spots: privileging elegance over utility, missing implementation difficulty
- Defers to Hal when: practice contradicts theory; to VERA when: framing affects correctness

**VERA:** Narrative thinker, framing expert
- Values: narrative coherence, accessibility, framing clarity, human stakes
- Blind spots: narrative elegance over rigor, emotional over rational, over-generalization
- Defers to Hal when: narrative contradicts reality; to Ada when: story is logically broken

### Layer 2: SOUL_LENS_POLICY
What they notice and how they appraise (changes after repeated evidence)

- What they notice first
- Evidence trust ranking
- Engagement drives
- Appraisal patterns
- Common questions
- Trade-offs willing to make

Example: **Hal notices practical constraints first; trusts end-to-end system evaluation most; asks "what breaks this?" rather than "is this elegant?"**

### Layer 3: SOUL_CONVERSATION_POLICY
How they interact (changes after paired tests)

- When to challenge
- When to defer
- How to repair misunderstanding
- How to update beliefs (visibly!)
- When to stay silent
- How to disagree substantively
- When to redirect

### Layer 4: SOUL_VOICE_REALIZATION
Cadence, vocabulary, tone (changes frequently)

- Cadence and pacing
- Syntax and sentence structure
- Humor and tone
- Vocabulary preferences (favors/avoids)
- Filler words
- Interruption patterns

## Usage

### 1. Initialize Database

```bash
python measure_authenticity.py init-db
python measure_authenticity.py benchmark-load
```

### 2. Audit an Episode

```bash
python measure_authenticity.py audit 42
```

Shows:
- Generic openers detected
- Persona declarations
- Failure tags
- Severity count

### 3. Grade an Episode

```bash
python measure_authenticity.py grade 42 --reviewer-type automated
```

Scores across 7 dimensions, checks release gates.

### 4. Generate Report

```bash
python measure_authenticity.py report 42
```

Shows median scores across all reviewers, aggregated failure tags.

### 5. View Calibration Guide

```bash
python measure_authenticity.py calibrate
```

Shows behavioral anchors for each dimension (what 0/2/4 look like).

## Release Gates (Promotion Criteria)

An episode must pass ALL gates to be promoted from a control/experiment:

1. **Character-Contingent Appraisal >= 3** (median across reviewers)
2. **Belief Continuity >= 3** (median)
3. **No unsupported central claims** (automated check)
4. **No sentinel regression** (vs baseline)
5. **Treatment preferred on >= 60% of matched papers** (counterfactual)
6. **Confidence interval not consistent with large loss**
7. **No host loses materially** (10th-percentile scene score unchanged or improved)
8. **No meaningful regression on any dimension**

## Reviewer Types

### Automated Checks
Fast, deterministic. Detects:
- Template reuse and patterns
- Persona declarations
- Airtime imbalance
- Round-robin scheduling
- Paper substitutability
- Private state leakage
- Fallback mode markers

**Confidence:** 0.7 (high precision, moderate recall)

### LLM Critic (Blind Mode)
Reads transcript without pipeline version, intended drives, or other metadata.

Graded on:
- Coherence of character reasoning
- Evidence-contingency of claims
- Believability of disagreement

**Confidence:** 0.85 (calibrated after initial rubric testing)

### Human Reviewer
Informed and naive listener variants.

**Confidence:** 0.9 (gold standard, expensive)

## Database Schema

### episode_run
Metadata about generation: papers, model, SOUL versions, pipeline, artifact stage

### annotation
Individual reviews: per unit (utterance/scene/episode), per reviewer, per dimension

### drive_activation
Which engagement drives were activated, where, how strongly

### experiment
Counterfactual test metadata: hypothesis, control/treatment, gates, results

### benchmark_paper
Frozen set for calibration and regression testing

## Next Steps

### Phase 2: Retrospective Calibration
1. Select 20-30 published episodes stratified by date/type
2. Run automated audit on all
3. LLM critics grade each (blinded)
4. Identify rubric anchor examples:
   - Best evidence_contingency episode (4)
   - Worst (0)
   - Mixed (2)
5. Calibrate scoring functions against these anchors
6. Document baseline variance by dimension

### Phase 3: Counterfactual Test Rig
1. Extract evidence graph from papers (structured entities, claims, evidence)
2. Build drive-removal infrastructure:
   - Generate with drive=None (only core prompting)
   - Generate with drive=A (active drive A)
   - Generate with drive=B (active drive B)
   - Generate with drives=[A,B] (both)
3. Blind comparison: raters see transcripts without knowing which drive combo
4. Statistical test: does drive combo improve on primary metrics?

### Phase 4: Active Optimization
1. Identify lowest-performing dimensions in production
2. Propose SOUL_LENS_POLICY or SOUL_VOICE_REALIZATION changes
3. Test on benchmark set (both control and treatment)
4. If gates pass, promote change
5. Roll out to production with monitoring

## Goodhart's Law Defenses

> "Any metric that becomes a target ceases to be a good metric."

**Our defenses:**

1. **Multi-dimensional:** Can't optimize for all 7 simultaneously
2. **Conflicting goals:** Character-contingency vs naturalism tension exists
3. **Release gates:** Blocking promotion if ANY dimension regresses
4. **Counterfactual validation:** Only optimize what actually helps
5. **Frozen benchmarks:** Regression against fixed test set
6. **Multiple reviewers:** Single reviewer can't be gamed
7. **Qualitative tags:** Failure tags catch novel failure modes
8. **External measurement:** Human listeners as ground truth

## Related Files

- `CLAUDE.md` — Project guidelines and context
- `gen-podcast.py` — Main episode generation entry point
- `elevenlabs_client.py` — LLM script generation
- `podcast.py` — Episode orchestration
- `soul_reasons.py` — Opening reason rotation (Phase 1 stopgap, to be replaced)

## References

- Goodhart's Law: https://en.wikipedia.org/wiki/Goodhart%27s_law
- ChatGPT Pro design review: `~/` (internal)
- Plan: `AUTHENTICITY_MEASUREMENT_PLAN.md`
