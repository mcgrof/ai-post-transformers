# paper-feed

Generate podcast episodes from AI research papers. Pass one or more
PDF URLs and get a two-host conversational podcast with transcripts,
subtitles, and episode cover art. Multiple PDFs produce a combined
episode that identifies common themes across papers.

The public podcast is **AI Post Transformers**, distributed via
Spotify and Apple Podcasts. Episodes are hosted on Cloudflare R2 at
https://podcast.do-not-panic.com.

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

1. Download PDFs and extract text via pypdf
2. Generate a conversation script using OpenAI (multi-pass pipeline
   with topic classification, source extraction, and script writing)
3. Synthesize audio with ElevenLabs TTS (two distinct voices)
4. Generate episode summary, SRT subtitles, and cover image
5. Store metadata in SQLite, output files to `drafts/YYYY/MM/`
6. On publish: upload to R2, copy to `public/YYYY/MM/`, regenerate
   RSS feed

## Directory Structure

```
paper-feed/
  gen-podcast.py         # CLI entry point
  config.yaml            # interests, models, podcast settings
  podcast.py             # podcast generation workflow
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
