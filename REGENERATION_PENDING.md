# Podcasts Pending Regeneration (Once Software Fixed)

These 4 episodes were approved but generated with old/broken software.
They must be regenerated once the podcast generation system is fixed
and tested with proper theme song integration and title handling.

## Approved Episodes to Regenerate

### 1. SkillOpt-Lite: Better and Faster Agent Self-evolution
- **Source:** arXiv 2607.03451
- **URL:** https://arxiv.org/pdf/2607.03451
- **Submission ID:** 2026-07-08T13-47-33-563Z
- **Status:** approved_for_publish
- **Current Draft:** drafts/2026/07/2026-07-08-skillopt-lite-and-the-smallest-self-evol-020af6.mp3
- **Notes:** Generated with old pipeline; regenerate to test theme songs

### 2. Can Matrix-Enhanced CPUs Replace GPUs?
- **Source:** Custom PDF upload
- **URL:** https://podcast.do-not-panic.com/uploaded-pdfs/2026-07-09T15-03-00-780Z-need-gpus.pdf
- **Submission ID:** 2026-07-09T15-03-03-817Z
- **Status:** approved_for_publish
- **Current Draft:** drafts/2026/07/2026-07-09-can-matrix-enhanced-cpus-replace-gpus-ececa4.mp3
- **Notes:** Uploaded PDF; regenerate with corrected generation

### 3. Accurate, Interdisciplinary and Transparent Structure-property Understanding with Deep Native Structural Reasoning
- **Source:** arXiv 2607.07708
- **URL:** https://arxiv.org/pdf/2607.07708
- **Submission ID:** 2026-07-09T15-17-39-566Z
- **Status:** approved_for_publish
- **Current Draft:** drafts/2026/07/2026-07-09-deep-native-structural-reasoning-for-str-9b70d8.mp3
- **Notes:** Generated with old pipeline; regenerate to test theme songs

### 4. GPT-5.6 System Card and High-Risk Deployment Mitigations
- **Source:** OpenAI System Card PDF
- **URL:** https://deploymentsafety.openai.com/gpt-5-6-preview/gpt-5-6-preview.pdf
- **Submission ID:** 2026-07-09T15-25-46-202Z
- **Status:** approved_for_publish
- **Current Draft:** drafts/2026/07/2026-07-09-gpt-56-system-card-and-high-risk-deploym-579534.mp3
- **Notes:** OpenAI safety doc; regenerate to ensure quality

## Regeneration Process

Once the PromQL podcast has been validated:
1. Test theme song integration (intro vs full vs end)
2. Verify title truncation (120-char limit)
3. Confirm SOUL personality context working
4. Then regenerate each of the 4 above via:

```bash
source ~/.enhance-bash
.venv/bin/python gen-podcast.py [PDF_URL] --publish
```

After regeneration, publish each to R2 and the admin UI will auto-refresh.

## Rejected/Failed (Delete)

- 2026-07-08T07-45-29-476Z: REJECTED (arXiv 2607.04371 - Puzzle Compression)
- 2026-07-14T01-09-33-516Z: GENERATION_FAILED (arXiv 2604.13327 - Event Tensor)

These are marked for deletion from the submissions queue.
