# paper-feed

Generate podcast episodes from AI research papers. Pass one or more
PDF URLs and get a two-host conversational podcast with transcripts,
subtitles, and episode cover art. Multiple PDFs produce a combined
episode that identifies common themes across papers.

The public podcast is **AI Post Transformers**, distributed via
Spotify and Apple Podcasts. Episodes are hosted on Cloudflare R2 at
https://podcast.do-not-panic.com.

## Editorial Bias

Memory, storage, bandwidth, and interconnect are increasingly
first-class constraints in modern AI systems — especially for
LLM inference, long-context serving, KV-cache-heavy workloads,
and agentic systems. The podcast is editorially biased toward
papers that treat these constraints as primary research targets
rather than incidental implementation details.

This bias is grounded in a growing body of work showing that
compute alone no longer dictates system performance. Data
movement — between HBM and DRAM, across CXL fabrics, through
storage hierarchies — is often the true bottleneck in
production AI deployments.

### Foundational Papers

- [AI and Memory Wall](https://arxiv.org/abs/2403.14123) —
  argues that memory bandwidth, not compute, is the primary
  bottleneck in modern AI due to two decades of asymmetric
  hardware scaling.

- [Challenges and Research Directions for LLM Inference
  Hardware](https://arxiv.org/abs/2601.05047) — identifies
  memory and interconnect as the dominant LLM inference
  bottlenecks and proposes architectural solutions including
  high-bandwidth flash, processing-near-memory, and
  low-latency interconnects.

- [A Systematic Characterization of LLM Inference on
  GPUs](https://arxiv.org/abs/2512.01644) — establishes a
  framework for analyzing LLM inference through performance
  heterogeneity, hardware causes, scaling behaviors, and
  emerging execution paradigms.

- [Accelerating LLM Inference via Dynamic KV Cache Placement
  in Heterogeneous Memory](https://arxiv.org/abs/2508.13231) —
  formalizes optimal KV cache distribution across HBM and
  off-package memory to maximize inference throughput under
  capacity constraints.

- [DualPath: Breaking the Storage Bandwidth Bottleneck in
  Agentic LLM Inference](https://arxiv.org/abs/2602.21548) —
  introduces dual-path KV-cache loading through both prefill
  and decoding engines, achieving up to 1.87x throughput
  improvement on agentic workloads.

The paper queue algorithm (see below) reflects this bias
through a dedicated Memory/Storage scoring lens alongside a
general Public AI Interest lens. Papers are ranked under both
lenses independently — a paper can score high on one, both, or
neither.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set API keys
export OPENAI_API_KEY="..."
export ELEVENLABS_API_KEY="..."

# Generate a podcast from a paper
./gen-podcast.py https://arxiv.org/pdf/2511.16664
```

## Usage

```bash
# Generate from PDF URLs
./gen-podcast.py URL [URL ...]

# With a focus topic
./gen-podcast.py URL --goal "focus on architecture changes"

# Goal and description from files (recommended for complex episodes)
./gen-podcast.py URL URL \
  --goal-file inputs/goal.txt \
  --description-file inputs/description.txt

# Read URLs from a file
./gen-podcast.py --input-papers papers.txt

# Publish latest episode to R2 + regenerate RSS feed
./gen-podcast.py --publish

# List episodes
./gen-podcast.py --list-podcasts                # public (from RSS)
./gen-podcast.py --list-generated-podcasts      # local
./gen-podcast.py --list-podcasts --top 5        # latest 5
```

## How It Works

Each episode goes through a multi-pass LLM pipeline followed by
per-segment TTS synthesis. A typical single-paper episode makes
**11 LLM calls** and synthesizes **~60 TTS segments**, taking
roughly **20–25 minutes** end-to-end.

### Pipeline Overview

```
PDF(s) → Extract text
       → Pass 0:   Topic classification + author extraction     [1 LLM call, ~30s]
       → Pass 1:   Background research (new topics only)        [1 LLM call, ~60s]
       → Pass 2:   Concept analysis + critical questions         [1 LLM call, ~60s]
       → Pass 2.5a: Local adversarial search (452+ prior eps)   [1 LLM call, ~30s]
       → Pass 2.5b: External adversarial search (Scholar)       [1 LLM call, ~45s]
       → Pass 3:   Episode bible                                [1 LLM call, ~45s]
       → Pass 3:   Script generation (4 parts)                  [4 LLM calls, ~6min]
       → Editorial pass (repetition removal)                    [1 LLM call, ~90s]
       → TTS synthesis (per-segment, dual voice)                [~60 segments, ~4min]
       → ffmpeg concatenation → MP3
       → Summary generation                                     [1 LLM call, ~30s]
       → Cover image generation (DALL-E)
       → Transcript (.txt) + Subtitles (.srt) + Metadata (.json)
```

### Pass Details

**Pass 0 — Topic Classification + Author Extraction**
Identifies 3–5 key topics in the paper and flags which are new vs
returning from prior episodes. Also extracts the full author list,
paper title, and institutions. Authors are cross-referenced against
the papers database to detect shared authors with prior covered
papers (e.g., "Ruisi Cai also authored Flextron").

**Pass 1 — Background Research**
For topics never covered before, generates plain-language
explanations, comparisons to familiar approaches, industry adoption
context, and 2–4 key reference papers per topic. Skipped entirely
if all topics have been covered in prior episodes.

**Pass 2 — Concept Analysis + Critical Questions**
Deep analysis of the paper's core contributions. Outputs structured
critical questions, blind spot analysis (what the paper ignores),
and a scope-vs-claims gap assessment comparing what the paper
claims vs what it actually tested experimentally.

**Pass 2.5a — Local Adversarial Search**
Searches the full episode catalog (452+ episodes: 3 new + 449
legacy Anchor episodes) for connections to the current paper.
Uses cheap keyword scoring to filter to the top 15 candidates,
then sends those to the LLM for connection analysis. Connected
episodes are added as sources and referenced naturally in the
script ("as we discussed in our episode on X").

**Pass 2.5b — External Adversarial Search**
Generates 3–5 short academic search queries from the paper's
claims, scrapes Google Scholar for results, then synthesizes
findings that complicate or challenge the paper. These are
presented naturally in the conversation, never as "our search
found."

**Pass 3 — Script Generation**
First generates an "episode bible" — a content allocation plan
that prevents repetition across parts. Then generates 4 script
parts in sequence, each receiving the bible, all prior questions
asked (to avoid re-asking), and cumulative context. An adaptive
complexity score (topics + questions + refs + paper length)
determines whether to use 2, 3, or 4 parts.

Each part targets approximately `max_words / 4` words (hard max
130% of target). Speaker turns are 80–200 words each.

**Editorial Pass**
The full assembled transcript is reviewed by the LLM at low
temperature (0.3) to cut repetitions, redundant questions, and
filler. This is an editing task, not creative — fast and
deterministic.

**TTS Synthesis**
Each script segment is synthesized individually using ElevenLabs
with two distinct voices (Host A: Hal Turing, Host B: Dr. Ada
Shannon). Segments are concatenated with 1s silence gaps via
ffmpeg. A countdown intro is prepended.

**Summary + Image**
The summary is generated from the first 40 transcript segments.
It does not mention host names (for the Spotify description).
The cover image is a dark-themed infographic generated via
DALL-E, showing key stats and findings — no podcast name or host
names.

### Episode Dynamics

Configurable per-episode dynamics add natural variation:
- **Interrupts**: ~1 per episode, marked in script for potential
  ffmpeg crossfade
- **Disagreements**: Every Nth episode (default: every 2nd), hosts
  take opposing positions on a substantive point
- **Jokes**: Max 2 per episode, situational only — no generic
  puns or forced humor

### Shared Author Detection

Pass 0 extracts authors and cross-references them against the
papers database. When a shared author is found (e.g., the same
researcher authored both the current paper and a previously
covered paper), this is injected into the script prompts for
natural mention: "interestingly, [author] was also behind
[prior paper], showing continuity in this research line."

### LLM Backend

The pipeline supports three backends (configured in
`config.yaml` → `podcast.llm_backend`):
- `claude-cli`: Claude CLI subprocess (default, uses Max sub)
- `openai`: OpenAI API via Python SDK
- `anthropic`: Anthropic API via Python SDK

JSON responses are parsed with progressive repair: regex cleanup,
brace-matched extraction, trailing-comma removal, truncation
repair, and up to 2 LLM retries with stricter instructions.

## Episode Filenames

Episodes use unique filenames to avoid collisions:

```
{YYYY-MM-DD}-{slug}-{hash6}.mp3
```

The slug is derived from the episode title (first ~40 chars,
slugified). The hash is the first 6 hex chars of SHA-256 over
title + date + sorted URLs. Companion files (`.txt`, `.srt`,
`.json`, `.png`) share the same stem.

## Paper Queue

The paper queue ranks interesting papers for future episodes.
It fetches from arXiv, HuggingFace Daily Papers, Semantic
Scholar, and GitHub Issues (community submissions), then scores
each paper against the editorial lenses described above.

```bash
make queue                        # fetch + score + generate queue
make publish-site                 # upload queue + site to R2
```

The queue is published at
https://podcast.do-not-panic.com/queue.html with a companion
RSS feed at `queue.xml`. Each paper shows a score breakdown on
hover explaining how it ranked.

### Editorial Queue Pipeline

The queue uses a two-pass scoring architecture. Pass 1 is fast
embedding-based scoring that processes hundreds of papers in
seconds. Pass 2 sends a shortlist to an LLM for editorial
judgment. The pipeline is implemented across `editorial_scorer.py`,
`llm_reviewer.py`, `paper_record.py`, and `paper_queue.py`.

**Pass 1 — EditorialScorer** (`editorial_scorer.py`)

1. Convert each paper dict into a `PaperRecord` dataclass
2. Batch-embed all paper titles + abstracts using
   `sentence-transformers` (model: `all-MiniLM-L6-v2`)
3. Classify taxonomy: `scope_bucket` (foundation, systems,
   architecture, training, inference, eval, benchmark, hardware,
   application, survey), `domain_bucket` (llm, multimodal,
   vision, audio, robotics, bio, medical, graphics, pde,
   recommendation, other), and `paper_type` (theory, empirical,
   systems, benchmark, survey, application)
4. Apply editorial filters: flag narrow-domain papers (medical,
   bio, graphics, pde, recommendation, plus keyword matches)
5. Compute triple similarity against seed embedding profiles:
   `sim_public` (public interest seeds), `sim_memory` (memory/
   storage seeds), `sim_negative` (out-of-scope seeds)
6. Compute feature scores: `broad_relevance`, `momentum`,
   `teachability`, `novelty_score`, `evidence_score`,
   `direct_memory_relevance`, `systems_leverage`,
   `deployment_proximity`, `memory_adjacent_future_value`,
   `bandwidth_capacity`, `transferability_score`, `clarity`,
   `reproducibility`
7. Compute composite scores as weighted sums (weights from
   `weights.yaml`):
   - `public_interest_score = 0.30*broad_relevance +
     0.20*momentum + 0.20*teachability + 0.15*novelty +
     0.15*evidence`
   - `memory_score = 0.35*direct_memory_relevance +
     0.20*systems_leverage + 0.15*deployment_proximity +
     0.15*evidence + 0.15*memory_adjacent_future_value`
   - `quality_score = 0.40*evidence + 0.25*transferability +
     0.20*clarity + 0.15*reproducibility`
8. Apply penalties: fatigue (>0.75 similarity to covered topics),
   negative profile (>0.5 sim to negative seeds), narrow domain
   (if transferability < 0.5), podcasted (10% of original score)
9. Compute `bridge_score = min(public, memory)` and
   `max_axis_score = max(public, memory)`
10. Select shortlist: top papers by `max_axis + 0.10*bridge +
    0.20*quality + 0.10*novelty`, minimum `max_axis >= 0.15`,
    default 100 papers

**Pass 2 — LLMReviewer** (`llm_reviewer.py`)

Runs parallel LLM calls (default 4 workers) on each shortlisted
paper using a 7-question editorial rubric:

1. Would a broad AI audience care within 30-90 days?
2. Does this change how people build/train/serve/evaluate AI?
3. Is the memory/storage connection direct, adjacent, or absent?
4. Are claims supported on realistic workloads?
5. General method or narrow task paper?
6. Genuinely new or near-duplicate of recent episodes?
7. Good episode potential or just a benchmark result?

The LLM returns JSON with score adjustments (clamped to ±0.3),
badges, status, editorial notes, and an episode hook. Score
adjustments are applied to the first-pass composites and
bridge/max_axis are recomputed.

Valid statuses: `Cover now`, `Monitor`, `Deferred this cycle`,
`Out of scope`. Valid badges: `Public AI`, `Memory/Storage Core`,
`Memory/Storage Adjacent`, `Bridge`, `Systems`, `Theory`,
`Hardware`, `Training`, `Inference`, `Application`.

On LLM failure for any paper, it keeps first-pass scores and
defaults to `Monitor` status.

**Final Queue Partitioning** (`paper_queue.build_final_queue`)

Papers with `Cover now` status are partitioned into three buckets:

- **Bridge** — scores above 0.3 on both lenses, or has `Bridge`
  badge (target: 10 slots)
- **Public** — `public_interest > memory` (target: 10 slots)
- **Memory** — `memory >= public_interest` (target: 10 slots)

Remaining papers fill **Monitor** (top 20 by quality×teachability),
**Deferred**, and **Out of Scope** sections. A diversity cap
(default 3) limits very similar papers within each bucket.
Sparse buckets backfill from Monitor.

## Visualization Catalog

The `viz-sync` command links podcast episodes to interactive
visualizations published at external sites. When a visualization
references the same arXiv papers as an episode, the episode
description is updated with a link to the visualization.

```bash
make viz-sync                     # fetch catalogs + update episodes
```

Catalog sources are configured in `config.yaml`:

```yaml
visualization:
  cache_dir: viz_cache
  sources:
    - name: do-not-panic
      url: https://www.do-not-panic.com/viz/catalog.json
```

Catalogs are cached locally so unchanged files are skipped on
subsequent runs. Multiple sources are fetched in parallel.

### Catalog JSON Format

Anyone can publish a visualization catalog. The required format:

```json
{
  "base_url": "https://example.com/visualizations/",
  "updated": "2026-03-05",
  "visualizations": [
    {
      "id": 72,
      "title": "Interactive Deep Dive",
      "url": "viz/2026/03/04/page.html",
      "date": "2026-03-04",
      "papers": [
        {"id": "2404.19737", "type": "arxiv"}
      ]
    }
  ]
}
```

Required fields: `base_url`, `updated`, `visualizations[]`.
Each visualization requires `id`, `title`, `url`, `date`, and
`papers[]`. Each paper ref requires `id` and `type` (currently
only `"arxiv"` is supported). Optional fields: `description`,
`image`.

The `url` field is relative to the site root (scheme+host from
`base_url`). Absolute URLs are also accepted.

## Legacy: Paper Digest

The original use case was a daily paper digest pipeline:

```bash
./gen-podcast.py digest           # fetch + score + output digest
./gen-podcast.py podcast          # generate from top DB paper
```

This fetches papers from arXiv, scores them against a research
interest profile (KV cache, memory-efficient training, optimizer
research, etc.), enriches with Semantic Scholar citations and
HuggingFace trending signal, and outputs a ranked digest.

## Configuration

Settings are split across three config files and a set of seed
embedding profiles.

**`config.yaml`** — Main configuration. Contains arXiv categories,
legacy interest keywords and scoring weights, embedding model
selection, Semantic Scholar API settings, ElevenLabs voices and
quality, image generation (DALL-E model, size, style), podcast
parameters (word counts, durations, host personalities, dynamics,
LLM backend/model), Spotify/RSS distribution, and the
`editorial` section that enables the two-pass pipeline and
points to the other config files.

**`weights.yaml`** — Scoring weights for the editorial queue.
Defines the weighted-sum coefficients for `public_interest`,
`memory`, and `quality` composites, boost values (`hf_trending`,
`github_submission`, `citation_velocity_max`, `bridge_bonus`),
penalty caps (`fatigue_max`, `negative_profile_max`,
`narrow_domain`), shortlist parameters (`size`, `min_max_axis`),
and final queue slot allocation (`bridge`, `public`, `memory`,
`diversity_cap`).

**`editorial_lenses.yaml`** — Editorial taxonomy definitions.
Declares the two lenses (Public AI Interest, Memory/Storage
First-Class AI) with their signal lists, taxonomy buckets
(scope, domain, paper types), valid statuses and badges, the
narrow domain penalty list, and arXiv category-to-bucket
mappings.

**`seed_sets/*.yaml`** — Embedding seed profiles used for
computing similarity scores:
- `public_interest_positive.yaml` — seeds for broad AI interest
- `memory_storage_positive.yaml` — seeds for memory/storage
  relevance
- `negative_out_of_scope.yaml` — seeds for out-of-scope detection

## Data Model

`PaperRecord` (`paper_record.py`) is the canonical dataclass
flowing through the editorial pipeline. Fields are grouped as:

- **Identity**: `arxiv_id`, `title`, `abstract`, `authors`,
  `published_at`, `categories`, `url`, `code_url`
- **Source signals**: `github_submission_flag`,
  `hf_trending_flag`, `citation_count`,
  `influential_citation_count`
- **Taxonomy**: `scope_bucket`, `domain_bucket`, `paper_type`,
  `narrow_domain_flag`
- **Similarities**: `sim_public`, `sim_memory`, `sim_negative`
- **Feature scores**: `broad_relevance`, `momentum`,
  `teachability`, `novelty_score`, `evidence_score`,
  `direct_memory_relevance`, `systems_leverage`,
  `deployment_proximity`, `memory_adjacent_future_value`,
  `bandwidth_capacity`, `transferability_score`, `clarity`,
  `reproducibility`
- **Composites**: `public_interest_score`, `memory_score`,
  `quality_score`, `bridge_score`, `max_axis_score`
- **Penalties**: `fatigue_penalty`, `negative_profile_penalty`
- **LLM review**: `badges`, `status`, `why_now`,
  `why_not_higher`, `downgrade_reasons`,
  `what_would_raise_priority`, `one_sentence_episode_hook`

`PaperRecord.from_paper_dict()` normalizes the varying field
names across arXiv, HF Daily, Semantic Scholar, and GitHub
Issues sources. `to_dict()` serializes all fields except the
embedding vector.

## Database

SQLite database (`papers.db`) with five tables:

- **papers** — cached paper metadata, scores, and fetch dates
- **podcasts** — episode records (title, date, audio file,
  source URLs, description, image)
- **podcast_papers** — junction table linking podcasts to their
  source arXiv papers
- **covered_topics** — tracks topics covered across episodes
  for novelty/fatigue scoring
- **fun_facts** — pool of fun facts for episode intros (unused/
  used status tracking)

## Directory Structure

```
paper-feed/
  gen-podcast.py         # CLI entry point
  Makefile               # queue, publish, publish-site targets
  podcast.py             # podcast generation workflow
  elevenlabs_client.py   # multi-pass script + TTS pipeline
  paper_queue.py         # paper queue pipeline + HTML/RSS
  editorial_scorer.py    # first-pass embedding scorer
  llm_reviewer.py        # second-pass LLM editorial reviewer
  paper_record.py        # PaperRecord dataclass
  llm_backend.py         # LLM backend abstraction (3 backends)
  interests.py           # legacy interest profile scoring
  rss.py                 # RSS 2.0 feed generator
  pdf_utils.py           # PDF download and text extraction
  image_gen.py           # episode cover image generation
  fun_facts.py           # fun fact collector for episode intros
  viz_catalog.py         # visualization catalog sync
  db.py                  # SQLite schema and queries
  r2_upload.py           # Cloudflare R2 upload
  config.yaml            # main settings
  weights.yaml           # scoring weights and queue allocation
  editorial_lenses.yaml  # taxonomy, statuses, badges
  seed_sets/
    public_interest_positive.yaml
    memory_storage_positive.yaml
    negative_out_of_scope.yaml
  sources/
    arxiv_source.py      # arXiv API fetcher
    hf_daily.py          # HuggingFace Daily Papers scraper
    semantic.py          # Semantic Scholar enrichment
  tests/
    conftest.py          # shared fixtures (config, scorer)
    test_editorial_scorer.py
    test_llm_reviewer.py
    regression_cases.yaml
  drafts/                # generated episodes (gitignored)
  public/                # local mirror of R2 episodes (gitignored)
  inputs/                # saved generation inputs (gitignored)
  podcasts/              # feed.xml + anchor_feed.xml
```

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

Tests cover the editorial scoring pipeline:

- `test_editorial_scorer.py` — PaperRecord construction,
  taxonomy classification (scope/domain/paper_type), relative
  score ordering across regression cases, editorial filter
  behavior (narrow domain flags), and shortlist threshold
  enforcement. Uses a shared `EditorialScorer` fixture loaded
  once per session.

- `test_llm_reviewer.py` — score adjustment application
  (positive, negative, clamped-to-zero), badge and status
  assignment, composite recomputation after adjustments, and
  graceful failure handling (defaults to Monitor on LLM error).
  All LLM calls are mocked.

- `tests/regression_cases.yaml` — curated papers representing
  edge cases: generic optimizer theory, narrow image
  restoration, broad LLM inference cache, direct memory papers,
  and broad architecture with weak memory signal. Tests assert
  relative ordering and taxonomy correctness against these cases.

## Requirements

- Python 3.11+
- CPU only (no GPU required)
- API keys: OpenAI, ElevenLabs
- Optional: Cloudflare R2 credentials (for publishing)

Key dependencies (`requirements.txt`):

- `sentence-transformers` — embedding model for seed profile
  similarity scoring
- `openai` — OpenAI API SDK (LLM backend + image generation)
- `anthropic` — Anthropic API SDK (LLM backend)
- `arxiv` — arXiv API client for paper fetching
- `feedparser` — RSS feed parsing (Anchor/Spotify feed)
- `beautifulsoup4` — HTML scraping (HuggingFace Daily Papers)
- `pypdf` — PDF text extraction
- `PyYAML` — YAML config loading
- `boto3` — Cloudflare R2 uploads (S3-compatible)
- `pytest` — test framework

## License

MIT. See [LICENSE](LICENSE) for details. Contributions require
DCO sign-off. See [CONTRIBUTING](CONTRIBUTING) for details.
