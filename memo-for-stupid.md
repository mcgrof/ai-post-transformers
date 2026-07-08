# memo-for-stupid: make a podcast from a custom transcript

How to turn **your own script/transcript** into a published episode using
the admin/Drafts console. No CLI needed. This is the same path the
"SOUL.md or Severance" episode used.

---

## TL;DR (the whole thing in 5 lines)

1. Open the **admin Submit page** (your CF-Access-protected admin console).
2. **Paper URLs**: paste any placeholder URL (it's a required field; the
   content is ignored when you give instructions).
3. **Special Instructions**: paste your **entire script/transcript**.
4. Click **Submit**. The worker picks it up (~2 min) and generates a draft
   (~10–40 min).
5. Go to the **Drafts** page → listen/read the draft → **Approve** → it
   publishes to the public feed.

That's it. The rest of this memo is the *why* and the *gotchas*.

---

## How it actually works (so you're not flying blind)

- Whatever you type in **Special Instructions** is handed to the generator
  as the `--goal` argument (`scripts/run_generation_worker.py`), which gets
  embedded directly into the script-writing LLM prompt
  (`elevenlabs_client.py`, the `host_block`).
- The system then **writes and performs a two-host conversation**:
  - **Host A — Hal Turing** (warm, curious, asks the questions)
  - **Host B — Dr. Ada Shannon** (sharp, direct, the expert)
  - With banter, an interruption or two, and citation formatting.
- Output is a full **draft**: `MP3` + transcript `.txt` + subtitles `.srt`
  + cover `.png` + metadata `.json`, all under the same filename stem.

### IMPORTANT: it *steers*, it does not transcribe verbatim
Your instructions **guide** an LLM that writes the final spoken script. It
is **not** a "read my text out loud word-for-word" machine by default. If
you paste a transcript, the LLM will follow it closely but may rephrase,
trim, or expand. **Always check the generated transcript before approving.**

---

## Step-by-step in the console

### 1. Submit
On the **Submit** page:

| Field | What to put |
|---|---|
| **Paper URLs** | A placeholder URL, e.g. `https://internal.do-not-panic.com/my-custom-episode`. Required field; ignored as a source when instructions are present. |
| **Special Instructions** | Your **full script / transcript / outline** (can be many thousands of characters — the SOUL episode was ~24k chars). |
| **Visibility** | `Public` (goes to the real feed) or `Private` (owner-only). |

Click **Submit**.

### 2. Wait for generation
- The background worker claims the submission on its ~2-minute timer and
  runs the generator. A full episode takes roughly **10–40 minutes** (script
  passes + ElevenLabs TTS + cover art).
- If nothing appears after a while, the submission is sitting in `submitted`
  / `generation_running`. (This is the pipeline we just hardened — it should
  pick up reliably now.)

### 3. Review in the Drafts console
- The finished episode shows up as a card on the **Drafts** page with its
  audio, title, and description.
- **Play it. Read the transcript.** Confirm it actually says what you want
  (especially important for a custom transcript — see the verbatim note).

### 4. Approve → publish
- Hit **Approve** on the draft. That advances it to publish; the publish
  worker uploads the audio, builds the cover/viz/site, and adds it to the
  RSS feed. (Publishing is slow — each one runs a visualization step — so
  give it up to an hour to actually appear live.)

---

## Getting as close to VERBATIM as possible

If your transcript is the literal dialogue you want spoken, put a directive
**at the very top** of Special Instructions, then the script. Example:

```
THIS IS A COMPLETE, FINAL SCRIPT. Perform it AS WRITTEN.
Do NOT add, remove, paraphrase, summarize, or invent any content.
Do NOT add intros/outros that aren't below. Keep every line.
Two hosts: A = Hal Turing, B = Dr. Ada Shannon. Map the speakers below
onto A and B in order.

A: <first line, exactly>
B: <next line, exactly>
A: <...>
...
```

Reality check: it's still an LLM, so it can drift. Listen to the draft and,
if it strayed, tighten the directive ("word-for-word, this is legal copy")
and regenerate. For truly word-perfect audio you'd need a verbatim
TTS-only path, which this pipeline doesn't expose today.

---

## Gotchas / FAQ

- **"It generated a normal paper episode, not my script."** Your text went
  in the wrong field. It must be in **Special Instructions**, not Paper URLs.
- **"Two hosts but I wanted one / different names."** Defaults are Hal &
  Ada. Host names/personalities come from config (`hosts.a` / `hosts.b`);
  for a one-off, just instruct the script accordingly in Special
  Instructions, but the engine is built around an A/B two-host format.
- **"Nothing showed up."** Check the submission status; generation is slow
  and serialized (one at a time). Be patient before resubmitting — a dupe
  just doubles the work.
- **"Don't publish it yet."** Use **Private** visibility, or just don't hit
  Approve. A draft sits in the Drafts console until you act on it.
- **Placeholder URL note:** a real PDF URL would also be fetched and blended
  in; for a pure custom episode, the placeholder + instructions is the
  pattern (this is exactly what the SOUL episode did).

---

## Power-user alternative (CLI, FYI only)

The console route above is the recommended one. Under the hood it's just:

```bash
source ~/.enhance-bash
.venv/bin/python gen-podcast.py \
  https://internal.do-not-panic.com/my-custom-episode \
  --goal-file my_script.txt        # your transcript as the goal
# then review the draft and:  .venv/bin/python gen-podcast.py publish
```

`--goal-file` keeps a saved record of the input; `--goal "..."` works for
short inline text.
