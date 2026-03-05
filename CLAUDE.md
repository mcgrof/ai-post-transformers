# paper-feed: Claude AI Assistant Preferences

## Project Overview

paper-feed generates AI research podcast episodes from PDF papers.
The public podcast is **AI Post Transformers**, hosted at
https://podcast.do-not-panic.com with episodes stored in a
Cloudflare R2 bucket. The RSS feed is at `podcasts/feed.xml`
(uploaded to R2 after each publish).

Legacy functionality includes a daily paper digest pipeline that
scores arXiv papers against a research interest profile.

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

### LLM backend selection

The pipeline routes all LLM calls through a configurable backend
defined in `llm_backend.py`. Three backends are supported:

```yaml
podcast:
  llm_backend: claude-cli   # "claude-cli", "openai", or "anthropic"
  llm_model: sonnet          # backend-specific model name
  analysis_model: sonnet
```

**claude-cli** (default): Calls the `claude` CLI as a subprocess.
Uses the Max subscription at no extra API cost. Temperature is not
controllable via CLI. Model names are Claude CLI model names
(e.g., `sonnet`, `opus`, `haiku`).

**openai**: Uses the OpenAI Python SDK. Requires `OPENAI_API_KEY`.
Model names are OpenAI model names (e.g., `gpt-4.1-mini`).

**anthropic**: Uses the Anthropic Python SDK. Requires
`ANTHROPIC_API_KEY`. Model names are Anthropic API model IDs
(e.g., `claude-sonnet-4-20250514`).

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

## Cross-Agent Access

To avoid other agents missing these guidelines, ensure every agent
entrypoint symlinks back to this document. For Codex runs,
`CODEX.md` must always be a symlink to `CLAUDE.md`.
