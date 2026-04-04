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
        print("[LLM] openai backend: using OpenAI SDK/API", file=sys.stderr)
        return {"type": "openai", "client": openai.OpenAI(api_key=api_key)}

    if backend_type == "anthropic":
        import anthropic
        return {"type": "anthropic", "client": anthropic.Anthropic()}

    if backend_type == "claude-cli":
        return {"type": "claude-cli"}

    raise ValueError(f"Unknown llm_backend: {backend_type!r}")


def llm_call(backend, model, prompt, temperature=0.4,
             max_tokens=16000, json_mode=True):
    """Unified LLM call. Returns parsed JSON dict/list or plain text.

    Args:
        backend: Dict from get_llm_backend().
        model: Model name (backend-specific).
        prompt: User prompt string.
        temperature: Sampling temperature (ignored by claude-cli).
        max_tokens: Maximum output tokens.
        json_mode: If True, parse response as JSON with repair logic.
                   If False, return raw text string.
    """
    btype = backend["type"]

    if btype == "openai":
        raw = _call_openai(backend["client"], model, prompt,
                           temperature, max_tokens)
    elif btype == "anthropic":
        raw = _call_anthropic(backend["client"], model, prompt,
                              temperature, max_tokens)
    elif btype == "claude-cli":
        raw = _call_claude_cli(model, prompt, max_tokens)
    elif btype == "codex":
        raw = _call_codex(model, prompt, max_tokens)
    else:
        raise ValueError(f"Unknown backend type: {btype!r}")

    if not json_mode:
        return raw

    return _parse_json(raw, backend, model, prompt, temperature, max_tokens)


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

def _call_openai(client, model, prompt, temperature, max_tokens):
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if (model or "").startswith(("gpt-5", "o1", "o3", "o4")):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def _call_anthropic(client, model, prompt, temperature, max_tokens):
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _call_claude_cli(model, prompt, max_tokens):
    cmd = ["claude", "-p",
           "--output-format", "text",
           "--model", model,
           "--max-turns", "3"]
    env = {**os.environ}
    env.pop("CLAUDECODE", None)  # avoid nested session blocker
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)  # also blocks nested sessions
    # Scale timeout with prompt size and max_tokens: large prompts
    # (editorial pass, multi-paper scripts) need more time
    prompt_factor = len(prompt) // 5000 * 30  # ~30s per 5K chars
    token_factor = max_tokens // 20
    timeout = max(300, prompt_factor + token_factor + 300)
    result = subprocess.run(
        cmd, input=prompt, capture_output=True,
        text=True, env=env, timeout=timeout)
    stdout = result.stdout.strip()
    # Claude CLI may put errors on stdout instead of stderr
    if stdout.startswith("Error:") or not stdout:
        stderr_msg = result.stderr[:300] if result.stderr else ""
        raise RuntimeError(
            f"Claude CLI error (rc={result.returncode}, "
            f"timeout={timeout}s): stdout={stdout[:200]}, "
            f"stderr={stderr_msg}")
    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI error (rc={result.returncode}, "
            f"timeout={timeout}s): {result.stderr[:300]}")
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

    raise json.JSONDecodeError(
        f"All JSON parse attempts failed after retries. Last raw output: {repr(raw2[:500])}",
        raw2[:500] if raw2 else "", 0)
