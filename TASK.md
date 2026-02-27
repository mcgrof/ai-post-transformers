# gen-podcast: AI Research Paper Podcast Generator

Generate podcast episodes from PDF URLs, with legacy support for daily paper
digests. Multiple PDFs produce a combined episode that identifies and explains
common themes across papers.

## Usage

```
# Primary: generate podcast from PDF URL(s)
./gen-podcast.py URL [URL ...]                              # from URLs
./gen-podcast.py URL [URL ...] --goal "focus on X"          # with focus topic
./gen-podcast.py --input-papers papers.txt                  # from file of URLs
./gen-podcast.py --input-papers papers.txt --goal "focus"   # file + goal

# Legacy: paper digest pipeline
./gen-podcast.py digest                                     # daily paper digest

# Legacy: podcast from database paper
./gen-podcast.py podcast [--paper ARXIV_ID]                 # podcast from DB paper

# Listing episodes
./gen-podcast.py --list-podcasts                            # public episodes from Anchor RSS
./gen-podcast.py --list-generated-podcasts                  # locally generated episodes
./gen-podcast.py --list-podcasts --top 5                    # latest 5 public episodes

# Spotify distribution
./gen-podcast.py spotify-upload                             # regenerate RSS feed
```

### CLI Flags
- `--input-papers FILE` — file with one PDF URL per line (blank lines and `#` comments skipped)
- `--goal TEXT` — focus topic injected into podcast instructions
- `--paper ARXIV_ID` — arXiv paper ID (used with the `podcast` subcommand)
- `--list-podcasts` — list all public episodes from the Anchor RSS feed (marks `[local]` for tool-generated ones)
- `--list-generated-podcasts` — list locally generated podcast episodes
- `--top N` — limit listing to the latest N episodes (requires a listing flag)

No arguments prints help. `--help` shows all modes in one view.

## Requirements

### Data Sources
1. **arXiv API** - fetch papers from last 24h in categories: cs.LG, cs.AI, stat.ML, cs.CL, cs.CV, cs.AR
2. **HuggingFace Daily Papers** - scrape/parse the daily papers page for trending signal
3. **Semantic Scholar API** - enrich with citation counts and fields of study
4. **PDF URLs** - download and extract text from any PDF URL for podcast generation

### Research Focus
Core interest areas for scoring and filtering:
- KV cache scaling laws and compression
- GPU memory wall and memory-efficient training
- Memory and storage as first-class citizens in AI model architecture
- Activation checkpointing, offloading, and storage hierarchy
- Paged attention and memory-augmented architectures
- Optimizer research (Adam variants, learning rate scheduling, second-order methods)
- Transformer architecture innovations (MoE, sparse attention)
- Open source AI tooling

### Scoring Pipeline
1. Embed paper title+abstract using sentence-transformers (all-MiniLM-L6-v2 or bge-small-en-v1.5)
2. Score against pre-embedded interest profile vectors
3. Boost papers that appear on HF Daily Papers
4. Boost papers with high citation velocity (from Semantic Scholar)
5. Keyword boosting for specific terms (KV cache, memory wall, GPU memory, etc.)
6. Penalize papers already covered in podcast episodes (90% score reduction)

### Output
- Generate a daily digest with top 15-20 papers
- For each paper: title, authors, URL, relevance score, 1-line why it matches interests
- Output as both:
  - A markdown file in `output/YYYY-MM-DD.md`
  - A WhatsApp-friendly text message (no markdown tables, use bullet lists)
  - The message should be sent via stdout so the caller can pipe it

### Podcast Generation
- **URL-based (primary):** Pass PDF URLs directly to generate episodes
  - Downloads PDFs and extracts text locally via pypdf
  - Single URL: standard two-host discussion of the paper
  - Multiple URLs: hosts identify common themes, then discuss each paper's contribution
  - Theme finding handled via ElevenLabs LLM prompt instructions (no local LLM needed)
- **DB-based (legacy):** Generate from top-scoring papers in the database
  - Two voices: host ("mcgrof uno") and guest ("Female AI") in conversation mode
  - Track which papers have been covered to avoid duplicates
- Text truncated to 100K chars for API safety (per-paper limit of 100K/N for multi-paper)

#### Spotify Distribution (RSS Feed)
- After each episode is created, an RSS 2.0 feed is auto-regenerated at `podcasts/feed.xml`
- Feed includes iTunes namespace tags for Spotify/Apple Podcasts compatibility
- `spotify-upload` subcommand manually regenerates the feed without creating a new episode
- Configure `spotify.audio_base_url` in `config.yaml` to set the public URL prefix for audio files
- Submit the hosted feed URL to Spotify for Podcasters to enable distribution

### Technical Constraints
- Python 3.11+
- Must run on CPU (no GPU required)
- SQLite for paper cache/dedup and podcast tracking
- Dependencies: requests, feedparser, sentence-transformers, sqlite3, pypdf, PyYAML
- Single entry point: `python gen-podcast.py`
- Config in `config.yaml` for interests, categories, ElevenLabs settings, etc.

### File Structure
```
paper-feed/
  gen-podcast.py       # main entry point with CLI
  config.yaml          # interests, categories, boost weights, podcast config
  interests.py         # interest profile embedding + scoring
  elevenlabs_client.py # ElevenLabs API client for podcast generation
  podcast.py           # podcast workflow and listing
  rss.py               # RSS 2.0 feed generator for Spotify distribution
  pdf_utils.py         # PDF download and text extraction
  sources/
    arxiv_source.py    # arXiv API fetcher
    hf_daily.py        # HuggingFace Daily Papers scraper
    semantic.py        # Semantic Scholar enrichment
  db.py                # SQLite cache/dedup + podcast tracking
  output/              # daily digests
  podcasts/            # generated podcast audio files
  requirements.txt
```

### DO NOT
- Don't build a web UI
- Don't build a feedback loop yet
- Don't over-engineer - this is MVP
- Don't use LangChain or heavy frameworks
