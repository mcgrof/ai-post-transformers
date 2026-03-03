"""ElevenLabs TTS client for podcast generation using regular text-to-speech API.

Pipeline:
  Pass 0: Topic classification — identify key topics, check which are new to the podcast
  Pass 1: Background research — for new topics, find surveys/comparisons/foundational refs
  Pass 2: Concept analysis — identify what needs explaining for the audience
  Pass 3: Script generation — produce the conversation with all context
"""
import os
import re
import sys
import time
import requests
import json

BASE_URL = "https://api.elevenlabs.io/v1"


def get_api_key():
    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Set ELEVEN_API_KEY or ELEVENLABS_API_KEY")
    return key


def tts_segment(text, voice_id, output_path):
    """Generate speech for a single segment."""
    key = get_api_key()
    resp = requests.post(
        f"{BASE_URL}/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        },
        stream=True
    )
    if resp.status_code != 200:
        raise RuntimeError(f"TTS failed ({resp.status_code}): {resp.text[:200]}")
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=4096):
            f.write(chunk)
    return output_path


def _get_openai_client():
    """Get an OpenAI client with API key from env or codex auth."""
    import openai
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            with open(os.path.expanduser("~/.codex/auth.json")) as f:
                api_key = json.load(f).get("OPENAI_API_KEY")
        except Exception:
            pass
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY for script generation")
    return openai.OpenAI(api_key=api_key)


def _llm_json(client, model, prompt, temperature=0.4, max_tokens=16000):
    """Call LLM and parse JSON response."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    result = resp.choices[0].message.content.strip()
    result = re.sub(r"^```json\n?|```$", "", result, flags=re.MULTILINE).strip()
    try:
        return json.loads(result)
    except json.JSONDecodeError as orig_err:
        print(f"[Podcast] Warning: JSON parse error, attempting repair...", file=sys.stderr)
        fixed = result
        # Remove trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        # Fix unescaped quotes inside strings (common GPT issue)
        # Replace unescaped newlines in strings
        fixed = fixed.replace('\n', '\\n')
        # Try as-is
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        # Try to extract just the JSON array (script parts return arrays)
        array_match = re.search(r'\[.*\]', fixed, re.DOTALL)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        # Try adding closing brackets for truncated output
        for suffix in ['"}]', '"]', ']', '}}', '}']:
            try:
                return json.loads(fixed + suffix)
            except json.JSONDecodeError:
                continue
        # Last resort: retry the LLM call once
        print(f"[Podcast] Warning: JSON repair failed, retrying LLM call...", file=sys.stderr)
        resp2 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt + "\n\nIMPORTANT: Output valid JSON only. No markdown, no trailing text."}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result2 = resp2.choices[0].message.content.strip()
        result2 = re.sub(r"^```json\n?|```$", "", result2, flags=re.MULTILINE).strip()
        result2 = re.sub(r',\s*([}\]])', r'\1', result2)
        return json.loads(result2)


# ---------------------------------------------------------------------------
# Pass 0: Topic Classification
# ---------------------------------------------------------------------------

def _topic_classification_pass(text, covered_topics, config):
    """Identify key topics in the paper and flag which are new to the podcast."""
    client = _get_openai_client()
    model = config.get("podcast", {}).get("analysis_model", "gpt-4o")

    covered_list = ", ".join(sorted(covered_topics)) if covered_topics else "(none yet)"

    prompt = f"""You are classifying topics for an AI research podcast.

Given this paper, identify the 3-5 KEY TOPICS it covers (e.g., "Gaussian Processes",
"Spectral Methods", "Kernel Approximation", "Bayesian Inference").

Topics already covered in previous podcast episodes: {covered_list}

For each topic, indicate whether it's NEW (never covered) or RETURNING (covered before).

Also extract the paper's AUTHORS (full names as they appear on the paper), TITLE, and
INSTITUTION(S)/LAB(S) (e.g., "NVIDIA", "Google DeepMind", "MIT").

Output as JSON:
{{
  "topics": [
    {{"name": "...", "is_new": true, "relevance": "one sentence on why this topic matters to the paper"}}
  ],
  "authors": ["First Last", "First Last", ...],
  "title": "Full paper title",
  "institutions": ["NVIDIA", "UT Austin", ...]
}}
Only output JSON.

Paper content:
{text[:6000]}"""

    return _llm_json(client, model, prompt)


def _find_shared_authors(authors, conn):
    """Check if any authors of the current paper also authored prior covered papers.

    Returns a list of dicts:
      [{"author": "Name", "prior_paper": "title", "prior_arxiv": "id"}, ...]
    """
    import json as _json
    if not authors:
        return []

    # Normalize author names to lowercase for matching
    current_authors = {a.lower().strip(): a for a in authors}
    shared = []

    rows = conn.execute("SELECT arxiv_id, title, authors FROM papers").fetchall()
    for row in rows:
        try:
            prior_authors = _json.loads(row["authors"]) if row["authors"] else []
        except (TypeError, _json.JSONDecodeError):
            continue
        for pa in prior_authors:
            pa_lower = pa.lower().strip() if isinstance(pa, str) else ""
            if pa_lower in current_authors:
                shared.append({
                    "author": current_authors[pa_lower],
                    "prior_paper": row["title"],
                    "prior_arxiv": row["arxiv_id"],
                })
    return shared


# ---------------------------------------------------------------------------
# Pass 1: Background Research for New Topics
# ---------------------------------------------------------------------------

def _background_research_pass(text, new_topics, config):
    """For new topics, find surveys, foundational papers, and comparisons."""
    client = _get_openai_client()
    model = config.get("podcast", {}).get("analysis_model", "gpt-4o")

    topics_str = "\n".join([f"- {t['name']}: {t['relevance']}" for t in new_topics])

    prompt = f"""You are a research librarian preparing background material for an AI podcast.

The podcast has NEVER covered these topics before. The audience is technically literate
(ML engineers, researchers) but may not know these areas well.

NEW TOPICS TO RESEARCH:
{topics_str}

For EACH new topic, provide:

1. WHAT IT IS: Plain-language explanation with comparison to more familiar approaches.
   For example, if the topic is "Gaussian Processes", explain:
   - What a GP is vs a neural network (key structural differences)
   - Why GPs never dominated like NNs did (scaling O(N³), fixed kernels vs learned features,
     GPU/SGD revolution favoring NNs)
   - Where GPs still shine (uncertainty quantification, small data, Bayesian optimization)

2. KEY SURVEY/COMPARISON PAPERS: For each topic, list 2-4 important papers that help
   contextualize it. For each paper provide:
   - Full title
   - Authors (main ones)
   - Year of publication
   - One paragraph on what it showed and why it matters
   - Whether anyone in industry adopted the ideas

3. INDUSTRY ADOPTION: Has this topic been adopted at scale? By whom? If not, why not?

4. RELATIONSHIP TO NEURAL NETWORKS: How does this topic connect to or diverge from
   mainstream deep learning? This is critical — our audience thinks in terms of
   transformers, SGD, and GPUs.

Output as JSON:
{{
  "background": [
    {{
      "topic": "...",
      "explanation": "multi-paragraph explanation...",
      "vs_neural_networks": "specific comparison...",
      "why_not_mainstream": "why this hasn't dominated...",
      "industry_adoption": "who uses it and how...",
      "key_papers": [
        {{
          "title": "...",
          "authors": "...",
          "year": "...",
          "summary": "paragraph summary...",
          "industry_impact": "..."
        }}
      ]
    }}
  ]
}}
Only output JSON.

Paper being discussed (for context):
{text[:6000]}"""

    return _llm_json(client, model, prompt)


# ---------------------------------------------------------------------------
# Pass 2: Concept Analysis + Critical Questions
# ---------------------------------------------------------------------------

def _concept_analysis_pass(text, background_context, config):
    """Identify concepts needing explanation and critical questions, informed by background."""
    client = _get_openai_client()
    model = config.get("podcast", {}).get("analysis_model", "gpt-4o")

    bg_text = ""
    if background_context:
        for bg in background_context.get("background", []):
            bg_text += f"\n### {bg['topic']}\n{bg['explanation']}\n"
            bg_text += f"vs NNs: {bg.get('vs_neural_networks', '')}\n"

    prompt = f"""You are a research analyst preparing a podcast about an AI/ML paper or report.

BACKGROUND RESEARCH already done on new topics:
{bg_text if bg_text else "(all topics previously covered)"}

Now analyze the specific paper/report and identify:

1. CRITICAL QUESTIONS a skeptical, well-informed reader should ask.
   Generate questions that are SPECIFIC and RELEVANT to this particular work.
   
   DO NOT ask generic boilerplate questions. In particular:
   - Do NOT ask "has anyone adopted this?" for technologies that are ALREADY widely
     deployed (e.g., LLM reasoning, transformers, attention mechanisms, fine-tuning,
     RLHF, chain-of-thought). Only ask about adoption for genuinely niche or
     unproven methods.
   - Do NOT ask "does this scale?" for methods already running at scale.
   - DO ask about methodology: Are the claims well-supported by the data? Are there
     confounding factors? Is the sample representative? Are the baselines fair?
   - DO scrutinize EXPERIMENTAL SCALE: What models/datasets were actually tested?
     Are the experiments at toy scale (e.g., GPT-2 124M) while claiming general applicability?
     Does fine-tuning at 8B prove the method works for pretraining at 8B? Be specific about
     what was tested vs what is claimed. If a paper says "memory efficient training" but only
     tests on small models or only fine-tuning, that's a critical gap worth discussing.
   - DO ask about what's missing: What perspectives or data are absent?
   - DO challenge assumptions specific to THIS work.

2. ADDITIONAL REFERENCES from the paper's citations or related work that deserve discussion.
   For each, give title, authors, year, and why it matters.

3. BLIND SPOT ANALYSIS — The paper focuses on one framing. Think about what it IGNORES:
   - Are there other uses of the artifacts/components this paper modifies?
     (e.g., if a paper quantizes optimizer states, who else uses those states for
     pruning, merging, continual learning, inference, etc.?)
   - Does the paper's optimization break something downstream it doesn't discuss?
   - What implicit assumptions does the paper make about how its components are used?
   - Are there recent papers (2024-2026) that give NEW value to what this paper treats
     as disposable or unimportant?
   Generate 1-3 blind spot questions that a reviewer would raise.

4. SCOPE vs CLAIMS — Compare what the paper CLAIMS (title, abstract, introduction) vs
   what it ACTUALLY TESTS (experiments section). Identify any gap between the two.
   Be specific: list claimed scope and actual experimental scope side by side.

Output as JSON:
{{
  "critical_questions": ["..."],
  "additional_references": [{{"title": "...", "authors": "...", "year": "...", "relevance": "..."}}],
  "blind_spots": ["..."],
  "scope_vs_claims": {{
    "claimed_scope": "...",
    "actual_experimental_scope": "...",
    "gap_assessment": "..."
  }}
}}
Only output JSON.

Paper content:
{text[:8000]}"""

    return _llm_json(client, model, prompt)


# ---------------------------------------------------------------------------
# Pass 2.5a: Local Adversarial Search (our own prior episodes)
# ---------------------------------------------------------------------------

def _local_adversarial_search(text, analysis, config):
    """Search our own podcast back catalog for related prior episodes.

    Returns relevant prior episodes with excerpts that could inform or challenge
    the current paper's claims.
    """
    import os

    client = _get_openai_client()
    model = config.get("podcast", {}).get("analysis_model", "gpt-4o")

    # Load ALL prior episodes: new pipeline DB + legacy Anchor feed
    from db import get_connection, init_db, list_podcasts
    import xml.etree.ElementTree as ET
    import re as _re

    catalog = []

    # 1. New pipeline episodes (with transcripts)
    conn = get_connection()
    init_db(conn)
    episodes = list_podcasts(conn)
    conn.close()

    for ep in episodes:
        audio = ep.get("audio_file", "")
        txt_path = audio.replace(".mp3", ".txt") if audio else ""
        transcript = ""
        if txt_path and os.path.exists(txt_path):
            transcript = open(txt_path, "r").read()[:1500]

        pub_date = ep.get("publish_date", "unknown")
        title = ep.get("title", "Unknown Episode")
        description = (ep.get("description", "") or "")[:500]
        audio_basename = os.path.basename(audio) if audio else ""
        ep_url = f"https://podcast.do-not-panic.com/episodes/{audio_basename}" if audio_basename else ""

        catalog.append({
            "title": title,
            "pub_date": pub_date,
            "url": ep_url,
            "description": description,
            "excerpt": transcript or description
        })

    # 2. Legacy Anchor episodes (from cached feed — titles + descriptions)
    anchor_feed = os.path.join(os.path.dirname(__file__), "podcasts", "anchor_feed.xml")
    if os.path.exists(anchor_feed):
        try:
            tree = ET.parse(anchor_feed)
            root = tree.getroot()
            for item in root.findall(".//item"):
                title_elem = item.find("title")
                desc_elem = item.find("description")
                pub_elem = item.find("pubDate")
                link_elem = item.find("link")

                title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                if not title:
                    continue

                # Clean HTML from description
                desc_raw = desc_elem.text if desc_elem is not None and desc_elem.text else ""
                desc_clean = _re.sub(r'<[^>]+>', '', desc_raw).strip()[:800]

                pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""
                link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""

                catalog.append({
                    "title": title,
                    "pub_date": pub_date,
                    "url": link,
                    "description": desc_clean,
                    "excerpt": desc_clean
                })
        except Exception as e:
            print(f"[Podcast]   Warning: Could not parse anchor feed: {e}", file=sys.stderr)

    if not catalog:
        return {"prior_episodes": []}

    print(f"[Podcast] Pass 2.5a: Searching {len(catalog)} prior episodes for connections...", file=sys.stderr)

    # Step 1: Extract key terms from the paper for keyword filtering
    # Use topics + critical questions to build search terms
    search_terms = []
    for t in analysis.get("critical_questions", []):
        # Extract key nouns/phrases
        for word in t.lower().split():
            if len(word) > 5 and word.isalpha():
                search_terms.append(word)
    # Add topic names
    topics = analysis.get("topics", [])
    if isinstance(topics, list):
        for t in topics:
            name = t.get("name", t) if isinstance(t, dict) else str(t)
            search_terms.extend(name.lower().split())

    # Also extract key terms from paper title/first 500 chars
    paper_start = text[:500].lower()
    key_phrases = _re.findall(r'[a-z]+(?:\s+[a-z]+){0,2}', paper_start)
    for phrase in key_phrases:
        if len(phrase) > 6:
            search_terms.append(phrase)

    search_terms = list(set(search_terms))

    # Step 2: Filter catalog by keyword relevance (cheap, no LLM)
    def _relevance_score(ep):
        searchable = (ep["title"] + " " + ep["description"] + " " + ep.get("excerpt", "")).lower()
        return sum(1 for term in search_terms if term in searchable)

    scored = [(ep, _relevance_score(ep)) for ep in catalog]
    relevant = sorted([(ep, s) for ep, s in scored if s > 0], key=lambda x: -x[1])
    top_matches = [ep for ep, _ in relevant[:15]]  # Top 15 most relevant

    if not top_matches:
        print(f"[Podcast]   No keyword matches in prior episodes", file=sys.stderr)
        return {"prior_episodes": []}

    print(f"[Podcast]   {len(relevant)} keyword matches, sending top {len(top_matches)} to LLM", file=sys.stderr)

    # Step 3: Send only relevant episodes to LLM for deeper analysis
    catalog_text = ""
    for c in top_matches:
        catalog_text += f"\n--- Episode: {c['title']} ({c['pub_date']}) ---\n"
        catalog_text += f"URL: {c['url']}\n"
        catalog_text += f"Description: {c['description'][:400]}\n"
        catalog_text += f"Excerpt:\n{c['excerpt'][:500]}\n"

    prompt = f"""You are reviewing a new paper for a podcast. Check if any PRIOR episodes
from the same podcast covered related topics that are relevant to this new paper.

NEW PAPER (first 3000 chars):
{text[:3000]}

CRITICAL QUESTIONS for the new paper:
{chr(10).join('- ' + q for q in analysis.get('critical_questions', []))}

PRIOR EPISODES:
{catalog_text}

For each prior episode that has a GENUINE connection to this new paper, identify:
1. What was covered in the prior episode that relates to this paper
2. How it informs, challenges, or provides context for the new paper
3. A specific callback the hosts could make ("As we discussed in our episode on X...")

Output as JSON:
{{
  "prior_episodes": [
    {{
      "episode_title": "...",
      "episode_date": "...",
      "episode_url": "...",
      "connection": "How this prior episode relates to the new paper",
      "callback_line": "Natural dialogue line referencing the prior episode",
      "relevance": "high|medium|low"
    }}
  ]
}}
Only include episodes with GENUINE connections. If no prior episodes are relevant,
return an empty array. Do NOT force connections that don't exist.
Only output JSON."""

    result = _llm_json(client, model, prompt, temperature=0.3, max_tokens=2000)
    if isinstance(result, list):
        prior = result
    else:
        prior = result.get("prior_episodes", [])
    relevant = [p for p in prior if p.get("relevance") in ("high", "medium")]
    print(f"[Podcast]   Found {len(relevant)} relevant prior episodes", file=sys.stderr)
    return {"prior_episodes": relevant}


# ---------------------------------------------------------------------------
# Pass 2.5b: External Adversarial Search (Google Scholar)
# ---------------------------------------------------------------------------

def _adversarial_search_pass(text, analysis, config):
    """Search for recent work that challenges, complicates, or extends the paper's claims.

    Uses the paper's key components and claims to find adversarial context via Google Scholar.
    Returns additional critical questions and references the LLM wouldn't know about.
    """
    import urllib.request
    import urllib.parse
    import re
    import time

    client = _get_openai_client()
    model = config.get("podcast", {}).get("analysis_model", "gpt-4o")

    # Step 1: Ask LLM to identify searchable claims and components
    print("[Podcast] Pass 2.5: Adversarial context search...", file=sys.stderr)
    search_prompt = f"""You are a research critic. Given this paper, identify:

1. KEY COMPONENTS the paper modifies, optimizes, or assumes are disposable
   (e.g., "Adam optimizer states", "gradient accumulator", "KV cache")
2. KEY CLAIMS that could be challenged by recent work
3. IMPLICIT ASSUMPTIONS about how components are used (e.g., "optimizer states
   are only useful during training")

For each, generate a Google Scholar search query that would find recent papers (2023-2026)
that give NEW VALUE to these components or CHALLENGE these assumptions.

QUERY GUIDELINES:
- Keep queries SHORT (3-6 words with one quoted phrase max)
- Use common academic terms, not paper-specific jargon
- Good: "Adam optimizer" "second moment" pruning OR inference
- Good: "optimizer states" reuse "model merging" OR "continual learning"
- Bad: "8-bit optimizer state quantization" AND "quantization error bounds" (too specific)
- Think: "who else uses the thing this paper modifies?"

Output as JSON:
{{
  "search_queries": [
    {{
      "component": "what the paper touches/modifies",
      "assumption": "what the paper assumes about it",
      "query": "short Google Scholar query",
      "what_to_look_for": "what kind of paper would challenge this"
    }}
  ]
}}
Generate 3-5 targeted queries.
Only output JSON.

Paper content (first 4000 chars):
{text[:4000]}"""

    search_plan = _llm_json(client, model, search_prompt, temperature=0.3, max_tokens=2000)
    queries = search_plan.get("search_queries", [])

    if not queries:
        print("[Podcast]   No adversarial queries generated, skipping", file=sys.stderr)
        return {"adversarial_findings": [], "adversarial_refs": []}

    print(f"[Podcast]   Generated {len(queries)} adversarial search queries", file=sys.stderr)

    # Step 2: Search Google Scholar for each query
    all_results = []
    for i, q in enumerate(queries[:4]):  # Cap at 4 searches to avoid rate limiting
        query_str = q.get("query", "")
        if not query_str:
            continue

        encoded = urllib.parse.quote_plus(query_str)
        url = f"https://scholar.google.com/scholar?q={encoded}&as_ylo=2023"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Extract paper titles and snippets from Scholar HTML
            # Look for <h3 class="gs_rt"> for titles and <div class="gs_rs"> for snippets
            titles = re.findall(r'<h3[^>]*class="gs_rt"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
            snippets = re.findall(r'<div[^>]*class="gs_rs"[^>]*>(.*?)</div>', html, re.DOTALL)

            # Clean HTML tags
            def clean(s):
                return re.sub(r'<[^>]+>', '', s).strip()

            results_for_query = []
            for j in range(min(len(titles), 3)):  # Top 3 per query
                title = clean(titles[j]) if j < len(titles) else ""
                snippet = clean(snippets[j]) if j < len(snippets) else ""
                if title:
                    results_for_query.append({
                        "title": title,
                        "snippet": snippet[:300],
                        "component": q.get("component", ""),
                        "assumption_challenged": q.get("assumption", ""),
                        "what_to_look_for": q.get("what_to_look_for", "")
                    })

            all_results.extend(results_for_query)
            print(f"[Podcast]   Query {i+1}: '{query_str[:60]}...' → {len(results_for_query)} results", file=sys.stderr)

        except Exception as e:
            print(f"[Podcast]   Query {i+1} failed: {e}", file=sys.stderr)

        # Rate limit: be gentle with Scholar
        time.sleep(2)

    if not all_results:
        print("[Podcast]   No Scholar results found, skipping adversarial analysis", file=sys.stderr)
        return {"adversarial_findings": [], "adversarial_refs": []}

    # Step 3: Ask LLM to synthesize adversarial findings
    results_text = "\n".join([
        f"- [{r['title']}] (found for component: {r['component']})\n"
        f"  Snippet: {r['snippet']}\n"
        f"  Assumption challenged: {r['assumption_challenged']}\n"
        f"  Looking for: {r['what_to_look_for']}"
        for r in all_results
    ])

    synth_prompt = f"""You are a research critic. I searched Google Scholar for recent papers that
might challenge or complicate the claims of a paper we're reviewing.

ORIGINAL PAPER (first 3000 chars):
{text[:3000]}

ORIGINAL CRITICAL QUESTIONS:
{chr(10).join('- ' + q for q in analysis.get('critical_questions', []))}

SCHOLAR SEARCH RESULTS:
{results_text}

Based on these search results, identify:

1. ADVERSARIAL FINDINGS: Papers that genuinely challenge, complicate, or add important
   context to the original paper's claims. Only include findings that are REAL and RELEVANT.
   Don't fabricate connections that don't exist.

2. NEW CRITICAL QUESTIONS that arise from these findings and weren't in the original analysis.

3. ADVERSARIAL REFERENCES: Papers worth citing in the podcast discussion (with approximate
   author, year, and why they matter).

Output as JSON:
{{
  "adversarial_findings": [
    "One-sentence finding that challenges/complicates the original paper"
  ],
  "new_critical_questions": [
    "Question the podcast should address based on these findings"
  ],
  "adversarial_refs": [
    {{"title": "...", "authors": "approximate", "year": "...", "relevance": "how it challenges the paper"}}
  ]
}}
Only include findings you're confident are real based on the snippets. If a search result
is irrelevant or you can't tell what the paper actually says, skip it.
Only output JSON."""

    result = _llm_json(client, model, synth_prompt, temperature=0.3, max_tokens=4000)
    n_findings = len(result.get("adversarial_findings", []))
    n_questions = len(result.get("new_critical_questions", []))
    n_refs = len(result.get("adversarial_refs", []))
    print(f"[Podcast]   Adversarial synthesis: {n_findings} findings, {n_questions} new questions, {n_refs} refs", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Pass 3: Script Generation
# ---------------------------------------------------------------------------

def generate_podcast_script(text, config, covered_topics=None):
    """Multi-pass podcast script generation with topic awareness and critical review.

    Returns (script, sources, topic_names) where script is a list of {speaker, text} dicts
    and sources is a list of {title, authors, year, url} dicts for all referenced works.
    """
    from fun_facts import get_podcast_context
    from db import get_connection, init_db, mark_facts_used

    client = _get_openai_client()
    podcast_config = config.get("podcast", {})
    max_words = podcast_config.get("max_words", 3000)
    model = podcast_config.get("llm_model", "gpt-4o")

    if covered_topics is None:
        covered_topics = set()

    # Get fun facts for this episode
    fun_context = get_podcast_context()
    intro_jokes = fun_context.get("intro_jokes", [])
    color_facts = fun_context.get("color_facts", [])
    all_facts = fun_context.get("all", [])

    if intro_jokes:
        print(f"[Podcast]   {len(intro_jokes)} intro jokes available, "
              f"{len(color_facts)} color facts", file=sys.stderr)
    else:
        print("[Podcast]   No fun facts available (run: python fun_facts.py collect)",
              file=sys.stderr)

    # Episode dynamics — interruptions, disagreements, joke discipline
    dynamics = podcast_config.get("dynamics", {})
    interrupt_count = dynamics.get("interrupt_per_episode", 1)
    disagreement_every = dynamics.get("disagreement_every_n", 2)
    max_jokes = dynamics.get("max_jokes_per_episode", 2)

    # Check episode count for disagreement trigger
    conn_ep = get_connection()
    init_db(conn_ep)
    from db import get_episode_count
    ep_count = get_episode_count(conn_ep)
    conn_ep.close()
    do_disagreement = (ep_count % disagreement_every == 0)

    print(f"[Podcast]   Episode #{ep_count + 1}: "
          f"interrupts={interrupt_count}, "
          f"disagreement={'YES' if do_disagreement else 'no'}, "
          f"max_jokes={max_jokes}", file=sys.stderr)

    # Pass 0: Topic classification
    print("[Podcast] Pass 0: Classifying topics...", file=sys.stderr)
    topic_info = _topic_classification_pass(text, covered_topics, config)
    topics = topic_info.get("topics", [])
    new_topics = [t for t in topics if t.get("is_new", True)]
    topic_names = [t["name"] for t in topics]

    print(f"[Podcast]   Topics: {', '.join(topic_names)}", file=sys.stderr)
    print(f"[Podcast]   New topics: {', '.join(t['name'] for t in new_topics) or 'none'}", file=sys.stderr)

    # Shared author detection
    paper_authors = topic_info.get("authors", [])
    paper_title = topic_info.get("title", "")
    paper_institutions = topic_info.get("institutions", [])
    shared_authors = []
    if paper_authors:
        conn_auth = get_connection()
        init_db(conn_auth)
        shared_authors = _find_shared_authors(paper_authors, conn_auth)
        conn_auth.close()
        if shared_authors:
            print(f"[Podcast]   ⚡ Shared authors detected:", file=sys.stderr)
            for sa in shared_authors:
                print(f"[Podcast]     {sa['author']} also authored: {sa['prior_paper']}", file=sys.stderr)

    # Pass 1: Background research (only for new topics)
    background_context = None
    background_text = ""
    all_bg_papers = []

    if new_topics:
        print(f"[Podcast] Pass 1: Researching {len(new_topics)} new topic(s)...", file=sys.stderr)
        background_context = _background_research_pass(text, new_topics, config)

        for bg in background_context.get("background", []):
            background_text += f"\n=== BACKGROUND: {bg['topic']} ===\n"
            background_text += f"EXPLANATION:\n{bg['explanation']}\n\n"
            background_text += f"VS NEURAL NETWORKS:\n{bg.get('vs_neural_networks', 'N/A')}\n\n"
            background_text += f"WHY NOT MAINSTREAM:\n{bg.get('why_not_mainstream', 'N/A')}\n\n"
            background_text += f"INDUSTRY ADOPTION:\n{bg.get('industry_adoption', 'N/A')}\n\n"
            background_text += "KEY PAPERS:\n"
            for p in bg.get("key_papers", []):
                background_text += f"  - {p['title']} ({p['authors']}, {p['year']}): {p['summary']}\n"
                all_bg_papers.append(p)
    else:
        print("[Podcast] Pass 1: Skipped (all topics previously covered)", file=sys.stderr)

    # Pass 2: Concept analysis
    print("[Podcast] Pass 2: Analyzing concepts and critical questions...", file=sys.stderr)
    analysis = _concept_analysis_pass(text, background_context, config)

    # Pass 2.5a: Local adversarial search (our own prior episodes)
    local_adversarial = _local_adversarial_search(text, analysis, config)

    # Pass 2.5b: External adversarial search (Google Scholar)
    adversarial = _adversarial_search_pass(text, analysis, config)

    questions_text = "\n".join([f"- {q}" for q in analysis.get("critical_questions", [])])

    # Include blind spots and scope analysis in critical questions
    blind_spots = analysis.get("blind_spots", [])
    if blind_spots:
        questions_text += "\n\nBLIND SPOTS (the paper ignores these — discuss critically):\n"
        questions_text += "\n".join([f"- {b}" for b in blind_spots])

    scope_analysis = analysis.get("scope_vs_claims", {})
    if scope_analysis and scope_analysis.get("gap_assessment"):
        questions_text += f"\n\nSCOPE vs CLAIMS GAP:\n"
        questions_text += f"- Claimed: {scope_analysis.get('claimed_scope', 'N/A')}\n"
        questions_text += f"- Actually tested: {scope_analysis.get('actual_experimental_scope', 'N/A')}\n"
        questions_text += f"- Gap: {scope_analysis.get('gap_assessment', 'N/A')}\n"
        questions_text += "Discuss this gap honestly — what does the evidence actually support vs what the paper implies?"

    # Include adversarial findings from Pass 2.5
    adv_findings = adversarial.get("adversarial_findings", [])
    adv_questions = adversarial.get("new_critical_questions", [])
    if adv_findings or adv_questions:
        questions_text += "\n\nRELATED WORK THAT COMPLICATES OR CHALLENGES THIS PAPER (discuss naturally — do NOT say 'adversarial search' or 'we searched for papers'. Just bring up these findings as things you know about):\n"
        for f in adv_findings:
            questions_text += f"- {f}\n"
        for q in adv_questions:
            questions_text += f"- {q}\n"

    # Include prior episode connections from Pass 2.5a
    prior_eps = local_adversarial.get("prior_episodes", [])
    if prior_eps:
        questions_text += "\n\nTOPICS WE COVERED IN PRIOR EPISODES (reference naturally, e.g. 'as we discussed in our episode on X back in [date]' or 'listeners may remember when we covered X'):\n"
        for ep in prior_eps:
            questions_text += f"- We covered \"{ep['episode_title']}\" on {ep['episode_date']}: {ep['connection']}\n"

    # Shared author connections
    if shared_authors:
        questions_text += "\n\nSHARED AUTHORS WITH PRIOR PAPERS (mention naturally — e.g. 'interestingly, [author] was also behind [prior paper]', showing continuity of research lines):\n"
        for sa in shared_authors:
            questions_text += f"- {sa['author']} also authored \"{sa['prior_paper']}\" (arxiv: {sa['prior_arxiv']})\n"

    extra_refs_text = "\n".join([
        f"- {r['title']} ({r.get('authors', '?')}, {r.get('year', '?')}): {r['relevance']}"
        for r in analysis.get("additional_references", [])
    ])

    # Add adversarial references
    for r in adversarial.get("adversarial_refs", []):
        extra_refs_text += f"\n- {r['title']} ({r.get('authors', '?')}, {r.get('year', '?')}): {r['relevance']}"

    humor_text = ""  # Jokes come from situation only, not pre-planned

    # Collect all source papers
    all_sources = list(all_bg_papers)
    for r in analysis.get("additional_references", []):
        all_sources.append(r)
    for r in adversarial.get("adversarial_refs", []):
        all_sources.append(r)

    # Add prior episodes as sources
    for ep in prior_eps:
        all_sources.append({
            "title": f"AI Post Transformers: {ep['episode_title']}",
            "authors": "Hal Turing & Dr. Ada Shannon",
            "year": ep.get("episode_date", "")[:4] if ep.get("episode_date") else "",
            "url": ep.get("episode_url", ""),
            "relevance": ep.get("connection", "")
        })

    # Pass 3: Generate the script in TWO PARTS for length
    print("[Podcast] Pass 3: Generating conversation script...", file=sys.stderr)

    instructions = podcast_config.get("instructions", "")

    # Host configuration
    hosts = podcast_config.get("hosts", {})
    host_a = hosts.get("a", {})
    host_b = hosts.get("b", {})
    host_a_name = host_a.get("name", "Hal Turing")
    host_b_name = host_b.get("name", "Dr. Ada Shannon")
    host_a_personality = host_a.get("personality", "Warm, curious, asks good questions.")
    host_b_personality = host_b.get("personality", "Sharp, knowledgeable, direct expert.")

    # Intro
    intro = podcast_config.get("intro", "")

    host_block = f"""HOST A — {host_a_name} (speaker "A"):
{host_a_personality}

HOST B — {host_b_name} (speaker "B"):
{host_b_personality}

CITATION REQUIREMENTS:
When referencing ANY paper, ALWAYS include: paper title, first author, institution/lab,
year of publication. Example: "That reminds me of the Random Features paper by Rahimi
and Recht out of UC Berkeley back in 2007"

STYLE:
- Use the hosts' names naturally: "Great point, Ada" or "So Hal, here's the thing..."
- Conversational, not lecture-style — these two have chemistry and banter
- Be honest about hype vs substance
{instructions}"""

    new_topic_bg = ""
    if new_topics:
        new_topic_bg = f"""
CRITICAL — NEW TOPICS: These have NEVER been covered on this podcast. TEACH them
thoroughly. Compare to familiar concepts (transformers, SGD, neural nets).

{background_text}
"""

    # --- Generate script in 4 PARTS for sufficient length ---
    quarter_words = max_words // 4  # ~950 words each

    # Build dynamics instructions
    dynamics_text = f"""
CONVERSATION DYNAMICS — Make this sound like a REAL podcast:

INTERRUPTIONS: Include exactly {interrupt_count} moment(s) in this part where one host
cuts in mid-thought. Mark these segments with "interrupt": true in the JSON. The
interrupting host should start talking as if they couldn't wait — e.g., "Oh wait wait
wait—" or "Sorry to cut you off but—" or "Hold on, that's actually—". This creates
energy and feels natural. Only in ONE segment per part.

JOKE DISCIPLINE: Maximum {max_jokes} jokes across the ENTIRE episode (not per part).
ONLY situational humor is allowed — jokes that arise from the irony or absurdity of what's
being discussed. NO generic jokes, puns, or formulaic quips. If the material isn't naturally
funny, use ZERO jokes. Forced humor is worse than no humor.
"""

    if do_disagreement:
        dynamics_text += """
HEATED DISAGREEMENT: In this episode, include ONE moment where the hosts genuinely
disagree about an interpretation or conclusion. Not hostile — passionate and intellectual.
One host should push back firmly: "I actually disagree with you there, Hal..." or
"No no no, that's not how I read it at all..." Let the disagreement play out for 2-3
exchanges before they find common ground or agree to disagree. This adds authenticity.
"""

    common_style = f"""Each speaker turn should be 100-250 words — like a real conversation
paragraph, NOT a one-liner. Write substantial, detailed dialogue. If a turn is under
50 words, it's too short — expand it with examples, analogies, or follow-up thoughts.
Generate AT LEAST {quarter_words} words for this part.

IMPORTANT — SEAMLESS TRANSITIONS: This is ONE continuous conversation. Do NOT include
any "welcome back", "in this segment", "moving on to part two", or any language that
suggests a break or section change. The listener hears this as one unbroken audio stream.
Transition naturally — e.g., "That actually ties into something else I found interesting..."
or "So given all that, Ada, what do you think about..." — just flow naturally.
{dynamics_text}

OUTPUT FORMAT: JSON array of objects. Each object has "speaker" (A or B) and "text".
Optionally include "interrupt": true on segments where the speaker is cutting in
mid-sentence of the other host."""

    all_scripts = []

    # Build topic list for prompts
    topic_list = ", ".join(topic_names) if topic_names else "the topics in this paper/report"

    # Build fun facts context strings
    intro_joke_text = ""
    if intro_jokes:
        jokes = "\n".join([f"- {j['fact']}" for j in intro_jokes])
        intro_joke_text = f"""
INTRO BANTER — After the standard intro and paper introduction, Hal and Ada may have
a quick fun exchange ONLY if one of these facts directly relates to the paper's topic:
{jokes}
RULES:
- ONLY use a fact if it is DIRECTLY RELEVANT to the specific paper being discussed.
- If NONE of the facts relate to this paper, SKIP the banter entirely. Go straight to content.
- Do NOT make meta-commentary about being AI hosts (no "it's weird we're AIs talking about AI").
- Do NOT force a connection that doesn't exist naturally.
- If used, keep it to 2-3 exchanges max — punchy, not drawn out.
"""

    color_facts_text = ""
    if color_facts:
        facts = "\n".join([f"- [{f['category']}] {f['fact']}" for f in color_facts])
        color_facts_text = f"""
COLOR FACTS — You may reference ONE of these facts ONLY if it directly connects to what's
being discussed at that specific moment. If none are relevant to the current discussion
point, use NONE. Do NOT force irrelevant facts into the conversation:
{facts}
"""

    # --- ADAPTIVE LENGTH: Decide number of parts based on paper complexity ---
    num_topics = len(topic_names)
    num_questions = len(analysis.get("critical_questions", []))
    num_refs = len(analysis.get("additional_references", []))
    paper_len = len(text)

    # Complexity score: topics + questions + references + paper length factor
    complexity = num_topics + (num_questions * 0.5) + (num_refs * 0.3) + (paper_len / 20000)

    if complexity < 5:
        num_parts = 2  # Simple paper: ~8-10 min
    elif complexity < 8:
        num_parts = 3  # Moderate: ~12-15 min
    else:
        num_parts = 4  # Complex: ~17-23 min

    print(f"[Podcast]   Complexity: {complexity:.1f} (topics={num_topics}, questions={num_questions}, refs={num_refs}, len={paper_len}) → {num_parts} parts", file=sys.stderr)

    # --- EPISODE BIBLE: Plan content allocation to prevent cross-part repetition ---
    print("[Podcast]   Generating episode bible...", file=sys.stderr)

    if num_parts == 2:
        bible_structure = """Create an EPISODE_BIBLE that allocates content across 2 parts.
Part 1: Intro + Background + Methods (the WHAT and HOW)
Part 2: Critical Analysis + Impact + Conclusion (the SO WHAT)
Keep it tight — this is a straightforward paper that doesn't need 4 parts."""
        part_allocation = """
  "part_1_must_cover": ["intro + authors", "background", "methods", "key results"],
  "part_1_do_not_cover": ["limitations deep dive", "future speculation"],
  "part_2_must_cover": ["critical analysis", "practical implications", "limitations", "closing"],
  "part_2_do_not_cover": ["re-introduce authors/title", "re-explain methods"]"""
    elif num_parts == 3:
        bible_structure = """Create an EPISODE_BIBLE that allocates content across 3 parts.
Part 1: Intro + Background Foundations
Part 2: Deep Dive into Methods + Results
Part 3: Critical Analysis + Impact + Conclusion
No padding — every segment must earn its place."""
        part_allocation = """
  "part_1_must_cover": ["intro + authors", "background foundations", "key definitions"],
  "part_1_do_not_cover": ["detailed methods", "limitations"],
  "part_2_must_cover": ["core methods", "key results/data", "technical details"],
  "part_2_do_not_cover": ["re-introduce authors/title", "re-define terms from Part 1"],
  "part_3_must_cover": ["critical analysis", "practical implications", "future directions", "closing"],
  "part_3_do_not_cover": ["re-introduce authors", "re-explain methods", "re-define terms"]"""
    else:
        bible_structure = """Create an EPISODE_BIBLE that allocates content across 4 parts to prevent ANY repetition."""
        part_allocation = """
  "part_1_must_cover": ["intro + authors", "background foundations", "key definitions"],
  "part_1_do_not_cover": ["detailed methods", "limitations critique", "societal implications"],
  "part_2_must_cover": ["core methods", "key results/data", "technical details"],
  "part_2_do_not_cover": ["re-introduce authors/title", "re-define terms from Part 1", "limitations"],
  "part_3_must_cover": ["critical analysis", "additional references", "limitations", "blind spots"],
  "part_3_do_not_cover": ["re-introduce authors", "re-explain methods already covered", "re-define terms"],
  "part_4_must_cover": ["practical implications", "future directions", "closing"],
  "part_4_do_not_cover": ["new technical details", "new definitions", "re-introduce authors"]"""

    bible_prompt = f"""You are planning a podcast episode about an academic paper/report.
The listener hears all parts in order as one continuous conversation.

{bible_structure}

Key topics: {topic_list}
Critical questions to address: {questions_text[:1000]}
Additional references: {extra_refs_text[:1000]}

Output as JSON:
{{
  "paper_citation": "Full citation (title, authors, institution, year) — say ONLY in Part 1",
  "core_research_question": "One sentence — state ONLY in Part 1",
  "key_definitions": [
    {{"term": "...", "define_in_part": 1, "brief": "..."}}
  ],
{part_allocation}
}}

Source content:
{text[:6000]}"""

    episode_bible = _llm_json(client, model, bible_prompt, temperature=0.3, max_tokens=4000)
    bible_text = json.dumps(episode_bible, indent=2)

    # Anti-repetition constraints used in all parts after Part 1
    no_repeat_rules = """
ANTI-REPETITION RULES (STRICT — VIOLATION = FAILURE):
- Do NOT re-introduce the paper title, authors, or institution — they were already introduced.
- Do NOT re-define any term that was already defined in a previous part.
- Do NOT re-ask any question listed in the COVERAGE MEMO, even rephrased differently.
  Example: if Part 1 asked "how does X work?", Part 3 CANNOT ask "how does X achieve Y?" — same question.
- Do NOT re-explain any concept or mechanism already covered. The listener ALREADY HEARD IT.
- If you must reference something from a previous part, use a callback of 12 words MAX
  (e.g., "As we discussed earlier..." or "Building on that point...").
- Each part must contain ONLY NEW information, NEW questions, and NEW insights.
"""

    # PART 1: Intro + Background Foundations
    print(f"[Podcast]   Part 1/{num_parts}: Intro + Background...", file=sys.stderr)
    p1 = f"""Generate PART 1 of {num_parts} of a podcast conversation.

{host_block}

EPISODE BIBLE (follow this allocation strictly):
{bible_text}

PART 1 COVERS — INTRO AND BACKGROUND FOUNDATIONS:
1. INTRO: Speaker A delivers this intro VERBATIM as the FIRST line:
   "{intro}"
   Then Hal introduces today's material: title, authors/organization, publication date.
   Give proper credit to who produced this work. Ada jumps in with why it caught her attention.
   This is the ONLY time authors and full title should be stated.
{intro_joke_text}

2. FOUNDATIONAL BACKGROUND: The key topics are: {topic_list}.
   For any topic that's new to the audience, explain it thoroughly. Compare with more
   familiar concepts. Reference foundational works by name, author, institution, year.
{new_topic_bg}
{color_facts_text}

{common_style}

Output as JSON array: [{{"speaker": "A", "text": "..."}}, ...]
Only output the JSON array.

Source content:
{text[:10000]}"""

    p1_script = _llm_json(client, model, p1, temperature=0.7, max_tokens=16000)
    if not isinstance(p1_script, list):
        p1_script = p1_script.get("script", [])
    p1_words = sum(len(s["text"].split()) for s in p1_script)
    print(f"[Podcast]     {len(p1_script)} segments, {p1_words} words", file=sys.stderr)
    all_scripts.extend(p1_script)

    # Extract questions and key concepts from a script part for the coverage memo
    def _extract_questions(segs):
        questions = []
        for s in segs:
            txt = s.get("text", "")
            # Find sentences ending with '?'
            for sentence in txt.replace("?", "?\n").split("\n"):
                sentence = sentence.strip()
                if sentence.endswith("?") and len(sentence) > 20:
                    questions.append(sentence)
        return questions

    def _extract_topics_discussed(segs):
        # Get a compact summary of what was discussed
        return "\n".join([f'- {s["speaker"]}: {s["text"][:150]}' for s in segs[:8]])

    # Build coverage memo from Part 1
    p1_questions = _extract_questions(p1_script)
    p1_topics = _extract_topics_discussed(p1_script)
    coverage_memo = f"""COVERAGE MEMO — What Part 1 already covered (DO NOT REPEAT):
- Authors, title, institution, publication date — fully introduced
- Core research question stated
- Background on: {topic_list}
- Terms defined in Part 1 (do not re-define)

QUESTIONS ALREADY ASKED IN PART 1 (DO NOT re-ask these or rephrase them):
{chr(10).join('- ' + q for q in p1_questions) if p1_questions else '- (none)'}

Topics discussed:
{p1_topics}
"""

    def _recap(segs):
        last = segs[-2:] if len(segs) >= 2 else segs
        return "\n".join([f'{s["speaker"]}: {s["text"][:300]}' for s in last])

    # PART 2: Deep Dive (or Deep Dive + Critical Analysis + Conclusion for 2-part)
    if num_parts == 2:
        p2_label = "Deep Dive + Critical Analysis + Conclusion"
        p2_content = f"""PART 2 COVERS — DEEP DIVE, CRITICAL ANALYSIS, AND CONCLUSION (FINAL PART):
1. Walk through the key findings, methods, or arguments. Core contribution and main results.
2. Technical concepts the audience needs, with analogies.
3. Critical analysis: address these questions naturally:
{questions_text}
4. Additional references: {extra_refs_text}
5. What's genuinely novel vs incremental? Limitations? Blind spots?
6. PRACTICAL IMPLICATIONS: Who does this affect?
7. CLOSING: Hal summarizes key takeaways. End with a simple farewell.
   Do NOT tease any "next episode" topic."""
    else:
        p2_label = "Deep dive into content"
        p2_content = f"""PART 2 COVERS — DEEP DIVE INTO THE CONTENT (only items from part_2_must_cover):
1. Walk through the key findings, methods, or arguments presented in this work.
   What's the core contribution? What are the main data points or results?
2. Explain any technical concepts that the audience needs to understand.
   Use analogies and comparisons to make complex ideas accessible.
3. Reference related works that provide context — cite by name, author,
   institution, and year."""

    print(f"[Podcast]   Part 2/{num_parts}: {p2_label}...", file=sys.stderr)
    p2 = f"""Generate PART 2 of {num_parts} of a podcast conversation.{' This is the FINAL part.' if num_parts == 2 else ''} Continues from Part 1.
Last lines from Part 1:
{_recap(p1_script)}

{host_block}

EPISODE BIBLE (follow this allocation strictly):
{bible_text}

{coverage_memo}

{no_repeat_rules}

{p2_content}

HUMOR POLICY — STRICT:
Do NOT insert generic jokes, puns, or formulaic humor.
The ONLY acceptable humor is SITUATIONAL — arising naturally from the material.
If nothing is genuinely funny in context, use ZERO jokes.
{color_facts_text}

{common_style}

Output as JSON array: [{{"speaker": "A", "text": "..."}}, ...]
Only output the JSON array.

Source content:
{text[:10000]}"""

    p2_script = _llm_json(client, model, p2, temperature=0.7, max_tokens=16000)
    if not isinstance(p2_script, list):
        p2_script = p2_script.get("script", [])
    p2_words = sum(len(s["text"].split()) for s in p2_script)
    print(f"[Podcast]     {len(p2_script)} segments, {p2_words} words", file=sys.stderr)
    all_scripts.extend(p2_script)

    # Update coverage memo
    p2_questions = _extract_questions(p2_script)
    p2_topics = _extract_topics_discussed(p2_script)
    coverage_memo += f"""
What Part 2 additionally covered (DO NOT REPEAT):
- Core methods and technical details explained
- Key results and data points presented

QUESTIONS ALREADY ASKED IN PART 2 (DO NOT re-ask or rephrase):
{chr(10).join('- ' + q for q in p2_questions) if p2_questions else '- (none)'}

Topics discussed in Part 2:
{p2_topics}
"""

    # PART 3: Critical Analysis + References (skip for 2-part episodes)
    if num_parts >= 3:
      print(f"[Podcast]   Part 3/{num_parts}: Critical analysis...", file=sys.stderr)
      p3 = f"""Generate PART 3 of {num_parts} of a podcast conversation.{' This is the FINAL part.' if num_parts == 3 else ''} Continues from Part 2.
Last lines from Part 2:
{_recap(p2_script)}

{host_block}

EPISODE BIBLE (follow this allocation strictly):
{bible_text}

{coverage_memo}

{no_repeat_rules}

PART 3 COVERS — CRITICAL ANALYSIS (only items from part_3_must_cover):
1. Address these critical questions naturally:
{questions_text}

2. Discuss these additional references, citing by name, author, institution, year:
{extra_refs_text}

3. What's genuinely novel vs incremental? What are the limitations or blind spots?
   What's missing? Be honest and specific.
{"" if num_parts > 3 else """
4. PRACTICAL IMPLICATIONS: Who does this affect? What should practitioners do differently?
5. FUTURE DIRECTIONS: Where is this heading? Open questions?
6. CLOSING: Hal summarizes key takeaways. End with a simple farewell.
   Do NOT tease any next episode topic."""}

HUMOR POLICY — STRICT:
Do NOT insert generic jokes, puns, or formulaic humor.
The ONLY acceptable humor is SITUATIONAL — arising naturally from the material.
If nothing is genuinely funny in context, use ZERO jokes.
{color_facts_text}

{common_style}

Output as JSON array: [{{"speaker": "A", "text": "..."}}, ...]
Only output the JSON array.

Source content:
{text[:10000]}"""

      p3_script = _llm_json(client, model, p3, temperature=0.7, max_tokens=16000)
      if not isinstance(p3_script, list):
          p3_script = p3_script.get("script", [])
      p3_words = sum(len(s["text"].split()) for s in p3_script)
      print(f"[Podcast]     {len(p3_script)} segments, {p3_words} words", file=sys.stderr)
      all_scripts.extend(p3_script)

      # Update coverage memo
      p3_questions = _extract_questions(p3_script)
      coverage_memo += f"""
What Part 3 additionally covered (DO NOT REPEAT):
- Critical analysis and limitations discussed
- Additional references cited
- Novelty vs incremental assessment made

QUESTIONS ALREADY ASKED IN PARTS 1-3 (DO NOT re-ask or rephrase):
{chr(10).join('- ' + q for q in (p1_questions + p2_questions + p3_questions)) if (p1_questions + p2_questions + p3_questions) else '- (none)'}
"""

    # PART 4: Real-World Impact + Conclusion (only for 4-part episodes)
    if num_parts >= 4:
      print(f"[Podcast]   Part 4/{num_parts}: Impact + conclusion...", file=sys.stderr)
      p4 = f"""Generate PART 4 of 4 (the FINAL part) of a podcast conversation. Continues from Part 3.
Last lines from Part 3:
{_recap(p3_script)}

{host_block}

EPISODE BIBLE (follow this allocation strictly):
{bible_text}

{coverage_memo}

{no_repeat_rules}

PART 4 COVERS — REAL-WORLD IMPACT AND CONCLUSION (only items from part_4_must_cover):
1. PRACTICAL IMPLICATIONS: Who does this affect? What should practitioners,
   companies, or researchers do differently based on this work?

2. FUTURE DIRECTIONS: Where is this heading? What are the open questions?

3. CLOSING: Hal summarizes key takeaways for the audience. What should the listener
   remember? End with a simple farewell — Hal thanks the audience and Ada.
   Do NOT tease or mention any "next episode" topic. Do NOT invent future episode
   subjects. Just say goodbye naturally.
{color_facts_text}

{common_style}

Output as JSON array: [{{"speaker": "A", "text": "..."}}, ...]
Only output the JSON array.

Source content:
{text[:10000]}"""

      p4_script = _llm_json(client, model, p4, temperature=0.7, max_tokens=16000)
      if not isinstance(p4_script, list):
          p4_script = p4_script.get("script", [])
      p4_words = sum(len(s["text"].split()) for s in p4_script)
      print(f"[Podcast]     {len(p4_script)} segments, {p4_words} words", file=sys.stderr)
      all_scripts.extend(p4_script)

    # --- EDITORIAL PASS: Review full script for repetitions and flow ---
    print("[Podcast]   Editorial pass: reviewing full script for repetitions...", file=sys.stderr)
    full_transcript = "\n".join([f'{i+1}. {s["speaker"]}: {s["text"]}' for i, s in enumerate(all_scripts)])
    edit_prompt = f"""You are a podcast script editor. Review this full transcript and fix it.

RULES:
1. REMOVE or REWRITE any segment where a question is asked that was already asked earlier
   (even if phrased differently). The FIRST occurrence stays; later duplicates get rewritten
   to ask something NEW and relevant, or removed if nothing new to add.
2. REMOVE or REWRITE any segment that re-explains a concept already explained earlier.
   Brief callbacks (≤12 words like "as we discussed") are fine; full re-explanations are not.
3. REMOVE any segment that re-introduces authors, paper title, or institution after the intro.
4. REMOVE filler/padding segments that add no new information (e.g., "That's a great point" 
   followed by repeating what was just said).
5. Ensure smooth transitions — if you remove a segment, adjust the surrounding segments so
   the conversation flows naturally.
6. Do NOT add new content or change the meaning. Only cut redundancy and smooth transitions.
7. Keep the conversation feeling natural — some acknowledgment between speakers is fine,
   but it should lead to NEW information, not rehashing.

Output the EDITED script as a JSON array: [{{"speaker": "A", "text": "..."}}, ...]
Include the "interrupt": true field on any segment that had it in the original.
Only output the JSON array.

FULL TRANSCRIPT:
{full_transcript}"""

    edited_script = _llm_json(client, model, edit_prompt, temperature=0.3, max_tokens=16000)
    if isinstance(edited_script, list) and len(edited_script) > 5:
        removed = len(all_scripts) - len(edited_script)
        edited_words = sum(len(s["text"].split()) for s in edited_script)
        print(f"[Podcast]   Editorial: {len(edited_script)} segments ({removed:+d}), {edited_words} words", file=sys.stderr)
        all_scripts = edited_script
    else:
        print("[Podcast]   Editorial pass returned invalid result, using original", file=sys.stderr)

    script = all_scripts
    total_words = sum(len(s["text"].split()) for s in script)
    print(f"[Podcast]   Total: {len(script)} segments, {total_words} words (~{total_words/150:.0f} min)", file=sys.stderr)

    # Mark all provided fun facts as used (even if LLM didn't use them all,
    # we cycle through to keep things fresh)
    if all_facts:
        fact_ids = [f["id"] for f in all_facts if "id" in f]
        if fact_ids:
            conn = get_connection()
            init_db(conn)
            mark_facts_used(conn, fact_ids)
            conn.close()
            print(f"[Podcast]   Marked {len(fact_ids)} fun facts as used", file=sys.stderr)

    # Use sources already collected from Pass 1 and Pass 2
    sources = all_sources

    # Merge sources from all passes
    seen_titles = set()
    merged_sources = []
    for s in list(sources) + all_sources:
        title = s.get("title", "").lower().strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            merged_sources.append(s)

    return script, merged_sources, topic_names


def create_podcast(text, config, covered_topics=None):
    """Create a podcast by generating script + stitching TTS segments.

    Returns (tmpdir, list_file, segment_files, sources, topic_names).
    """
    import subprocess
    import tempfile

    podcast_config = config.get("podcast", {})
    el_config = config.get("elevenlabs", {})

    voice_a = el_config.get("voice_a", "oTOJ3soGzir2ldiaDSNs")  # Host A
    voice_b = el_config.get("voice_b", "HBQuDIqftrmAQQAHSWnF")  # Host B

    # Also check nested voices config
    voices = el_config.get("voices", {})
    if voices:
        host = voices.get("host", {})
        guest = voices.get("guest", {})
        if host.get("voice_id"):
            voice_a = host["voice_id"]
        if guest.get("voice_id"):
            voice_b = guest["voice_id"]

    print("[Podcast] Generating conversation script...", file=sys.stderr)
    script, sources, topic_names = generate_podcast_script(text, config, covered_topics)

    # Count interrupts
    interrupt_segs = sum(1 for s in script if s.get("interrupt"))
    print(f"[Podcast] Script has {len(script)} segments, {len(sources)} sources, "
          f"{interrupt_segs} interrupts", file=sys.stderr)

    # Generate TTS for each segment
    tmpdir = tempfile.mkdtemp(prefix="podcast_")
    segment_files = []

    # --- Generate countdown + dual-voice intro ---
    print("[Podcast] Generating countdown intro...", file=sys.stderr)
    import subprocess

    # Countdown: 3 (Hal), 2 (Ada), 1 (Hal)
    tts_segment("three", voice_a, os.path.join(tmpdir, "countdown_3.mp3"))
    tts_segment("two", voice_b, os.path.join(tmpdir, "countdown_2.mp3"))
    tts_segment("one", voice_a, os.path.join(tmpdir, "countdown_1.mp3"))
    time.sleep(0.3)

    # Both voices say the show name (overlaid)
    tts_segment("Welcome to AI Post Transformers!", voice_a, os.path.join(tmpdir, "welcome_a.mp3"))
    tts_segment("Welcome to AI Post Transformers!", voice_b, os.path.join(tmpdir, "welcome_b.mp3"))
    time.sleep(0.3)

    # Overlay both welcome voices
    subprocess.run(
        ["ffmpeg", "-y", "-i", os.path.join(tmpdir, "welcome_a.mp3"),
         "-i", os.path.join(tmpdir, "welcome_b.mp3"),
         "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
         "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "welcome_overlay.mp3")],
        capture_output=True, text=True
    )

    # Short pause between countdown numbers
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "0.3", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "short_pause.mp3")],
        capture_output=True, text=True
    )

    # Build the intro sequence as pre-segments
    intro_audio_files = [
        os.path.join(tmpdir, "countdown_3.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_2.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_1.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "welcome_overlay.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
    ]

    # --- Generate main script TTS ---
    for i, seg in enumerate(script):
        voice = voice_a if seg["speaker"] == "A" else voice_b
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        print(f"[Podcast] TTS segment {i+1}/{len(script)} ({seg['speaker']})"
              f"{'[INT]' if seg.get('interrupt') else ''}...", file=sys.stderr)
        tts_segment(seg["text"], voice, seg_path)
        segment_files.append(seg_path)
        time.sleep(0.3)  # Rate limit courtesy

    # Build concat list, handling interrupts with crossfade
    list_file = os.path.join(tmpdir, "segments.txt")
    # For interrupts, we'll create overlapped segments using ffmpeg amix
    final_segment_files = []
    i = 0
    while i < len(segment_files):
        if i + 1 < len(segment_files) and script[i + 1].get("interrupt"):
            # Overlap: mix last 0.8s of current with start of next
            overlap_path = os.path.join(tmpdir, f"overlap_{i:03d}.mp3")
            import subprocess
            # Get duration of current segment
            dur = _get_segment_duration(segment_files[i])
            if dur > 1.0:
                # Trim current to lose last 0.8s, then crossfade with next
                trimmed = os.path.join(tmpdir, f"trimmed_{i:03d}.mp3")
                tail = os.path.join(tmpdir, f"tail_{i:03d}.mp3")
                trim_at = dur - 0.8
                # Get trimmed main part
                subprocess.run(
                    ["ffmpeg", "-y", "-i", segment_files[i], "-t", str(trim_at),
                     "-c:a", "libmp3lame", "-q:a", "2", trimmed],
                    capture_output=True, text=True
                )
                # Get the tail (last 0.8s) and overlay with interrupt start
                subprocess.run(
                    ["ffmpeg", "-y", "-i", segment_files[i], "-ss", str(trim_at),
                     "-c:a", "libmp3lame", "-q:a", "2", tail],
                    capture_output=True, text=True
                )
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tail, "-i", segment_files[i + 1],
                     "-filter_complex",
                     "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
                     "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", overlap_path],
                    capture_output=True, text=True
                )
                final_segment_files.append(trimmed)
                final_segment_files.append(overlap_path)
                print(f"[Podcast]   Overlapped segments {i+1}+{i+2} (interrupt)", file=sys.stderr)
            else:
                final_segment_files.append(segment_files[i])
                final_segment_files.append(segment_files[i + 1])
            i += 2
        else:
            final_segment_files.append(segment_files[i])
            i += 1

    # Prepend the countdown intro to the final segment list
    with open(list_file, "w") as f:
        for sf in intro_audio_files:
            f.write(f"file '{sf}'\n")
        for sf in final_segment_files:
            f.write(f"file '{sf}'\n")

    return tmpdir, list_file, segment_files, sources, topic_names, script


def _get_segment_duration(filepath):
    """Get duration of an audio file in seconds using ffprobe."""
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", filepath],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _format_srt_time(seconds):
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def save_transcript(script, output_path, host_names=None):
    """Save the podcast script as plain text transcript.

    Args:
        script: List of {speaker, text} dicts.
        output_path: Path to write the .txt transcript.
        host_names: Dict mapping "A"/"B" to names. Defaults to Hal/Ada.
    """
    if host_names is None:
        host_names = {"A": "Hal Turing", "B": "Dr. Ada Shannon"}

    with open(output_path, "w") as f:
        for seg in script:
            name = host_names.get(seg["speaker"], seg["speaker"])
            f.write(f"{name}: {seg['text']}\n\n")
    print(f"[Podcast] Transcript saved to {output_path}", file=sys.stderr)


def generate_srt(script, segment_files, output_path, silence_duration=1.0, host_names=None):
    """Generate an SRT subtitle file from segment audio durations.

    Args:
        script: List of {speaker, text} dicts.
        segment_files: List of audio file paths (matching script order).
        output_path: Path to write the .srt file.
        silence_duration: Duration of leading silence in seconds.
        host_names: Dict mapping "A"/"B" to names.
    """
    if host_names is None:
        host_names = {"A": "Hal Turing", "B": "Dr. Ada Shannon"}

    current_time = silence_duration  # Start after silence lead-in

    with open(output_path, "w") as f:
        for i, (seg, seg_file) in enumerate(zip(script, segment_files)):
            duration = _get_segment_duration(seg_file)
            start_time = current_time
            end_time = current_time + duration

            name = host_names.get(seg["speaker"], seg["speaker"])

            f.write(f"{i + 1}\n")
            f.write(f"{_format_srt_time(start_time)} --> {_format_srt_time(end_time)}\n")
            f.write(f"[{name}] {seg['text']}\n\n")

            current_time = end_time

    print(f"[Podcast] SRT subtitles saved to {output_path}", file=sys.stderr)


def finalize_podcast(tmpdir, list_file, output_path):
    """Concatenate segments into final MP3."""
    import subprocess

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c:a", "libmp3lame", "-q:a", "2", output_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:200]}")
    print(f"[Podcast] Saved to {output_path}", file=sys.stderr)
    return output_path
