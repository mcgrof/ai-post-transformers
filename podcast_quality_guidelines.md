# Podcast Quality Guidelines — Distilled from User Feedback

These guidelines are accumulated from all user feedback across draft reviews.
They should be fed into every podcast generation pass as additional context.

## Length & Pacing
- **Single paper: 15-23 minutes** (~3800 words). NEVER exceed 23 min.
- **Multi-paper (4-8 papers): ~30 minutes** max.
- 54-minute draft was rejected as "too fucking long" — respect the limits.
- Don't pad with filler. If the paper doesn't warrant 20 min, make it 15.

## Tone & Style
- **Conference bar conversation**, not a lecture or news broadcast.
- Honest critical analysis — not hype. If a paper oversells, say so.
- Nerdy humor only when it fits naturally. **No forced jokes, puns, or formulaic quips.**
- Humor must be **situational** — tied to the specific content being discussed.
- **No "AIs talking about AI" meta-commentary** — avoid self-referential AI jokes.
- Max 2 jokes per episode, prefer current events/tech culture references.

## Structure
- Must sound like **one continuous conversation** — no "welcome back" or section breaks.
- No fake "next episode" teasers — just a simple farewell.
- Mandatory intro: leading "..." for 1s audio buffer, then countdown 3-2-1, dual-voice "Welcome to AI Post Transformers!", then Hal's intro.
- **No "welcome back from break"** or any implication of commercial breaks.

## Citations & Attribution
- ALL cited papers must include: **title, authors, institution/lab, year**.
- Example: "That's from the Flash Attention paper by Tri Dao out of Stanford, 2022"
- Don't just name-drop — explain WHY you're referencing that work.

## Content Quality
- **Adversarial search findings presented imperatively** — not "the adversarial search found..." Just state the counterpoint as if the host knows it.
- Fun facts must be **real, well-known AI news** — not hallucinated.
- Fun facts must rotate (tracked in DB, never repeat).
- No forced irrelevant fun facts — if nothing fits, skip it.
- Background context is essential for new topics. Don't assume the listener read last week's episode.

## Host Dynamics
- Host A = "Hal Turing" (male, curious interviewer, warm, asks clarifying questions)
- Host B = "Dr. Ada Shannon" (female, expert co-host, sharp, direct, dry wit)
- Rare voice overlaps/interruptions (1 per episode) — make them feel spontaneous.
- Occasional heated disagreements (every 2nd episode) — genuine intellectual tension.
- Hosts should NOT agree on everything. Ada should push back on hype.

## What to Avoid
- ❌ Removing host names from audio (verbal intros are fine)
- ❌ Generic jokes unrelated to the paper
- ❌ "AIs talking about AI" self-awareness humor
- ❌ Padding episodes to hit a time target
- ❌ Overly enthusiastic "this changes everything!" claims
- ❌ Formulaic structure (same opening joke pattern every episode)

## Quality Benchmarks — Reference Episodes
When generating new drafts, these published episodes represent the quality bar:
- **ID 37: "Why CARTRIDGE Works"** — Good technical depth, proper citations
- **ID 34: "Structured State Space Duality"** — Strong background explanation
- **ID 46: "Gradient Descent at Inference Time"** — Good critical analysis
- **ID 38: "Systematic LLM Inference Characterization"** — Good industry context

## Pre-Generation Checklist
Before generating, the pipeline should:
1. Check the Episode Bible for topic overlap with existing episodes
2. Check the Coverage Memo for related work already covered
3. Verify fun facts haven't been used before (DB check)
4. Confirm word count target based on single vs multi-paper
5. Feed these guidelines into the script generation pass
