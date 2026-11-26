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
