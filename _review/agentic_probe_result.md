# Agentic Probe Result

**Date:** 2026-07-15
**Risk:** med

## Gates Results

### Gate 1: py_compile
- [x] `cc_rotate_claude_provider.py` - OK
- [x] `claude_a2a_wrapper.py` - OK

### Gate 2: `agentic-probe`
- GLM 3 channels: `agentic-ok`
- k40/100xlabs 4 channels: `down` (503)

### Gate 3: `try-next --agentic`
- All candidates unhealthy -> skipping rotation (exit 2)

### Gate 4: Unit tests - truncation detection
All 9 tests pass:
1. Strong truncation signal: trigger (PASS)
2. Short prompt ("reply OK"): not trigger (PASS)
3. Too slow (30s): not trigger (PASS)
4. Output too long (600 chars): not trigger (PASS)
5. Has code block: not trigger (PASS)
6. Has file path: not trigger (PASS)
7. Has ## header: not trigger (PASS)
8. Prompt too short: not trigger (PASS)
9. CLAUDE_TRUNC_DETECT=0: not trigger (PASS)

### Gate 5: No full token leakage
- [x] Probe output only shows ...xxxxxx (last 6 chars)

## Deliverables

### 1. scripts/cc_rotate_claude_provider.py
- _agentic_probe_single(): sends request with dummy get_weather tool
- cmd_agentic_probe(): probes all channels, prints table
- try-next --agentic: only switches to agentic-ok channels
- Metrics: data/agentic_probe_metrics.jsonl

### 2. claude_a2a_wrapper.py
- _detect_truncation(): 4-condition conservative check
  - Duration < CLAUDE_TRUNC_TIMEOUT_SECS (default 20s)
  - Output length < CLAUDE_TRUNC_MIN_CHARS (default 500)
  - No tool trace in output
  - Prompt length >= CLAUDE_TRUNC_PROMPT_LEN (default 200)
- Integrated into execute_claude(), raises channel error on detection
- Env switch: CLAUDE_TRUNC_DETECT=0 disables
- Thresholds adjustable via env vars

### 3. Metrics
- Probe: agentic_probe_metrics.jsonl (ts, channel, status, reason)
- Truncation: claude_metrics.jsonl (truncated=true, trunc_channel)

## Compatibility
- [x] cc-switch DB write path untouched
- [x] CLAUDE_CHANNEL_FAILOVER / CLAUDE_FALLBACK_* intact
- [x] try-next original semantic preserved
- [x] Pin coordination intact
- [x] Lock + atomic write intact
- [x] Task-level timeout does not trigger rotation
