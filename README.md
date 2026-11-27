# Gladeon

Offline personal assistant appliance (Phase 1): Debian 13 server, CPU-only llama.cpp, encrypted
vault, Obsidian notes via Syncthing, Android (Termux) client.

- Specification (frozen implementation baseline): `docs/SPEC_PHASE1_v2.13.md`
- Audit history: `docs/AUDIT_PHASE1_v1.md`, `docs/AUDIT_PHASE1_v2.md`
- Code: `gladeon/` (Python 3.13 standard library only)
- Tests: `python3 -m unittest discover -s tests -v`

Build status: see the "Build log" section below.

## Build log
| Step (spec §20) | Module | Status |
|---|---|---|
| 4 | `gladeon/voice/` tokenizer, normalization, router, tts_text, fixed replies (12.0–12.5) | done, tests P1–P8c, P10–P10c, P12, P13, P19 |
| 4 / 6 | `gladeon/prompts.py` system prompt, sanitizer, header tag, BRAIN/GENERAL messages, post-filter, leak check, abstention prefix rule (15.1–15.4, 8.6.4, 12.2) | done, tests P6d2, P9, P11, P11b, P45b |
| 5 | `gladeon/brain/` chunker, FTS terms, brain.db schema and writer, int8 vectors, hybrid search, watcher (13.1–13.6) | done, tests P6d, P11c, P11d, P20, P21, P22, P26, P27; calibration floors and partial flags (13.6.7–13.6.8) in `brain/calibrate.py`; EMBED client pending |
| 7 | `gladeon/proxy/` HTTP reader with 11.2 limits, bearer gate, keep-alive policy, request cache, persisted lockout, public state (6.2, 9.7, 11.2, 11.3) | done, limit/order/lockout/cache tests |
| 3 / 7 | `gladeon/engine/` llama-server client: streamed chat with deadline and cancel, mode parameters, empty retry, `/tokenize`, embeddings (8.4, 8.6) | done, tested against a fake llama-server (`tests/fake_llama.py`) |
| 6 | `gladeon/pipeline.py` + `gladeon/budget.py`: normalize → route → search → 8.7 budget → BRAIN/GENERAL answer → implicit fallback with reserve → leak check → tts; follow-up buffer (12.3); mid-stream truncation | done, end-to-end tests incl. P8c, leak block, Deep-unavailable prefix, ANSWER_TRUNCATED |
| 7 | `gladeon/proxy/server.py` + `app.py`: TLS ≥ 1.2 server, peer allowlist, queue 8, 4 workers, 3 s read deadline, keep-alive policy, endpoint dispatch, `/query` dedup via `X-Request-Id`, `/result/{id}` | done, loopback TLS tests incl. P17; controller (state + privd wiring) next |
