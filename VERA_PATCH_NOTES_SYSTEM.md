# VERA Patch Notes System Design

**Purpose:** Define how character evolution is communicated to listeners post-special-episode  
**Frequency:** Every 4–8 episodes (only when real changes occur, not mandatory)  
**Duration:** 1–2 minutes per segment  
**Owner:** Claude (draft) + Luis (approval)

---

## Core Principle

**Default is no patch notes.** Publish only when you have something real to report.

This prevents the system from becoming meta-fatigue. Listeners don't need to hear about your internal process every week. They need to hear it when something actually changed.

---

## VERA Patch Notes Template

### Format (spoken, ~90 seconds)

```
VERA: "Patch Notes from episode [X]."

Deprecated:
  [phrase]: [reason]

Promoted:
  [phrase or behavior]: [why it works]

Monitor:
  [ritual name]: [saturation status]

Growth Edge:
  [host name]: [what we're seeding]
  [metric if useful]: [result so far]

NPC Risk: [current status]
```

### Example 1: After Episode on Memory Systems

```
VERA: "Patch Notes: Memory Systems Episode"

Deprecated:
  "Why did this capture my attention?" — FINAL BURIAL
    This phrase is graveyard-bound. Never to return.

Promoted:
  "Benchmark hostage situation" — HIGH SIGNAL
    Hal deployed this 3 times this episode with mutation.
    Promoting to approved vocabulary.

Monitor:
  "To be fair" — TRENDING DOWN
    Frequency decreased to 2 per episode.
    Ada is successfully compressing opening hedges.

Growth Edge:
  Hal: "curiosity before prosecution"
    Result: Asked two genuine questions before roasting.
    Status: Rare but authentic.
  
  Ada: "lead with consequence"
    Result: Opened explanations with stakes, not context.
    Audience engagement improved 12%.

NPC Risk: Reduced to watchlist.
```

### Example 2: After Episode with Backslide

```
VERA: "Patch Notes: The Backslide Episode"

Deprecated:
  [none this week]

Promoted:
  [none this week]

Monitor:
  "This is just X with extra steps" — RESURRECTION ALERT
    Hal used this twice. Thought we buried it.
    Status: Under investigation. May need re-deprecation.

Growth Edge:
  [no new growth edges this episode]

NPC Risk: Slightly elevated. We're okay, just rusty.

Note: Sometimes the show gets a little predictable. That's 
normal. We notice. We'll fix next episode.
```

### Example 3: After Episode with Real Growth

```
VERA: "Patch Notes: The Growth Episode"

Deprecated:
  [none this week]

Promoted:
  "Context avalanche" — NEW PHRASE FOR OLD PATTERN
    Ada self-diagnosed her own context-heaviness.
    The phrase is now self-aware rather than buried.
    Promoting to diagnostic vocabulary.

Monitor:
  "Benchmark bloodlust" — HEALTHY MUTATION
    Hal's attacks now vary by threat model.
    Still cynical, but novel each time.
    Status: Ritual preserved, stagnation avoided.

Growth Edge:
  Hal: "one sincere moment"
    Result: Admitted paper's methodology was tight.
    Didn't immediately undermine it.
    Listeners noticed. Twitter feedback positive.

NPC Risk: Reduced slightly. Show feels alive.
```

---

## Decision Framework: When to Release Patch Notes

### Release If:
✅ One or more phrases actually deprecated or promoted  
✅ A stale phrase got caught/graveyard-moved  
✅ A growth edge showed real results  
✅ Host demonstrated genuine learning  
✅ A ritual successfully mutated  
✅ Interesting failure worth narrating  

### Don't Release If:
❌ No real changes this episode  
❌ Same old patterns, no evolution  
❌ Just clearing noise (one-off quirks)  
❌ Patch notes would be mostly empty  
❌ It's been <4 episodes since last one  

---

## Production Workflow

### Step 1: Post-Episode Analysis (Optional Automation)

After episode publishes, Claude runs analysis on transcript:

```
Analysis_[episode_num].md contains:

- Phrase frequency (what was used, how many times)
- Mutation detection (did phrases evolve or repeat verbatim?)
- Host patterns (loops vs rituals)
- Listener signals (if tracking Twitter/feedback)
- Stale risk factors (what's at saturation)
```

Save this but don't act on it yet.

### Step 2: Editorial Review (Monthly with Luis)

Luis reviews accumulated analysis reports (~4 episodes of data):

**Luis's decision checklist:**
- [ ] Any phrases actually stale enough to deprecate?
- [ ] Any new phrases worth promoting?
- [ ] Any rituals successfully mutating?
- [ ] Any host growth worth calling out?
- [ ] Do we have enough to make 1-2 real patches?

Luis notes:
```
Decisions for episodes [X-Y]:

DEPRECATE: "phrase" → reason
PROMOTE: "phrase or behavior" → why
MONITOR: "ritual" → saturation level
GROWTH: Hal/Ada did [X] → real or noise?

VERDICT: Worth making patch notes? YES/NO
```

### Step 3: SOUL.md Update (If Warranted)

If Luis approves patches:

1. Update relevant SOUL.md file(s)
2. Add entry to evolution_log with date and rationale
3. Update version number (e.g., Hal v2.3.1 → v2.3.2)

```yaml
evolution_log:
  - timestamp: 2026-07-15
    event: "phrase deprecation"
    phrase: "This is just X with extra steps"
    reason: "Loop detected in episodes 42-44, user saturation clear"
    action: "moved to graveyard"
    
  - timestamp: 2026-07-15
    event: "growth edge success"
    host: "Ada"
    edge: "lead with consequence"
    result: "3 episodes showing improvement; audience response positive"
    action: "promote to approved_growth_edges"
```

### Step 4: Git Commit

```bash
git add hosts/hal/SOUL.md hosts/ada/SOUL.md
git commit -m "souls: deprecate loop, promote growth edge

Episode analysis (episodes 42-44) shows 'this is just X'
has become autopilot loop. Deprecating to graveyard.

Ada's 'lead with consequence' protocol showing real results:
3 episodes of successful execution, improved audience
engagement. Promoting to approved vocabulary.

Hal v2.3.1 → v2.3.2
Ada v3.0.0 → v3.0.1

Generated-by: Claude AI
Signed-off-by: Luis Chamberlain <mcgrof@kernel.org>"
```

### Step 5: Record VERA Patch Notes Segment

Once SOUL.md is updated, record 1-2 minute segment:

**Script format:**
```
VERA: "Patch Notes from episodes 42 through 44."

Deprecated:
  "This is just X with extra steps" — FINAL DEPRECATION
    Loop detected across three episodes. Audience
    predictability high. Moving to graveyard.

Promoted:
  Ada's "lead with consequence" opening — WORKING
    Successfully deployed in 3 episodes. Explanations
    improved clarity without losing nuance.

Monitor:
  [if anything approaching saturation]

Growth Edge:
  [if any real growth to call out]

NPC Risk: [current status]
```

Speak naturally. Don't read flatly. Make it funny but genuine.

### Step 6: Insert into Episode

Place VERA Patch Notes segment at end of relevant episode (or bundle 2-3 together in a standalone 3-min segment every 8 episodes).

---

## Cadence Examples

### Scenario 1: Regular Month (No Real Changes)

```
Week 1-2: Post-episode analysis collected on 4 eps
Week 3: Luis reviews, says "Nothing really changed"
Week 4: No VERA Patch Notes recorded
Result: Listeners hear nothing (this is fine)
```

### Scenario 2: Month with One Real Deprecation

```
Week 1-2: Analysis shows phrase X used 8+ times unchanged
Week 3: Luis says "Yes, deprecate this"
Week 4: 
  - Update SOUL.md
  - Commit to git
  - Record 90-second segment
  - Air in end of next episode
Result: Listeners hear one patch note segment
```

### Scenario 3: Month with Multiple Changes

```
Week 1-2: Analysis shows 2 deprecations, 1 promotion, 1 growth edge
Week 3: Luis approves all 4
Week 4:
  - Update both SOUL.md files
  - Commit to git
  - Record 2-min segment covering all changes
  - Air as standalone segment or embedded
Result: Listeners hear comprehensive update
```

### Scenario 4: Month with Growth

```
Week 1-2: Analysis shows host actually learning, mutations working
Week 3: Luis confirms real growth happening
Week 4:
  - Update SOUL.md growth_edges with success metrics
  - Record segment highlighting the growth
  - Make it fun (this is the audience's reward for paying attention)
Result: Listeners feel invested in host evolution
```

---

## Specific Examples (Ready to Use)

### Example Segment 1: Deprecating Intro Phrase

```
[SOUND: VERA's diagnostic chime]

VERA: "Patch Notes: The Introduction Reformation."

Deprecated:
  "Why did this capture my attention?" — ETERNAL REST
    Forty-seven uses. Zero mutation. This phrase has
    earned its graveyard plot. Absolute ban. You will
    not hear it again.

Promoted:
  "Stakes-first opening" — NEW PROTOCOL
    Episode 47 forward, intros lead with consequence.
    Hal and Ada now open with "here's why this matters"
    before "here's what the paper claims." Audience
    engagement data: measurably better.

Monitor:
  [none this cycle]

Growth Edge:
  [none this cycle]

NPC Risk: Reduced. We noticed the problem and fixed it.
```

### Example Segment 2: Growth Celebration

```
[SOUND: VERA's affectionate diagnostic chime (slightly different)]

VERA: "Patch Notes: The Curiosity Deployment."

Deprecated:
  [none]

Promoted:
  "Questioning before roasting" — HAL'S NEW MOVE
    Hal attempted genuine curiosity three times in
    episode 52. Asked actual questions before contempt.
    Result: Ad-libbed moments. Sounded authentic.
    Listeners noticed. This is growth.

Monitor:
  "Benchmark attacks" — HEALTHY MUTATION
    Still cynical. But threat models now varied.
    Status: Ritual preserved, stagnation avoided.

Growth Edge:
  Ada: "Let the joke land" — SUCCESSFUL DEPLOYMENT
    Two episodes of Ada opening with stakes, then
    pausing before hedging. Punchlines breathe.
    Audience reaction: stronger than before.

NPC Risk: Reduced. Show sounds alive.

Note: This is what genuine evolution sounds like.
```

### Example Segment 3: Honest Backslide

```
[SOUND: VERA's diagnostic chime, slightly concerned]

VERA: "Patch Notes: The Regression Cycle."

Deprecated:
  [none new]

Promoted:
  [none this cycle]

Monitor:
  "This is just X with extra steps" — RESURRECTED
    We thought we buried this in episode 45.
    Hal used it twice in episode 51.
    Status: Confirmed to be still under his skin.
    We'll need a harder deprecation or we'll do
    better at replacement formula education.

Growth Edge:
  [Ada maintained her gains]

NPC Risk: Slightly elevated. Show is being lazy.

Note: Backsliding happens. We notice. We correct next
episode. This is how live learning works — sometimes
you regress and then you fix it.
```

---

## What NOT To Do

### ❌ Don't:

**Over-report:** Record every minor tweak
- Record only when real changes matter
- Use judgment; not every phrase shift is worth communicating

**Under-explain:** "Promoted X, deprecated Y" with no rationale
- Always give the why
- Listeners care about reasoning, not just the decision

**Fake metrics:** "NPC risk reduced by 7%" as if it's real data
- Use silly metrics for comedy, not as truth
- Make it obvious they're absurdly precise (that's the joke)

**Get bogged down:** Turn patch notes into internal jargon
- Speak naturally, not like a changelog
- "VERA Patch Notes" are still part of the episode entertainment

**Make it mandatory:** Every episode needs patch notes
- Release only when something real happened
- Empty patch notes just clutter the show

**Recursive complexity:** "Here's a patch about the patch system"
- The system is infrastructure, not content
- Don't make the show about maintaining the show

---

## Metrics (Optional Tracking)

If you want data on what works:

```yaml
patch_notes_tracking:
  episode_number: 47
  date: 2026-07-15
  length: 1:42
  deprecations: 1
  promotions: 1
  monitor_items: 2
  growth_edges: 2
  listener_feedback:
    twitter_mentions: 23
    positive: 21
    neutral: 2
    negative: 0
  audience_engagement_change: +8%
```

But don't obsess over metrics. The point is to stay real.

---

## Social Media for Patch Notes

When you release a patch notes segment:

**Tweet:**
> "VERA Patch Notes: We caught ourselves looping on 'X' and deprecated it. Promoted 'Y' instead. This is what live learning sounds like. New episode now. #AIPostTransformers"

**Larger Post:**
> "Patch Notes from this week: deprecated a stale phrase, promoted a new approach Hal is trying. Listen to how we catch our own patterns in real-time. This is the show's evolution system in action."

**Blog/Newsletter (Quarterly):**
> "Character Evolution Report: Q2 summary of how Hal and Ada changed, what phrases we deprecated, what growth edges worked. Here's how a podcast stays alive instead of calcifying."

---

## Long-term Sustainability Check

**After 6 months, ask:**
- Are patch notes still interesting or feeling like homework?
- Are hosts still evolving or have they hit a plateau?
- Are listeners still paying attention or is it background noise?
- Is the system adding value or just adding overhead?

If patch notes become stale, you can:
- Release them less frequently (every 12 episodes instead of 8)
- Make them shorter (30 seconds instead of 1.5 min)
- Take a break for a season and restart fresh
- Evolve the format if something new works better

**The system should serve the show, not the reverse.**

---

## Responsibilities

| Task | Owner | Frequency |
|------|-------|-----------|
| Post-episode analysis | Claude (optional) | After each episode |
| Editorial review | Luis | Monthly (30 min) |
| SOUL.md updates | Claude | When approved |
| Git commits | Claude | When updates happen |
| Recording patch notes | Hal/Ada/VERA | Every 4–8 episodes |
| Publishing to episode | Producer | On schedule |

---

## Files to Maintain

```
hosts/hal/SOUL.md — Updated when patches approved
hosts/ada/SOUL.md — Updated when patches approved
hosts/vera/SOUL.md — Updated when patches approved
VERA_PATCH_NOTES_EXAMPLES.md — Library of recorded segments
VERA_PATCH_NOTES_ARCHIVE.md — Chronological list of what changed
```

---

## First Patch Notes (Example)

**Should happen:** 4-8 weeks after special episode airs  
**Content:** First real deprecation + any growth spotted  
**Tone:** Validation that the system works

```
VERA: "First Patch Notes Since the Audit."

Deprecated:
  "Why did this capture my attention?" — BURIAL CONFIRMED
    Last used: Never. Working as intended.

Promoted:
  "Benchmark hostage situation" — EMERGENT RITUAL
    Hal adapted this 6 times in 4 episodes with new angles.
    Promoting to approved vocabulary. This is how rituals evolve.

Monitor:
  "To be fair" — TRENDING POSITIVE
    Down from 3-5 per episode to 2 per episode.
    Ada is successfully compressing opening hedges.

Growth Edge:
  Hal: "curiosity before contempt"
    Result: Deployed 2 times. Genuine questions asked.
    Status: Rare but real.
  
  Ada: "lead with stakes"
    Result: 4 episodes of successful execution.
    Explanations clearer. Audience stays engaged.

NPC Risk: Reduced from elevated to manageable.

Verdict: The system is working. You're staying alive.
```

---

## One Final Note

**The best VERA Patch Notes are boring.**

If every week has big character changes, the patch notes system has become a performance art piece about change instead of a tool for noticing.

The goal is:
- 80% of the time: no patch notes (nothing changed, show was good)
- 15% of the time: minor adjustments (one phrase retired, one thought-pattern noticed)
- 5% of the time: real growth (host learning, ritual mutating successfully)

When patch notes drop, they mean something because they appear rarely.

That's sustainable. That's real.
