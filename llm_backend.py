"""Configurable LLM backend abstraction.

Supports four backends:
  - openai: OpenAI API via Python SDK
  - codex: Codex CLI subprocess (uses Codex subscription, not API credits)
  - claude-cli: Claude CLI subprocess (uses Max subscription, not API credits)
  - anthropic: Anthropic API via Python SDK

Important operational rule:
When llm_backend is "openai", use the OpenAI SDK/API path directly.
Do NOT silently reroute unattended generation work through the Codex CLI,
because CLI subscription limits and local sandbox behavior are a different
operational surface than the API.

Usage:
    from llm_backend import get_llm_backend, llm_call

    backend = get_llm_backend(config)
    result = llm_call(backend, model, prompt)            # JSON mode
    text = llm_call(backend, model, prompt, json_mode=False)  # plain text
"""
import json
import os
import re
import subprocess
import sys
import time


# Backend calls fail transiently all the time: the Claude CLI reports
# "model at capacity" or exits rc=1 with no output, Codex hits 429/403
# websocket errors, APIs return 500/503/overloaded. A single blip used
# to hard-fail a whole generation (killing an early mandatory pass) or
# truncate an episode mid-part. Retry these with backoff instead.
_TRANSIENT_PATTERNS = (
    "at capacity", "overloaded", "rate limit", "429", "too many requests",
    "500", "502", "503", "504", "connection", "reset by peer",
    "websocket", "reconnect", "temporarily", "try again",
    "timeout", "timed out",  # slow/overloaded call — retried at most once
    "rc=1",  # Claude CLI generic non-zero exit — usually environmental
)


def _is_transient_error(exc):
    msg = str(exc).lower()
    return any(p in msg for p in _TRANSIENT_PATTERNS)


def _is_timeout_error(exc):
    # Match an ACTUAL timeout ("... timeout after 600s ...", "timed
    # out"), not the "timeout=525s" value that every Claude CLI error
    # string reports. The bare substring "timeout" wrongly demoted a
    # transient rc=1 (which carries "timeout=Ns") to a single retry.
    msg = str(exc).lower()
    return "timeout after" in msg or "timed out" in msg


def _dispatch_backend(backend, model, prompt, temperature, max_tokens,
                      use_tools=True):
    """Route a prompt to the configured backend and return raw output."""
    btype = backend["type"]
    if btype == "openai":
        effective = max_tokens
        if _is_reasoning_model(model) and effective < 32000:
            effective = 32000
        return _call_openai(backend["client"], model, prompt,
                            temperature, effective)
    if btype == "anthropic":
        return _call_anthropic(backend["client"], model, prompt,
                               temperature, max_tokens)
    if btype == "claude-cli":
        return _call_claude_cli(model, prompt, max_tokens, use_tools=use_tools)
    if btype == "codex":
        return _call_codex(model, prompt, max_tokens)
    raise ValueError(f"Unknown backend type: {btype!r}")


def get_llm_backend(config):
    """Return a backend dict based on config podcast.llm_backend.

    Reads config["podcast"]["llm_backend"] (default: "openai").
    Returns a dict with "type" key and optional "client" for SDK backends.
    """
    backend_type = config.get("podcast", {}).get("llm_backend", "openai")

    if backend_type == "codex":
        return {"type": "codex"}

    if backend_type == "openai":
        try:
            import openai
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "openai backend requires the 'openai' Python package; "
                "install requirements.txt into the worker venv"
            ) from exc
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            try:
                with open(os.path.expanduser("~/.codex/auth.json")) as f:
                    api_key = json.load(f).get("OPENAI_API_KEY")
            except Exception:
                pass
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY for openai backend")
        # LOUD warning — this path spends real money on every call.
        # Reasoning models (gpt-5, o-series) with our auto-escalation
        # can burn $15-30 per podcast episode in API credits.
        # If Codex CLI is installed, prefer "llm_backend: codex" in
        # config.yaml to use the flat-rate subscription instead.
        import shutil
        codex_present = " (Codex CLI is available — consider llm_backend: codex)" if shutil.which("codex") else ""
        print(
            "[LLM] *** WARNING *** openai backend SPENDS REAL API "
            "CREDITS per call. " + codex_present,
            file=sys.stderr,
        )
        return {"type": "openai", "client": openai.OpenAI(api_key=api_key)}

    if backend_type == "anthropic":
        import anthropic
        return {"type": "anthropic", "client": anthropic.Anthropic()}

    if backend_type == "claude-cli":
        return {"type": "claude-cli"}

    raise ValueError(f"Unknown llm_backend: {backend_type!r}")


def _sanitize_prompt(prompt):
    """Drop lone surrogate codepoints that break UTF-8 encoding.

    PDF text extraction can emit unpaired surrogates; passing them to
    subprocess stdin or an HTTP client raises UnicodeEncodeError
    ("surrogates not allowed") and kills the whole generation.
    """
    return prompt.encode("utf-8", errors="replace").decode("utf-8")


def llm_call(backend, model, prompt, temperature=0.4,
             max_tokens=16000, json_mode=True, use_tools=True):
    """Unified LLM call. Returns parsed JSON dict/list or plain text.

    Args:
        backend: Dict from get_llm_backend().
        model: Model name (backend-specific).
        prompt: User prompt string.
        temperature: Sampling temperature (ignored by claude-cli).
        max_tokens: Maximum output tokens.  For OpenAI reasoning
            models (gpt-5, o1/o3/o4) this is also the budget for
            internal reasoning tokens, so the effective starting
            budget is bumped to give the model headroom.
        json_mode: If True, parse response as JSON with repair logic.
                   If False, return raw text string.
        use_tools: claude-cli only. When False the CLI runs a single
            tool-free turn — required for pure text/HTML generation so
            it emits the content instead of trying to write a file.
    """
    prompt = _sanitize_prompt(prompt)

    # Retry transient backend failures (capacity, rate limits, rc=1)
    # with exponential backoff so a momentary blip does not kill a
    # mandatory pass or truncate an episode. Timeouts are expensive to
    # re-run, so they get a single retry at most.
    backoffs = [8, 20, 45]
    raw = None
    for attempt in range(len(backoffs) + 1):
        try:
            raw = _dispatch_backend(backend, model, prompt,
                                    temperature, max_tokens,
                                    use_tools=use_tools)
            break
        except Exception as exc:  # noqa: BLE001 - classify then re-raise
            last = attempt == len(backoffs)
            timeout_exhausted = _is_timeout_error(exc) and attempt >= 1
            if last or timeout_exhausted or not _is_transient_error(exc):
                raise
            delay = backoffs[attempt]
            print(f"[llm] transient backend error "
                  f"(attempt {attempt + 1}/{len(backoffs) + 1}): "
                  f"{str(exc)[:160]}; retrying in {delay}s",
                  file=sys.stderr)
            time.sleep(delay)

    if not json_mode:
        return raw

    return _parse_json(raw, backend, model, prompt, temperature, max_tokens)


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

# Hard cap on max_completion_tokens auto-escalation. Reasoning models
# can burn enormous budgets thinking; this prevents an unbounded retry
# loop. 128K is the practical ceiling for current GPT-5 / o-family
# models.
_OPENAI_MAX_TOKENS_CAP = 128000

# Reasoning model prefixes. These models budget BOTH internal reasoning
# tokens AND output tokens against max_completion_tokens, so they need
# substantially more headroom than legacy chat models.
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model):
    return (model or "").startswith(_REASONING_MODEL_PREFIXES)


def _openai_create_once(client, model, prompt, temperature, max_tokens):
    """Single OpenAI chat completion call. Returns the choice object."""
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if not _is_reasoning_model(model):
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = max_tokens
    else:
        kwargs["max_completion_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0]


def _call_openai(client, model, prompt, temperature, max_tokens):
    """Call OpenAI with auto-escalation on length-truncation.

    Reasoning models (gpt-5, o1/o3/o4) budget BOTH internal reasoning
    tokens AND output tokens against max_completion_tokens. A complex
    prompt can burn the entire budget thinking, leaving zero output
    and finish_reason=length. When that happens we retry with double
    the budget up to _OPENAI_MAX_TOKENS_CAP. This is the difference
    between "the API failed" and "the model needs more headroom".
    """
    attempts = []
    budget = max_tokens
    is_reasoning = _is_reasoning_model(model)

    while True:
        choice = _openai_create_once(
            client, model, prompt, temperature, budget)
        content = choice.message.content
        finish = getattr(choice, "finish_reason", None)
        refusal = getattr(choice.message, "refusal", None)
        attempts.append({
            "budget": budget, "finish": finish,
            "had_content": bool(content and content.strip()),
        })

        if content is not None and content.strip():
            return content.strip()

        # Empty content. Decide whether to escalate or give up.
        # Only auto-escalate when the cause is length truncation on a
        # reasoning model — that's the recoverable case. Refusals,
        # content-filter hits, and stop-with-empty are not recoverable
        # by retrying with more tokens.
        if (is_reasoning and finish == "length"
                and budget < _OPENAI_MAX_TOKENS_CAP):
            new_budget = min(budget * 2, _OPENAI_MAX_TOKENS_CAP)
            print(f"[LLM] OpenAI hit length on {model} with "
                  f"max_completion_tokens={budget}; "
                  f"retrying with {new_budget}", file=sys.stderr)
            budget = new_budget
            continue

        parts = [f"OpenAI returned empty content (model={model}"]
        if finish:
            parts.append(f"finish_reason={finish}")
        if refusal:
            parts.append(f"refusal={refusal}")
        if len(attempts) > 1:
            parts.append(f"attempts={len(attempts)}")
            parts.append(f"final_budget={budget}")
        raise RuntimeError(", ".join(parts) + ")")


def _call_anthropic(client, model, prompt, temperature, max_tokens):
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    stop = getattr(resp, "stop_reason", None)
    if not resp.content:
        raise RuntimeError(
            f"Anthropic returned empty content (model={model}, "
            f"stop_reason={stop})")
    text = resp.content[0].text
    if text is None or not text.strip():
        raise RuntimeError(
            f"Anthropic returned empty text (model={model}, "
            f"stop_reason={stop})")
    return text.strip()


def _call_claude_cli(model, prompt, max_tokens, use_tools=True):
    import signal
    import os
    import contextlib

    # The script prompts instruct the model to read files (SOUL host
    # profiles, ANTI_PATTERNS.md, the paper context file) and every
    # tool use consumes a turn. With --max-turns 3 the CLI exited with
    # "Reached max turns (3)" before it could generate anything, so
    # every script pass silently fell back to stub content while
    # file-free calls (titles, summaries) kept working.
    # Pure-generation callers (use_tools=False) must NOT get the agentic
    # tool loop: given a prompt that asks for an HTML file, the CLI would
    # try to Write the file, hit an approval wall, and return commentary
    # ("The Write tool call needs approval") instead of the HTML — which
    # then got saved as a broken viz page. With tools disabled and a
    # single turn it just emits the requested text.
    if use_tools:
        cmd = ["claude", "-p",
               "--output-format", "text",
               "--model", model,
               "--max-turns", "25"]
    else:
        # No tools at all, but allow 2 turns: a long HTML document can
        # need more than a single inference pass to finish, which showed
        # up as "Reached max turns (1)" on the biggest transcripts. With
        # --allowedTools "" the model still cannot touch the filesystem
        # regardless of turn count, so this stays purely generative.
        cmd = ["claude", "-p",
               "--output-format", "text",
               "--model", model,
               "--max-turns", "2",
               "--allowedTools", ""]
    env = {**os.environ}
    env.pop("CLAUDECODE", None)  # avoid nested session blocker
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)  # also blocks nested sessions

    # Scale timeout with prompt size: large prompts need more time.
    # Script passes must read several files and then generate ~1000
    # words of dialogue, which routinely needs minutes, not seconds.
    prompt_factor = min(300, len(prompt) // 5000 * 45)  # ~45s per 5K chars
    timeout = max(300, prompt_factor + 300)  # 300s floor, 600s ceiling

    # Create subprocess in isolated process group using start_new_session=True
    # This prevents os.killpg() from killing the parent process
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True)  # Critical: isolate child process group

    # Child's process group ID is its PID when start_new_session=True
    child_pgid = proc.pid

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Try graceful shutdown first, then escalate to SIGKILL
        try:
            os.killpg(child_pgid, signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            # Escalate to SIGKILL
            try:
                os.killpg(child_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
        except ProcessLookupError:
            stdout, stderr = "", ""

        raise RuntimeError(
            f"Claude CLI timeout after {timeout}s (model={model}): "
            f"process terminated; stderr={stderr[:200] if stderr else '(empty)'}")
    except BaseException:
        # Clean up child on any parent-side interruption
        with contextlib.suppress(Exception):
            os.killpg(child_pgid, signal.SIGKILL)
            proc.communicate(timeout=2)
        raise

    stdout = stdout.strip()
    # Claude CLI may put errors on stdout instead of stderr
    if stdout.startswith("Error:") or not stdout:
        stderr_msg = stderr[:300] if stderr else ""
        raise RuntimeError(
            f"Claude CLI error (rc={proc.returncode}, "
            f"timeout={timeout}s): stdout={stdout[:200]}, "
            f"stderr={stderr_msg}")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Claude CLI error (rc={proc.returncode}, "
            f"timeout={timeout}s): {stderr[:300]}")
    return stdout


def _call_codex(model, prompt, max_tokens):
    import tempfile
    outfile = tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, mode="w")
    outfile.close()
    try:
        cmd = ["codex", "exec",
               "-s", "read-only",
               "--ephemeral",
               "-o", outfile.name]
        if model:
            cmd.extend(["-m", model])
        prompt_factor = len(prompt) // 5000 * 30
        token_factor = max_tokens // 20
        timeout = max(300, prompt_factor + token_factor + 300)
        result = subprocess.run(
            cmd, input=prompt, capture_output=True,
            text=True, timeout=timeout)
        if result.returncode != 0:
            stderr_lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
            stderr_tail = "\n".join(stderr_lines[-8:]) if stderr_lines else ""
            stdout_tail = (result.stdout or "").strip()[-300:]
            details = stderr_tail or stdout_tail or "Codex CLI returned non-zero exit status"
            raise RuntimeError(
                f"Codex CLI error (rc={result.returncode}, "
                f"timeout={timeout}s): {details}")
        output = open(outfile.name).read().strip()
        if not output:
            raise RuntimeError(
                "Codex CLI returned empty output")
        return output
    finally:
        try:
            os.unlink(outfile.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# JSON parsing with repair (extracted from elevenlabs_client._llm_json)
# ---------------------------------------------------------------------------

def _extract_json_block(text):
    """Extract the outermost JSON object or array using brace/bracket matching."""
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
    return None


def _parse_json(raw, backend, model, prompt, temperature, max_tokens):
    """Parse JSON from LLM output, with progressive repair attempts."""
    raw = raw if isinstance(raw, str) else ("" if raw is None else str(raw))

    # Empty / whitespace-only output is a common transient backend failure.
    # Surface a clear error instead of the opaque JSON "char 0" failure.
    if not raw.strip():
        raise RuntimeError(
            "LLM returned empty output while JSON was required"
        )

    # Strip markdown code fences
    result = re.sub(r"^```(?:json)?\n?", "", raw, flags=re.MULTILINE)
    result = re.sub(r"\n?```\s*$", "", result, flags=re.MULTILINE).strip()
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        pass

    # Try extracting the outermost JSON block (handles surrounding text)
    block = _extract_json_block(result)
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            # Try fixing trailing commas in the extracted block
            fixed_block = re.sub(r',\s*([}\]])', r'\1', block)
            try:
                return json.loads(fixed_block)
            except json.JSONDecodeError:
                pass

    print("[LLM] Warning: JSON parse error, attempting repair...",
          file=sys.stderr)
    fixed = result
    # Remove trailing commas before closing brackets
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Try adding closing brackets for truncated output
    base = block or fixed
    for suffix in ['"}]', '"]', '"}]}', ']', '}}', '}', '"}}', '"}']:
        try:
            return json.loads(base + suffix)
        except json.JSONDecodeError:
            continue

    # Last resort: retry the LLM call with stricter instructions
    print("[LLM] Warning: JSON repair failed, retrying LLM call...",
          file=sys.stderr)
    print(f"[LLM] Debug: raw output first 500 chars: {repr(raw[:500])}",
          file=sys.stderr)
    print(f"[LLM] Debug: raw output last 200 chars: {repr(raw[-200:])}",
          file=sys.stderr)

    # Try up to 2 retries with increasingly strict instructions
    raw2 = raw
    for retry_num in range(2):
        retry_prompt = (prompt +
                        "\n\nIMPORTANT: Output valid JSON only. "
                        "No markdown code fences. No trailing text. "
                        "No ```json blocks. Raw JSON only.")
        btype = backend["type"]
        if btype == "openai":
            raw2 = _call_openai(backend["client"], model, retry_prompt,
                                temperature, max_tokens)
        elif btype == "anthropic":
            raw2 = _call_anthropic(backend["client"], model, retry_prompt,
                                   temperature, max_tokens)
        elif btype == "claude-cli":
            raw2 = _call_claude_cli(model, retry_prompt, max_tokens)
        elif btype == "codex":
            raw2 = _call_codex(model, retry_prompt, max_tokens)
        else:
            raise ValueError(f"Unknown backend type: {btype!r}")

        raw2 = raw2 if isinstance(raw2, str) else ("" if raw2 is None else str(raw2))
        if not raw2.strip():
            print(f"[LLM] Retry {retry_num + 1} returned empty output.",
                  file=sys.stderr)
            continue

        result2 = re.sub(r"^```(?:json)?\n?", "", raw2, flags=re.MULTILINE)
        result2 = re.sub(r"\n?```\s*$", "", result2, flags=re.MULTILINE).strip()
        result2 = re.sub(r',\s*([}\]])', r'\1', result2)

        try:
            return json.loads(result2)
        except json.JSONDecodeError:
            # Try brace-matched extraction
            block2 = _extract_json_block(result2)
            if block2:
                try:
                    return json.loads(block2)
                except json.JSONDecodeError:
                    try:
                        return json.loads(re.sub(r',\s*([}\]])', r'\1', block2))
                    except json.JSONDecodeError:
                        pass
            print(f"[LLM] Retry {retry_num + 1} failed. Output: {repr(result2[:300])}...",
                  file=sys.stderr)

    sample = raw2[:500] if raw2 else ""
    raise RuntimeError(
        "All JSON parse attempts failed after retries. "
        f"Last raw output: {repr(sample)}"
    )
