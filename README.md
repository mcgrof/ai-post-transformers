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

## Directory Structure

```
paper-feed/
  gen-podcast.py         # CLI entry point
  Makefile               # queue, publish, publish-site targets
  config.yaml            # interests, models, podcast settings
  podcast.py             # podcast generation workflow
  paper_queue.py         # paper queue pipeline + HTML/RSS
  elevenlabs_client.py   # multi-pass script + TTS pipeline
  rss.py                 # RSS 2.0 feed generator
  pdf_utils.py           # PDF download and text extraction
  image_gen.py           # episode cover image generation
  fun_facts.py           # fun fact collector for episode intros
  interests.py           # interest profile embedding + scoring
  db.py                  # SQLite schema and queries
  r2_upload.py           # Cloudflare R2 upload
  sources/
    arxiv_source.py      # arXiv API fetcher
    hf_daily.py          # HuggingFace Daily Papers scraper
    semantic.py          # Semantic Scholar enrichment
  drafts/                # generated episodes (gitignored)
  public/                # local mirror of R2 episodes (gitignored)
  inputs/                # saved generation inputs (gitignored)
  podcasts/              # feed.xml + anchor_feed.xml
```

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

All settings live in `config.yaml`: arXiv categories, interest
keywords, scoring weights, OpenAI model selection, ElevenLabs
voices, image generation, and Spotify/RSS distribution settings.

## Requirements

- Python 3.11+
- CPU only (no GPU required)
- API keys: OpenAI, ElevenLabs
- Optional: Cloudflare R2 credentials (for publishing)

## License

MIT. See [LICENSE](LICENSE) for details. Contributions require
DCO sign-off. See [CONTRIBUTING](CONTRIBUTING) for details.
