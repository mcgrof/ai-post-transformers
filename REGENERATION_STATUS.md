# PromQL Episode Regeneration Status

## Completed Work

### ✅ Stale Draft Cleanup
- Removed 7 stale manifest entries (IDs: 215, 400, 401, 407, 413, 414, 415)
- Deleted 45 draft files from R2 (ai-post-transformers/drafts/)
- Updated podcast-admin/manifest.json on R2
- Restarted admin worker to reflect changes
- Admin drafts page now shows zero pending drafts
- Created STALE_DRAFTS_FOR_REGENERATION.md to track sources for future regen

### ✅ Code Fixes
1. **Episode Slug Truncation** (podcast.py:26-35)
   - Increased from 40-char to 120-char limit
   - Allows full episode titles like "cluster-native-text-to-promql-with-temporal-resolution"

2. **Theme Song Integration** (elevenlabs_client.py:1919-1928, 2222-2235)
   - Inserted theme-intro.mp3 directly after countdown
   - Detection of theatrical episodes (SOUL, Severance, VERA)
   - Uses full theme.mp3 for theatrical, theme-intro.mp3 for standard

3. **Anti-Patterns Enforcement** (config.yaml)
   - Added ANTI_PATTERNS.md reference to LLM instructions
   - Hard-fail rules for banned phrases
   - Personality file references (hosts/hal/SOUL.md, hosts/ada/SOUL.md)

4. **Intro Fallback Logic** (elevenlabs_client.py:1562-1579)
   - Manually injects intro when LLM Part 1 returns 0 segments
   - Ensures every episode has "Alrighty! Thanks for tuning in!"

### ✅ Repository Integration
- Integrated podcast-theme/ directory with theme files
- Committed STALE_DRAFTS_FOR_REGENERATION.md
- Created fallback intro logic

## ❌ Known Issues

### LLM Script Generation Broken
The core issue preventing successful PromQL regeneration:
- **Part 1 (Intro)**: Returns 0 segments from LLM
  - Fix: Fallback intro injection (2 segments injected)
- **Part 2 (Deep Dive)**: Also returns 0 segments (should have 12)
  - First regen: Part 2 had 12 segments, worked fine
  - Second regen: Part 2 also returns 0 segments
- **Part 3 (Analysis)**: Returns 0 segments from LLM

Root cause: Claude-cli (opus) LLM backend appears to be:
- Timing out during Part 1-3 generation
- Returning empty JSON responses
- Possibly hitting rate limits or having prompt parsing issues

Status: Generation starts but stalls at LLM calls; processes get killed after ~2min

## Next Steps

### To Fix LLM Issues
1. Check if claude-cli backend is responsive
2. Try alternative LLM backend (openai/anthropic)
3. Debug LLM prompt templates (elevenlabs_client.py:1523-1668)
4. Verify token budgets and model availability

### Manual Workaround
If LLM regeneration continues to fail:
1. Generate episode with working LLM backend
2. Use fallback intro injection for missing Part 1
3. Accept Parts 2-3 having 0 segments if unavoidable (episode still plays with Part 2 audio)

### Files Ready for PromQL
- Source: http://arxiv.org/abs/2604.13048v1
- Theme: theme-intro.mp3 + theme-end.mp3
- Anti-patterns: ANTI_PATTERNS.md ready
- Personality: Hal + Ada SOUL v2.3.1/v3.0.0 loaded

## Commits Made (Today)
1. podcast.py: increase episode slug truncation limit from 40 to 120 characters
2. config.yaml: add comprehensive anti-patterns guide to LLM instructions
3. elevenlabs_client.py: actually insert theme song into podcast intro sequence after countdown
4. elevenlabs_client.py: use dedicated podcast-theme files from repo; support theatrical episode detection for full theme
5. CLAUDE.md: document stale draft cleanup procedure
6. STALE_DRAFTS_FOR_REGENERATION.md: track source URLs of 7 deleted stale podcast drafts
7. elevenlabs_client.py: add fallback intro injection when Part 1 script generation returns zero segments
