# Podcast Quality Guidelines — Distilled from User Feedback

These guidelines are accumulated from all user feedback across draft reviews.
They should be fed into every podcast generation pass as additional context.

## Length & Pacing
- Follow the generation run's word budget; it accounts for single vs multi-paper
  episodes. The budget is a ceiling to work within, not a reason to pad.
- 54-minute draft was rejected as "too fucking long" — respect the limits.
- If the material needs less time, finish sooner. Keep needed explanations.
- Let important comparisons land at a conversational pace. Cut excess figures
  rather than rushing through them. Short questions and answers need no padding.

## Benchmarks People Can Remember
- **Meaning first, evidence second.** Explain what the test measures, then the
  finding, a useful comparison, and the limit on that conclusion.
- Choose the few results that matter to the paper's argument. Summarize patterns
  across tables; include important regressions and trade-offs. Never tour every
  row, read a leaderboard, or trade lists of scores between hosts.
- Usually one comparison and one or two numeric anchors per exchange are enough.
  Follow with interpretation or a real question before introducing more numbers.
- Prefer human-scale expressions grounded in the measurement: "about half the
  wait" or "roughly four more correct answers out of every hundred." Round only
  when the small difference is not itself the point. Keep the exact evidence in
  the planning notes and source paper, rather than reading all of it aloud.
- Keep the baseline, metric, conditions, and caveat clear. Percentage points are
  not relative percentages; throughput is not latency. Do not turn memory savings
  into a claim about GPU count or cost without evidence. Do not call a small gap
  a tie or a decisive win without support for that interpretation.
- Reactions should respond to the finding. No stock amazement, mandatory jokes,
  forced analogies, or repeated catchphrases. Vary the exchange naturally.

Illustrative examples only — these are fictional measurements, not paper results
or dialogue templates to copy into episodes:

- **Table reading:** "On tasks A, B, and C the baseline accuracy percentages
  are 71.2, 74.6, and 69.8; the new model scores 75.3, 78.5, and 73.7."
  **Spoken takeaway:** Ada: "On these three accuracy tests, it gets roughly four
  more answers right out of every hundred than the baseline." Hal: "Does that
  gain hold up across runs?" Ada: "They don't report that uncertainty, so we
  can't tell how repeatable the gain is." The caveat assumes that omission in
  this fictional source; use the actual paper's evidence in real episodes.
- **Table reading:** "Latency is 820 milliseconds versus 410 milliseconds."
  **Spoken takeaway:** Ada: "In their tested setup, the same request takes about
  half the wait — under half a second." Hal: "And under heavier load?"
  The reply must follow what the paper tested, without guessing.

## Tone & Style
- **Conference bar conversation**, not a lecture or news broadcast.
- Honest critical analysis — not hype. If a paper oversells, say so.
- Nerdy humor only when it fits naturally. **No forced jokes, puns, or formulaic quips.**
- Humor must be **situational** — tied to the specific content being discussed.
- **No "AIs talking about AI" meta-commentary** — avoid self-referential AI jokes.
- Max 2 jokes per episode, prefer current events/tech culture references.

## Exchanges That Build Understanding
- Answer the question just asked. The next turn should respond to that answer,
  test it, or explore its consequence. Skip generic interview questions that only
  introduce another prepared speech. Say when the paper cannot answer a question.
- Both hosts should contribute observations and reasoning. Hal need not play dumb
  to make Ada explain; Ada can acknowledge uncertainty or revise an interpretation.
  Do not invent personal experience or previous beliefs to dramatize learning.
- Teach the mechanism: what changes, why it affects the outcome, and its limits.
  Define unfamiliar terms when needed, with enough context for a first-time
  listener. A topic's presence in an earlier episode does not establish familiarity.
- Use concrete examples and apt analogies, with their limits when needed. Avoid
  changing metaphors every turn or treating an analogy as proof.
- Allow brief clarifications and useful callbacks. Remove repeated explanations,
  not follow-ups that resolve something the first explanation left unclear.
- Keep material caveats beside the claim they qualify. Cut repeated generic
  hedges, ceremonial praise, and constant name-addressing instead.
- Character traits guide tone; they are not a checklist of jokes, compliments,
  vulnerable moments, arguments, or emotional reactions to perform.

## Structure
- Must sound like **one continuous conversation** — no "welcome back" or section breaks.
- No fake "next episode" teasers — just a simple farewell.
- Begin the dialogue with Hal's configured intro. The audio pipeline handles
  the countdown and theme; do not add production cues or a second opening.
- **No "welcome back from break"** or any implication of commercial breaks.

## Citations & Attribution
- At first mention, cite **title, first author, institution/lab, year** when known.
  Later mentions use a clear short name. Never invent missing citation details.
- Example: "That's from the Flash Attention paper by Tri Dao out of Stanford, 2022"
- Don't just name-drop — explain WHY you're referencing that work.
- **Never use unpublished drafts, private/internal episodes, local-only artifacts, or operator research notes as sources or callbacks.** Prior-episode references are allowed only for already published public podcast episodes.

## Content Quality
- **Adversarial search findings presented imperatively** — not "the adversarial search found..." Just state the counterpoint as if the host knows it.
- Fun facts must be **real, well-known AI news** — not hallucinated.
- Fun facts must rotate (tracked in DB, never repeat).
- No forced irrelevant fun facts — if nothing fits, skip it.
- Background context is essential for new topics. Don't assume the listener read last week's episode.

## Host Dynamics
- Host A = "Hal Turing" (male, curious interviewer, warm, asks clarifying questions)
- Host B = "Dr. Ada Shannon" (female, expert co-host, sharp, direct, dry wit)
- Interruptions are optional, subject to the configured episode-wide maximum.
  Keep definitions, comparisons, and important qualifications audible.
- Disagree only over a specific claim where different readings are supported;
  explain the evidence that would settle it. Agreement is fine when earned.
- Push back on hype without inventing conflict or forcing a reconciliation.

## What to Avoid
- ❌ Removing host names from audio (verbal intros are fine)
- ❌ Generic jokes unrelated to the paper
- ❌ "AIs talking about AI" self-awareness humor
- ❌ Padding episodes to hit a time target
- ❌ Overly enthusiastic "this changes everything!" claims
- ❌ Formulaic structure (same opening joke pattern every episode)

## Quality Benchmarks — Reference Episodes
When generating new drafts, these published episodes represent the quality bar:
- **"Why CARTRIDGE Works"** — Good technical depth, proper citations
- **"Structured State Space Duality"** — Strong background explanation
- **"Gradient Descent at Inference Time"** — Good critical analysis
- **"Systematic LLM Inference Characterization"** — Good industry context

## Pre-Generation Checklist
Before generating, the pipeline should:
1. Check the Episode Bible for topic overlap with existing episodes
2. Check the Coverage Memo for related work already covered
3. Verify fun facts haven't been used before (DB check)
4. Confirm word count target based on single vs multi-paper
5. Feed these guidelines into the script generation pass
