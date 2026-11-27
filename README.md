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
