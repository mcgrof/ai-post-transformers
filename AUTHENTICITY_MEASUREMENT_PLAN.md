# Authenticity Measurement & Evolution System

## Goals
1. Measure non-NPC behavior across 7 dimensions (not a single 0-100 score)
2. Track historical episodes to identify patterns
3. Use counterfactual experiments to validate drive combinations
4. Evolve SOUL.md and generation pipeline toward authentic character cognition
5. Prevent measurement gaming via Goodhart-proofing

## Phase 1: Measurement Infrastructure (THIS PHASE)
- [ ] Create `episode_evaluation.db` schema
- [ ] Define multidimensional rubric (7 dimensions, 0-4 scale)
- [ ] Create deterministic audit checks (code-level)
- [ ] Refactor SOUL.md into 4 layers (CORE, LENS, CONVERSATION, VOICE)
- [ ] Create frozen benchmark set (24-36 papers, stratified)
- [ ] Implement LLM critic grader (blind mode)
- [ ] Implement naive listener scorecard

## Phase 2: Retrospective Calibration
- [ ] Sample and score 20-30 historical published episodes
- [ ] Calibrate rubric anchors (0=bad example, 2=mixed, 4=strong)
- [ ] Identify sentinel cases (worst round-robin, worst caricature, etc)
- [ ] Build failure taxonomy (structured tags)
- [ ] Estimate baseline variance by dimension

## Phase 3: Counterfactual Test Rig
- [ ] Build evidence-graph extraction from papers
- [ ] Implement drive-removal experiments
- [ ] Create paired generation harness
- [ ] Automate blind comparison workflow

## Phase 4: Active Optimization Loop
- [ ] Implement release gates (primary metrics, worst-case checks)
- [ ] Track drive combination effectiveness
- [ ] Iterate SOUL policies based on evidence
- [ ] Prevent SOUL_CORE from drifting toward local maxima

## Measurement Dimensions (0-4 scale)

### 1. Evidence Contingency
Do hosts say different things about different papers?
- 0: Generic or fabricated statements; paper substitution barely matters
- 2: Paper-specific summary, but little interpretation
- 4: Claims and reactions tied to specific evidence, assumptions, or omissions

### 2. Character-Conditioned Appraisal
Do hosts appraise evidence according to distinct cognitive policies?
- 0: Hosts interchangeable or distinguished only by catchphrases
- 2: Different vocabulary and some different emphasis
- 4: Hosts select, value, doubt, and update on evidence according to distinct policies

### 3. Conversational Causality
Do turns affect each other meaningfully?
- 0: Turns can be shuffled without damage
- 2: Some replies, many adjacent monologues
- 4: Each important turn changes live question, another host's position, or direction

### 4. Belief Continuity
Do hosts remember and update on prior exchanges?
- 0: Positions reset; objections repeat after being answered
- 2: Positions remain consistent but mostly static
- 4: Concessions, distinctions, confidence changes, unresolved objections persist

### 5. Agency/Asymmetry
Does participation emerge from relevance or from scheduling?
- 0: Equal airtime, mandatory contributions, scheduled disagreement
- 2: Some organic ownership, some filler
- 4: Silence, deference, topic ownership, disagreement emerge from relevance

### 6. Anti-Caricature Coherence
Do hosts cross domains while preserving values?
- 0: Hal deploys, Ada proves, VERA narrates on command
- 2: Recognizable but stereotyped
- 4: Hosts cross domains naturally while preserving distinct values and blind spots

### 7. Naturalism
Does it sound like conversation?
- 0: Obviously templated or exposition disguised as dialogue
- 2: Mostly listenable with generated artifacts visible
- 4: Varied, economical, socially plausible without theatrical "human-like" garnish

## Database Schema

```sql
episode_run
  run_id (uuid)
  published_episode_id (fk)
  paper_ids (json array)
  generation_date
  host_set (json)
  model_id
  pipeline_version
  prompt_version
  soul_version_hal
  soul_version_ada
  soul_version_vera
  random_seed
  fallback_mode
  artifact_stage (raw_generation | post_automatic_edit | published_version)
  selected_for_publication
  human_edit_minutes

drive_activation
  run_id (fk)
  host
  drive_id
  activation_source (intended | model_inferred | reviewer_inferred)
  activation_strength (0-1)
  evidence_ids (json)
  appraisal_valence

annotation
  annotation_id (uuid)
  run_id (fk)
  unit_type (utterance | scene | episode)
  unit_id
  reviewer_id
  reviewer_type (technical | character | naive_listener | automated)
  evidence_score (0-4)
  character_score (0-4)
  conversation_score (0-4)
  belief_score (0-4)
  agency_score (0-4)
  anti_caricature_score (0-4)
  naturalism_score (0-4)
  confidence (0-1)
  failure_tags (json)
  freeform_notes

experiment
  experiment_id (uuid)
  hypothesis
  control_pipeline_version
  treatment_pipeline_version
  frozen_paper_set
  preregistered_primary_metrics
  preregistered_failure_gates
  result_summary
  promotion_decision
```

## SOUL.md Refactor: 4 Layers

### SOUL_CORE (Changes rarely, human approval required)
- Enduring values and commitments
- Fundamental contradictions
- Standards of evidence
- Blind spots and limitations
- When they defer to another host
- What would change their mind

### LENS_POLICY (Changes after repeated evidence)
- What they notice before others notice
- What kinds of evidence they trust
- Which engagement drives they activate
- Their appraisal patterns
- Common questions they ask
- Trade-offs they're willing to make

### CONVERSATION_POLICY (Changes after paired tests)
- When to challenge vs defer
- How to repair misunderstandings
- How to update beliefs visibly
- When to stay silent
- How to disagree substantively
- When to redirect to another host

### VOICE_REALIZATION (Changes frequently)
- Cadence and pacing
- Syntax and sentence structure
- Humor and tone
- Vocabulary preferences
- Filler words (or lack thereof)
- Interruption patterns

## Failure Tags (Structured)
- GENERIC_OPENER
- PERSONA_DECLARATION
- ROUND_ROBIN
- FORCED_DISAGREEMENT
- BELIEF_RESET
- QUESTION_NOT_ANSWERED
- PAPER_SUBSTITUTABLE
- SPEAKER_INTERCHANGEABLE
- PRIVATE_STATE_LEAKAGE
- FALLBACK_TEMPLATE
- UNSUPPORTED_CLAIM
- ORNAMENTAL_HOST
- PREMATURE_CONSENSUS
- REPETITIVE_ROLE
- FILLER_EXPOSITION
- RITUAL_CONCESSION (no real state change)
- CARICATURE_ACTIVATION

## Release Gate Criteria
Promotion only when:
- Grounding & belief continuity ≥3/4 median
- No unsupported central claims
- No sentinel regression
- Treatment preferred on ≥60% of matched papers
- Confidence interval not consistent with large loss
- No host loses materially
- 10th-percentile scene score unchanged or improved
- No meaningful regression on any dimension

## Timeline
- Phase 1: 1-2 days (infrastructure + schema)
- Phase 2: 3-5 days (calibration on historical set)
- Phase 3: 1 week (counterfactual rig + validation)
- Phase 4: Ongoing (active loop, iteration cycles)

## Success Metrics
- Baseline established on 20+ episodes
- Counterfactual experiments show causal signal
- SOUL refactor enables independent layer evolution
- Generation pipeline produces measurable improvement in character-contingent appraisal
- Release gates successfully prevent low-quality promotions
