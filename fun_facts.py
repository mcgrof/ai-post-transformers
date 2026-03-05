"""Fun facts collector for podcast intros and mid-episode color.

Categories:
  - intro_joke: Opening banter between Hal and Ada (the "weird AI moment" bit)
  - ai_news: Current AI news tidbits for mid-episode color
  - fun_fact: General nerdy/interesting facts related to AI/tech
  - meta: Self-referential AI humor
"""
import sys

from db import get_connection, init_db, add_fun_facts, get_unused_fun_facts, get_fun_facts_stats, prune_used_fun_facts
from llm_backend import get_llm_backend, llm_call


def collect_fun_facts(config, count=15):
    """Collect fresh fun facts from current AI news and general knowledge.

    Generates a batch of facts across categories:
    - intro_joke: punchy opening jokes for the Hal+Ada intro banter
    - ai_news: current AI industry news that nerds would know
    - fun_fact: interesting tidbits about AI/ML/tech history
    - meta: self-referential AI humor
    """
    backend = get_llm_backend(config)
    model = config.get("podcast", {}).get("analysis_model", "sonnet")

    # Check what we already have
    conn = get_connection()
    init_db(conn)
    stats = get_fun_facts_stats(conn)
    existing_unused = get_unused_fun_facts(conn, limit=50)
    existing_texts = [f["fact"] for f in existing_unused]
    conn.close()

    existing_sample = "\n".join(existing_texts[:10]) if existing_texts else "(none yet)"

    prompt = f"""Generate {count} fun facts, jokes, and news tidbits for an AI podcast called
"AI Post Transformers" hosted by Hal Turing and Dr. Ada Shannon.

CATEGORIES (generate a mix):

1. "intro_joke" (4-5 items): Punchy one-liner jokes or observations for the opening banter.
   These follow the format where Hal says something like "can we talk about how weird it is
   that we're both AI voices talking about AI?" and then connects to a REAL current event or
   well-known fact. Examples of good connections:
   - Government pushing AI companies on military use
   - Major model releases (GPT-5, Claude 4, Gemini Ultra)
   - AI companies' funding rounds or valuations
   - Funny AI failures or hallucinations in the news
   - AI regulation developments (EU AI Act, etc.)
   DO NOT use the podcast's own content as a joke source. Use REAL world events.

2. "ai_news" (4-5 items): Current AI news that most AI nerds would know about.
   Brief, factual, interesting. Things like "Did you know OpenAI just hit 500M weekly users?"
   or "NVIDIA's latest earnings beat expectations by 40% — AI infrastructure is still booming."

3. "fun_fact" (3-4 items): Interesting historical or technical facts about AI/ML.
   Like "The term 'artificial intelligence' was coined at the Dartmouth Conference in 1956"
   or "GPT-3 has 175 billion parameters but a fruit fly brain has about 100,000 neurons."

4. "meta" (2-3 items): Self-referential AI humor that Hal and Ada could use naturally.
   Like "You know what's funny? I technically don't exist between episodes."

EXISTING FACTS (don't duplicate these):
{existing_sample}

Output as JSON array:
[{{"fact": "...", "category": "intro_joke|ai_news|fun_fact|meta", "source": "optional source/context"}}]
Only output the JSON array."""

    facts = llm_call(backend, model, prompt, temperature=0.8)

    # Save to DB
    conn = get_connection()
    init_db(conn)
    add_fun_facts(conn, facts)
    new_stats = get_fun_facts_stats(conn)
    conn.close()

    print(f"[FunFacts] Collected {len(facts)} new facts "
          f"(total: {new_stats['unused']} unused, {new_stats['used']} used)",
          file=sys.stderr)
    return facts


def get_podcast_context(count=8):
    """Get a batch of unused fun facts to feed into podcast script generation.

    Returns a dict with:
    - intro_jokes: list of intro joke options
    - color_facts: list of facts/news for mid-episode color
    """
    conn = get_connection()
    init_db(conn)

    intro_jokes = get_unused_fun_facts(conn, limit=3, category="intro_joke")
    ai_news = get_unused_fun_facts(conn, limit=3, category="ai_news")
    fun_facts = get_unused_fun_facts(conn, limit=2, category="fun_fact")
    meta = get_unused_fun_facts(conn, limit=2, category="meta")

    conn.close()

    return {
        "intro_jokes": intro_jokes,
        "color_facts": ai_news + fun_facts + meta,
        "all": intro_jokes + ai_news + fun_facts + meta,
    }


if __name__ == "__main__":
    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["collect", "stats", "list", "prune"])
    parser.add_argument("--count", type=int, default=15)
    args = parser.parse_args()

    if args.action == "collect":
        facts = collect_fun_facts(config, count=args.count)
        # Auto-prune old used facts
        conn = get_connection()
        init_db(conn)
        pruned = prune_used_fun_facts(conn, keep_days=7)
        if pruned:
            print(f"[FunFacts] Pruned {pruned} used facts older than 7 days", file=sys.stderr)
        conn.close()
    elif args.action == "stats":
        conn = get_connection()
        init_db(conn)
        stats = get_fun_facts_stats(conn)
        print(f"Fun facts: {stats['unused']} unused, {stats['used']} used, {stats['total']} total")
        conn.close()
    elif args.action == "prune":
        conn = get_connection()
        init_db(conn)
        pruned = prune_used_fun_facts(conn, keep_days=args.count)
        print(f"Pruned {pruned} used facts older than {args.count} days")
        conn.close()
    elif args.action == "list":
        conn = get_connection()
        init_db(conn)
        facts = get_unused_fun_facts(conn, limit=20)
        for f in facts:
            print(f"[{f['category']}] {f['fact']}")
        conn.close()
