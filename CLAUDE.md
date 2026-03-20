# paper-feed: Claude AI Assistant Preferences

## Project Overview

paper-feed generates AI research podcast episodes from PDF papers.
The public podcast is **AI Post Transformers**, hosted at
https://podcast.do-not-panic.com with episodes stored in a
Cloudflare R2 bucket. The RSS feed is at `podcasts/feed.xml`
(uploaded to R2 after each publish).

Legacy functionality includes a daily paper digest pipeline that
scores arXiv papers against a research interest profile.

## Architecture

### File map

**Entry points:**
- `gen-podcast.py` — CLI: generate episodes, publish, list, digest, queue, viz-sync
- `Makefile` — targets: `queue`, `publish`, `publish-site`, `viz-sync`

**Scoring pipeline (editorial queue):**
- `editorial_scorer.py` — `EditorialScorer`: first-pass embedding scorer
- `llm_reviewer.py` — `LLMReviewer`: second-pass LLM editorial review
- `paper_record.py` — `PaperRecord` dataclass (canonical pipeline record)
- `paper_queue.py` — queue orchestration, `build_final_queue`, HTML/RSS output

**LLM and content generation:**
- `llm_backend.py` — `get_llm_backend`, `llm_call` (3 backends)
- `podcast.py` — episode generation workflow
- `elevenlabs_client.py` — multi-pass script generation + TTS synthesis
- `image_gen.py` — DALL-E cover art generation

**Data and publishing:**
- `db.py` — SQLite schema, upserts, queries (`papers.db`)
- `rss.py` — RSS 2.0 feed builder
- `r2_upload.py` — Cloudflare R2 upload
- `pdf_utils.py` — PDF download and text extraction
- `fun_facts.py` — fun fact pool for episode intros
- `interests.py` — `InterestScorer` (legacy single-profile scorer)
- `viz_catalog.py` — visualization catalog sync (`run_viz_sync`)

**Legacy migration:**
- `mirror_legacy.py` — scrape, mirror, and rebuild legacy episodes

**Sources:**
- `sources/arxiv_source.py` — arXiv API fetcher
- `sources/hf_daily.py` — HuggingFace Daily Papers scraper
- `sources/semantic.py` — Semantic Scholar citation enrichment

**Tests:**
- `tests/conftest.py` — shared fixtures (config, editorial_scorer)
- `tests/test_editorial_scorer.py` — taxonomy, scoring, filters
- `tests/test_llm_reviewer.py` — adjustments, badges, failure handling
- `tests/test_viz_catalog.py` — caching, matching, idempotency
- `tests/regression_cases.yaml` — curated edge-case papers

**Config files:**
- `config.yaml` — main settings (arXiv categories, models, podcast params)
- `weights.yaml` — scoring weights, boosts, penalties, queue allocation
- `editorial_lenses.yaml` — taxonomy buckets, statuses, badges, category maps
- `seed_sets/*.yaml` — embedding profiles (public, memory, negative)

### Key classes

- **PaperRecord** (`paper_record.py`) — dataclass with ~40 fields
  covering identity, source signals, taxonomy, similarities,
  features, composites, penalties, and LLM review. Created via
  `from_paper_dict()`, serialized via `to_dict()`.
- **EditorialScorer** (`editorial_scorer.py`) — loads seed
  embeddings and weights, runs batch embed + taxonomy + filters +
  feature scoring + composite computation. Entry: `score_papers()`,
  then `select_shortlist()`.
- **LLMReviewer** (`llm_reviewer.py`) — parallel LLM calls with
  7-question rubric. Entry: `review_papers()`. Applies score
  adjustments, badges, and status. Falls back to Monitor on error.
- **LLMBackend** (`llm_backend.py`) — `get_llm_backend(config)`
  returns a backend dict, `llm_call()` dispatches to claude-cli,
  openai, or anthropic. JSON parsing with progressive repair.
- **InterestScorer** (`interests.py`) — legacy single-profile
  scorer using primary/secondary interest embeddings and keyword
  boosts. Used when `editorial.enabled` is false.

### Data flow

```
arXiv/HF/Scholar/GitHub → paper dicts
  → PaperRecord.from_paper_dict()
  → EditorialScorer.score_papers()
    → batch embed → classify taxonomy → editorial filters
    → compute similarities → compute features → composites
  → EditorialScorer.select_shortlist()
  → LLMReviewer.review_papers()
    → parallel LLM calls → apply adjustments/badges/status
  → build_final_queue()
    → Bridge / Public / Memory / Monitor / Deferred / Out of Scope
  → write queue.yaml + queue.html + queue.xml
```

### Database tables

- **papers** — cached paper metadata and scores
- **podcasts** — episode records with audio paths and URLs
- **podcast_papers** — links podcasts to source arXiv papers
- **covered_topics** — topic novelty/fatigue tracking
- **fun_facts** — intro fun fact pool with used/unused status

### Common modification patterns

**Adding a new editorial section/queue bucket:** Edit
`build_final_queue()` in `paper_queue.py` to add the partition
logic. Add the slot count to `weights.yaml` under `final_queue`.
Update the HTML/RSS output functions in `paper_queue.py`.

**Changing scoring weights:** Edit `weights.yaml`. The three
composite formulas (`public_interest`, `memory`, `quality`) are
weighted sums — coefficients should sum to 1.0. Boosts and
penalty caps are also there.

**Adding a new badge:** Add it to the `badges` list in
`editorial_lenses.yaml`. Add it to the `badges` array in the
LLM review prompt in `llm_reviewer.py`
(`REVIEW_PROMPT_TEMPLATE`). No code changes needed — badges are
free-form strings stored in `PaperRecord.badges`.

**Adding a new paper source:** Create a fetcher in `sources/`,
returning a list of paper dicts with at minimum `arxiv_id`,
`title`, `abstract`. Wire it into `run_queue()` in
`paper_queue.py` alongside the existing parallel fetches.
`PaperRecord.from_paper_dict()` handles normalization.

**Changing the LLM review prompt:** Edit
`REVIEW_PROMPT_TEMPLATE` in `llm_reviewer.py`. The template
receives first-pass scores and paper metadata as format
variables. The expected JSON response schema is defined inline.

**Adding a visualization source:** Add an entry to the
`visualization.sources` list in `config.yaml` with `name` and
`url`. The catalog JSON must have `base_url`,
`visualizations[].url`, and `visualizations[].papers[].id`.
Run `make viz-sync` to test.

**Adding a test:** Add test functions to the appropriate file
in `tests/`. For new regression cases, add entries to
`tests/regression_cases.yaml` with `name`, `paper` dict, and
optional `expected` fields. Use the shared `editorial_scorer`
fixture from `conftest.py` (session-scoped, loaded once).

## Podcast Generation

### Primary workflow: generate from PDF URLs

```bash
source ~/.enhance-bash   # loads API keys

.venv/bin/python gen-podcast.py \
  https://arxiv.org/pdf/XXXX.XXXXX \
  https://arxiv.org/pdf/YYYY.YYYYY \
  --goal-file inputs/episode-goal.txt \
  --description-file inputs/episode-description.txt
```

For quick one-off runs, `--goal TEXT` and `--description TEXT`
work as inline alternatives to the file variants.

### Input preservation

Every generation run automatically saves its inputs to a versioned
directory under `inputs/YYYY/MM/DD/{slug}-v{N}-{hash}/` containing
`urls.txt`, `goal.txt`, and `description.txt`. The version N
auto-increments when regenerating the same topic on the same day.

### Publishing to R2

After generation, review the draft in `drafts/YYYY/MM/`, then:

```bash
.venv/bin/python gen-podcast.py publish
# or append --publish to the generation command
```

This uploads to R2, copies files to `public/YYYY/MM/`, and
regenerates the RSS feed.

### Directory layout

- `drafts/YYYY/MM/` — newly generated episodes (work-in-progress)
- `public/YYYY/MM/` — local mirror of R2-published episodes
- `inputs/YYYY/MM/DD/` — saved generation inputs per episode
- `podcasts/` — RSS feed files (`feed.xml`, `anchor_feed.xml`)

### Public repo vs local runtime state

This repository is public. Do not accidentally commit local podcast
runtime state, generated episode payloads, or machine-specific caches.

The following paths are local-only and intentionally ignored by git:

- `papers.db` / `*.db` — local SQLite cache of papers, podcasts, and
  topic history. This is runtime state, not source.
- `drafts/` — generated draft episodes and companion assets waiting for
  review.
- `public/` — local mirror of already-published episode files copied
  from R2 or prepared for publication.
- `inputs/` — saved prompt/URL bundles used to generate specific
  episodes. Useful locally, but not source code.
- `podcasts/` — generated RSS output and other publish artifacts.
- `queue/` and `queue.yaml` — local editorial queue output.
- `viz/`, `viz_cache/`, `generated_covers/` — generated visuals and
  caches. Only commit visualization source/templates when explicitly
  intended for publication.
- `.claude/`, `.venv/`, `__pycache__/`, `*.pyc` — machine-local tool
  state and Python cache files.

Treat these as deploy/runtime artifacts. Sync them between hosts with
`rsync` or explicit publish steps, not normal git commits.

### Episode filenames

Episodes use unique filenames:
`{YYYY-MM-DD}-{slug}-{hash6}.mp3` with companion `.txt`, `.srt`,
`.json`, `.png` files sharing the same stem. The hash is derived
from title + date + sorted URLs, so different inputs always produce
different filenames.

### R2 and public URLs

Audio files are uploaded to R2 under `episodes/{basename}`. Public
URLs follow `https://podcast.do-not-panic.com/episodes/{basename}`.
The RSS feed references these URLs for podcast distribution.

### Legacy episodes and anchor_feed.xml

The podcast launched on Spotify/Anchor in August 2025. Those ~467
legacy episodes were originally hosted on Anchor with audio on
Spotify's CloudFront CDN. When the podcast migrated to self-hosted
R2, Spotify set up a 301 redirect from the old Anchor RSS URL to
our `feed.xml`. This means the old feed is no longer available
from Spotify.

All legacy episode data is now mirrored to R2:
- Audio: `episodes/{slug}.m4a` on R2
- HTML pages: `episodes/{slug}/index.html` on R2
- Cover images: `episodes/{slug}-cover.jpg` or already on R2
- Metadata manifest: `legacy_manifest.json` (local, not committed)

The file `podcasts/anchor_feed.xml` is a locally-generated RSS
feed containing all legacy episodes with R2-hosted audio URLs.
It is loaded by `generate_feed()` in `rss.py` and merged with
the local DB episodes to produce the combined `feed.xml`.

**CRITICAL — LEGACY EPISODE PROTECTION RULES:**

These rules exist because legacy episodes were accidentally wiped
from the RSS feed once before. That must never happen again.

1. Do NOT delete, empty, or overwrite `anchor_feed.xml` with a
   stub or skeleton. This file contains 467 legacy episode RSS
   items. Destroying it removes the entire back catalog from
   podcast apps. If the feed needs regeneration, run:
   ```bash
   python mirror_legacy.py build-feed
   ```

2. Do NOT modify `_load_anchor_items()` in `rss.py` to skip,
   filter, or reduce legacy episodes without explicit user
   approval. The legacy episodes are a core part of the podcast.

3. Do NOT modify `generate_feed()` in `rss.py` to exclude legacy
   items or change deduplication logic without explicit approval.
   The merged feed must always contain both new DB episodes AND
   legacy anchor items.

4. Do NOT download or overwrite `anchor_feed.xml` from the Anchor
   RSS URL in `config.yaml`. That URL now 301-redirects to our
   own feed.xml — fetching it would replace 467 legacy items with
   our current (much smaller) feed, destroying the back catalog.

5. Before any change to `rss.py`, `mirror_legacy.py`, or
   `anchor_feed.xml`, verify that `generate_feed()` still
   produces a feed with both new AND legacy episodes. The total
   should be ~480+ episodes. If the count drops below 400,
   something is wrong — stop and investigate.

6. The legacy audio files on R2 (`episodes/{slug}.m4a`) are the
   ONLY surviving copies. Spotify's CloudFront CDN copies may
   vanish at any time. Do NOT delete R2-hosted legacy audio.

The authoritative source for legacy metadata is
`legacy_manifest.json` and the R2-hosted episode pages.

### LLM backend selection

The pipeline routes all LLM calls through a configurable backend
defined in `llm_backend.py`. Four backends are supported:

```yaml
podcast:
  llm_backend: openai    # "claude-cli", "codex", "openai", or "anthropic"
  llm_model: gpt-5.4     # backend-specific model name
  analysis_model: gpt-5.4
```

**claude-cli**: Calls the `claude` CLI as a subprocess. Uses the
Max subscription at no extra API cost. Temperature is not
controllable via CLI. Model names are Claude CLI model names
(e.g., `sonnet`, `opus`, `haiku`).

**codex**: Calls the `codex exec` CLI as a subprocess. Uses the
Codex subscription at no extra API cost. Model names are OpenAI
model names (e.g., `o3`, `gpt-5.4`).

**openai**: Configured to use the OpenAI Python SDK, but when the
`codex` binary is found in PATH the backend automatically routes
through `codex exec` instead, so that the Codex subscription is
used rather than burning pay-per-token API credits. The SDK
fallback only activates when `codex` is not installed.

**anthropic**: Uses the Anthropic Python SDK. Requires
`ANTHROPIC_API_KEY`. Model names are Anthropic API model IDs
(e.g., `claude-sonnet-4-20250514`).

**Cost note:** The `claude-cli` and `codex` backends use flat-rate
subscriptions ($200/mo each). Prefer these over `openai` or
`anthropic` API backends to avoid per-token charges. The `openai`
backend auto-promotes to `codex` when the CLI is available, so
setting `llm_backend: openai` in config.yaml is safe — it will
not spend API credits as long as `codex` is installed.

`image_gen.py` still uses the OpenAI image API (`gpt-image-1`)
since no Claude/Anthropic equivalent exists. When `OPENAI_API_KEY`
is not set, image generation is silently skipped and the episode
is created without a cover image.

## Git Commit Practices

### Commit Structure
- Make small, atomic commits — one logical change per commit
- Each commit should be functional and not break the build
- Test that code runs successfully before committing

### Commit Messages
- **MANDATORY**: Always use this exact format for ALL commits:
  ```
  file.py: brief description of change

  Detailed explanation of what was changed and why.
  Include technical details about the implementation.

  Generated-by: Claude AI
  Signed-off-by: Luis Chamberlain <mcgrof@kernel.org>
  ```

- **LINE LENGTH**: Maximum 70 characters per line in commit
  messages
  - Subject line (first line): 70 characters max
  - Body paragraphs: 70 characters max per line
- **CRITICAL**: Never use "Generated with [Claude Code]" or
  "Co-Authored-By: Claude"
- **REQUIRED**: Every commit MUST have both "Generated-by:"
  and "Signed-off-by:" trailers
- **NO EXCEPTIONS**: This format is mandatory for ALL commits
- **STYLE**: Be terse and to the point. NO shopping-list style
  bullet points. Write in paragraphs explaining the change,
  rationale, and technical details concisely.

### Development Workflow
1. Make a single focused change
2. Test that the code runs without errors
3. Commit with detailed message
4. Repeat for next change

## Code Style

### Python
- Follow PEP 8 conventions
- No manual formatting quirks
- Prefer editing existing files over creating new ones

## Avoid Silly Language

Do not use the word "comprehensive". It is overused and explains
nothing. Be terse and to the point.

## Episode Numbering: NEVER Use Internal IDs

The podcast has been published across multiple platforms over its
lifetime. The internal database `id` column in the `podcasts` table
is a local auto-increment counter specific to this repository's
SQLite database. It does NOT correspond to any public episode number
and has NO meaning to listeners.

**Rules:**
- NEVER reference an episode by its database ID in descriptions,
  transcripts, or any listener-facing text. Do not write "Episode 43"
  or "episode 47" or any variant.
- When referring to a previous episode, use the episode TITLE instead.
  Example: "as we discussed in 'Why CARTRIDGE Works: Keys as Routers
  in KV Caches'" — not "as we discussed in episode 37."
- This applies to ALL generated text: episode descriptions stored in
  the database, transcript dialogue, SRT subtitles, and RSS feed
  content.
- When generating podcast scripts, instruct the LLM that the hosts
  must refer to prior episodes by title, never by number. If the
  script generation prompt allows cross-references to earlier
  episodes, those references must use the episode title verbatim.
- If you find existing episodes with numbered references in their
  descriptions or transcripts, fix them by replacing the number with
  the episode title.

## Content Isolation: No External Private Context

The podcast content must be derived ONLY from:
1. The source paper(s) provided as input URLs
2. Published academic literature cited by or related to those papers
3. General public knowledge about the research field
4. Previously published episodes of this podcast (referenced by title)

**Strictly forbidden in generated scripts, descriptions, and
transcripts:**
- Any private research, experiments, or unpublished results that the
  podcast operator or contributors are working on independently
- Internal project names, experiment codenames, or proprietary
  methodologies not described in the source papers
- References to private conversations, internal discussions, or
  personal R&D agendas
- Any knowledge that could only come from the operator's own
  unpublished work rather than from the source paper or public
  literature

The podcast is a PUBLIC artifact. The operator's own research
interests and ongoing experiments are PRIVATE and must never leak
into episode content. If a source paper happens to be related to
the operator's private work, the episode must discuss the paper
strictly on its own published merits — never connecting it to
unpublished internal projects, using internal terminology, or
revealing that the operator has related work in progress.

This separation must be maintained even when the generation prompt
or system context contains information about the operator's private
research. That context exists for the agent's awareness only and
must NEVER flow into the podcast output.

When generating scripts, the LLM prompt must explicitly instruct
the hosts to discuss papers based solely on their published content,
public citations, and the broader academic landscape. No insider
knowledge, no proprietary framing, no "our earlier work on X"
unless X is a previously published episode of this podcast.

## Cross-Agent Access

To avoid other agents missing these guidelines, ensure every agent
entrypoint symlinks back to this document:
- `CODEX.md` → `CLAUDE.md` (OpenAI Codex legacy name)
- `AGENTS.md` → `CLAUDE.md` (OpenAI Codex/Agents current name)

Both must always be symlinks, never independent files. If an agent
framework adds a new instruction filename, symlink it here too.
