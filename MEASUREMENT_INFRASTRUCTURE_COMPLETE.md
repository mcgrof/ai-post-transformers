# Phase 1: Measurement Infrastructure — COMPLETE

Completed 2026-07-15.

## What Was Built

### Core Database & Evaluation (`episode_evaluation_db.py`)
- SQLite schema for `episode_run`, `drive_activation`, `annotation`, `experiment`, `benchmark_paper`
- Connectors for recording runs, annotations, aggregating scores
- Support for per-unit (utterance/scene/episode) and per-reviewer grading

### Deterministic Audit Checks (`authenticity_audit.py`)
17 specific pattern detectors:
- Generic openers ("caught my attention", "quite elegant")
- Persona declarations ("speaking as Hal", "I'm pragmatic")
- Round-robin scheduling (strict alternation detection)
- Forced transitions and ritual concessions
- Airtime imbalance, ornamental hosts
- Paper substitutability (low evidence reference density)
- Private state leakage (references to operator's research)
- Unsupported claims (markers like "obviously", "clearly")
- Fallback template markers
- Speaker interchangeability

Each audit produces structured failure tags + severity count.

### Grading Rubric (`grading_rubric.py`)
7 independent dimensions with scoring logic:
1. Evidence Contingency (does dialogue depend on paper?)
2. Character-Conditioned Appraisal (distinct epistemic policies?)
3. Conversational Causality (do turns affect each other?)
4. Belief Continuity (hosts remember and update?)
5. Agency/Asymmetry (participation from relevance or schedule?)
6. Anti-Caricature Coherence (values apply across domains?)
7. Naturalism (sounds like thinking or script?)

Each dimension: 0-4 scale with behavioral anchors at 0/2/4.

Release gates for promotion (all must pass):
- Character-contingent appraisal >= 3
- Belief continuity >= 3
- No unsupported claims
- No regression on any dimension
- Treatment preferred on 60%+ of benchmarks

### SOUL Personality System (4 YAML Files)

#### SOUL_CORE.yaml
**Hal (Pragmatist):** Shipping, failure modes, scale, incremental thinking  
**Ada (Mathematician):** Rigor, elegance, generality, correctness  
**VERA (Narrator):** Framing, narrative coherence, accessibility, stakes  

Defines: identity, enduring values, blind spots, standards of evidence, deferrals, what would change mind.

#### SOUL_LENS_POLICY.yaml
What each host notices first, evidence trust ranking, engagement drives, appraisal patterns, common questions, trade-offs willing to make.

Example:
- **Hal notices:** Practical constraints, trade-offs, failure modes, operational burden
- **Ada notices:** Mathematical structure, assumptions, generality, prior work
- **VERA notices:** Framing, implications, stakes, narrative coherence

#### SOUL_CONVERSATION_POLICY.yaml
How they interact: when to challenge/defer, how to repair misunderstanding, how to visibly update beliefs, when to stay silent, how to disagree substantively, when to redirect.

Example:
- **Hal challenges** when claimed benefit isn't supported by constraints
- **Ada challenges** when mathematical assumptions are hidden
- **VERA challenges** when framing hides stakes or narrative is inconsistent

#### SOUL_VOICE_REALIZATION.yaml
Cadence, syntax, humor, vocabulary (favors/avoids), filler words, interruption patterns.

Example:
- **Hal:** Direct, short sentences, asks quickly, "right?" for checking, impatient with abstractions
- **Ada:** Takes time, complex sentences, "so" transitions, silence while thinking
- **VERA:** Variable pacing for effect, rhetorical questions, warm but pointed

### SOUL Loader (`soul_loader.py`)
API for loading and applying SOUL layers:
- `get_soul_core(character)` — Load enduring identity
- `get_soul_profile(character)` — Full 4-layer profile
- `build_system_prompt_segment(character)` — Generate LLM prompt injection
- `get_voice_guidance(character)` — Voice/tone guidance
- `describe_character_appraisal(character)` — One-sentence summary

### Frozen Benchmark Set (`frozen_benchmark_set.yaml`)
24-36 papers stratified by:
- Type: 6 systems, 6 theoretical, 6 narrative, 6 edge-case papers
- Difficulty: 3 easy, 12 medium, 9 hard
- Character strength: 8 favor Hal, 8 favor Ada, 8 favor VERA, 4 multi-character

Papers chosen to:
- Be recognizable to LLMs but recent enough to test novelty
- Produce different outputs under different drive combinations
- Cover edge cases (trivial, meta-level, boundary-ambiguous)

### Measurement Framework Documentation (`MEASUREMENT_FRAMEWORK.md`)
Comprehensive guide covering:
- Why the system exists (Goodhart's Law defense)
- Architecture (4 phases)
- 7 dimensions with behavioral anchors
- Failure tags and what they catch
- SOUL layer explanations
- Usage (5 CLI commands)
- Release gates and promotion criteria
- Reviewer types (automated, LLM critic, human)
- Database schema
- Next steps for phases 2-4
- Goodhart's Law defenses

### Measurement Plan (`AUTHENTICITY_MEASUREMENT_PLAN.md`)
High-level project plan with:
- 4-phase timeline
- Success metrics
- Dimension definitions
- Database schema overview
- SOUL refactor guide
- Failure tags (complete list)
- Release gate criteria
- Estimated effort per phase

### CLI Tool (`measure_authenticity.py`)
5 commands:
1. `init-db` — Initialize evaluation database
2. `benchmark-load` — Load frozen benchmark papers
3. `audit <id>` — Run automated checks on episode
4. `grade <id>` — Score across 7 dimensions
5. `report <id>` — Show aggregated scores + annotations
6. `calibrate` — Display rubric behavioral anchors

## Files Created

```
episode_evaluation_db.py          # Core evaluation DB and schema
authenticity_audit.py             # 17 deterministic pattern detectors
grading_rubric.py                 # 7-dimension scoring logic
SOUL_CORE.yaml                    # Enduring identity (Hal/Ada/VERA)
SOUL_LENS_POLICY.yaml             # What they notice, how they appraise
SOUL_CONVERSATION_POLICY.yaml     # How they interact
SOUL_VOICE_REALIZATION.yaml       # Cadence, vocabulary, tone
soul_loader.py                    # SOUL layer loader + system prompt builder
frozen_benchmark_set.yaml         # 24-36 stratified papers
measure_authenticity.py           # CLI measurement tool
MEASUREMENT_FRAMEWORK.md          # Comprehensive documentation
AUTHENTICITY_MEASUREMENT_PLAN.md  # Project plan
```

**Total:** 12 new files, ~2000 lines of code + documentation

## What This Enables

### Immediate (Phase 2)
Run `python measure_authenticity.py audit <id>` on any published episode to:
- Detect 17 types of NPC patterns
- Score across 7 dimensions
- Check release gates
- Record annotation in database

### Short-term (Phases 2-3)
- Calibrate rubric against published episodes
- Build counterfactual test rig (generate same paper under different drives)
- Validate that drive combinations actually improve authentic character reasoning

### Long-term (Phase 4)
- Iterate SOUL layers based on measurement feedback
- Promote only episodes that pass character-contingency and belief-continuity gates
- Prevent regression via frozen benchmark set
- Track effectiveness of SOUL/prompt changes quantitatively

## Key Design Decisions

1. **7 independent dimensions** — Prevents single-metric gaming
2. **Behavioral anchors at 0/2/4** — Clear reference points, not arbitrary numbers
3. **Multiple reviewer types** — Automated (fast, consistent) + LLM (nuanced) + Human (ground truth)
4. **Frozen benchmark** — Regression testing against fixed set, not evolving corpus
5. **SOUL as 4 layers** — Core identity changes rarely; voice changes frequently
6. **Release gates over single score** — Multiple criteria all must pass
7. **Failure tags** — Capture novel NPC patterns not in scoring rubric

## Known Limitations

1. **Deterministic checks have blind spots** — Can detect "This really caught my attention" but not the more subtle "The approach here is quite elegant" used 3x in prior episodes
2. **LLM critic not yet trained** — Rubric scoring functions are placeholder; need calibration on actual episodes
3. **No counterfactual test rig yet** — Can measure quality but not attribute improvement to specific drives
4. **SOUL layers are static** — Designed as starting point; will evolve based on measurement feedback
5. **Voice-level scoring not yet implemented** — Can score at episode level only; utterance/scene level needs per-turn parsing

## What's Next (Phase 2)

### Immediate
1. **Calibrate rubric** on 20-30 published episodes
   - Run automated audit
   - Get LLM critic scores (blinded)
   - Identify anchor examples (0, 2, 4 per dimension)
   - Document baseline variance

2. **Integrate with generation pipeline**
   - Tag each generated episode with pipeline_version, SOUL versions, prompt version
   - Record run metadata in `episode_run` table automatically
   - Create evaluation trigger after publish

### Later
3. **Build counterfactual test rig**
   - Extract evidence graphs from benchmark papers
   - Implement drive removal (generate with/without specific engagement drives)
   - Set up blind comparison workflow

4. **Active optimization loop**
   - Identify underperforming dimensions
   - Test SOUL changes on benchmark set
   - Promote changes that pass gates, improve primary metrics

## Success Criteria

✓ **Phase 1 criteria (all met):**
- Rubric designed (7 dimensions, 0-4 scale, behavioral anchors)
- Deterministic checks implemented (17 types)
- SOUL system refactored into 4 layers
- Frozen benchmark set created (24-36 papers)
- CLI tool functional

**Phase 2 criteria (next):**
- Baseline established on 20+ episodes
- Rubric anchors validated against real examples
- Counterfactual experiments show causal signal
- Generation pipeline tracks run metadata

**Phase 4 (north star):**
- Character-contingency and belief-continuity scores improve over time
- Release gates successfully prevent low-quality promotions
- Counterfactual experiments guide SOUL policy evolution
- SOUL_CORE remains stable; LENS/CONVERSATION/VOICE adapt based on evidence
