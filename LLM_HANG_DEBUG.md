# Claude-CLI Backend Hang Issue - Debug Report

## Findings

### Root Cause Identified
The claude-cli LLM backend is hanging during script generation calls:
- Occurs at **Pass 2: Concept Analysis** (Line 1161: `_concept_analysis_pass()`)
- Subprocess.run() with timeout parameter is not reliably killing stuck processes
- Claude-cli appears to be hanging or not responding to input

### Attempts to Fix
1. ✅ Fixed timeout calculation in `_call_claude_cli()` (was 1130s, now 60-120s max)
2. ✅ Added explicit `subprocess.TimeoutExpired` exception handling
3. ✅ Added try-except wrappers around Pass 2 and 2.5 with fallback structures
4. ✅ Disabled Pass 2.5a and 2.5b (local/external adversarial searches) which also hung
5. ❌ All attempts still result in hung subprocess that survives timeout

### Current State
- Pass 1-2 initialization works fine
- Process hangs at "Part 1/3: Intro + Background..." when calling `llm_call()` for script generation
- Subprocess remains hung even with 60-120 second timeout
- Even killing with SIGKILL leaves processes that need manual cleanup

### Why Timeout Isn't Working
The `subprocess.run(timeout=X)` parameter should kill the subprocess after X seconds, but:
- Claude-cli might be in an unkillable state (zombie subprocess)
- stdin/stdout buffering might be preventing timeout detection
- The process tree (claude CLI + underlying model) might not respond to SIGTERM

### Evidence
```
Query 4: ... 3 results
[Podcast]   Generating episode bible...
[Podcast]   Part 1/3: Intro + Background...
```
Log ends here. Process stuck indefinitely even with 300s timeout.

## Recommended Next Steps

### Short Term (For Next Episode)
1. Try switching to `openai` or `anthropic` backend instead of `claude-cli`
2. If claude-cli is required, add Popen with separate signal.alarm() for harder timeout
3. Implement generation bypass: use cached/pre-written scripts if LLM hangs

### Medium Term
1. Profile claude-cli's behavior with large prompts (part 1 prompt is ~15KB)
2. Check if model (opus) selection is causing slowness
3. Verify claude-cli binary is up to date

### Long Term
1. Move to streaming LLM response for script generation (handle tokens as they arrive)
2. Implement generation cancellation with proper cleanup
3. Add circuit-breaker pattern to disable hung LLM calls system-wide

## Files Changed
- `llm_backend.py`: timeout calculation + subprocess exception handling
- `elevenlabs_client.py`: Pass 2/2.5 error handling + Pass 2.5 disabled
- `podcast.py`: slug truncation (120 chars)
- `config.yaml`: anti-patterns enforcement
