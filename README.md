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
| 2 | `gladeon/privd/` wire protocol, SO_PEERCRED peer check, per-verb ACL, verb lock with `PRIVD_BUSY`, floor mutex, lock-free verbs, client; clock floor and persistent correction budget (5.2.3–5.2.7, 5.2.17, 10.6) | done, tests P29–P29d plus ACL/locking; cryptsetup/mount/systemd verbs written in `privd/vault.py` + `privd/main.py`, tested against a fake system (command order, unwinds, LOCK_REQUESTED, client gone at reply, busy umount); real-device test needs the PC |
| 8 | `phone/` Termux scripts: ask, unlock, lock, status, shutdown; pinned TLS, retry on the other IP, `/result` polling, control-character stripping (11.9–11.10) | done, run with bash against the real TLS proxy in `tests/test_phone.py` |
| 7 / 9 | `gladeon/proxy/controller.py`: unlock (lockout, privd `vault-open`, recovery checks, watcher + indexer start), lock steps 1–7 with BRAIN cache replacement, lock-during-unlock, LOCK_FAILED retry, auto-lock, indexer gate, `/status` | done, integration tests with a fake privd daemon + fake llama + real brain (P14 cache rule) |
| 7 | `gladeon/proxy/main.py` + `gladeon/hygiene.py`: entry point, `READY=1` after the listener, watchdog liveness rule, engine health polling, auto-lock and floor-tick timers, SIGTERM shutdown path; stderr sink and exception hooks | done, process smoke test (`tests/test_main.py`) checks nothing reaches stderr |
| 7 | `Controller.reconcile()` (6.3.5): adopt with saved `last_activity`, lock when missing, resume a lock intent, close after a still-running `vault-open` (retrying `PRIVD_BUSY`), close a half-open mapping | done, tests P23, P23d-like |
| 3 / 19 | `gladeon/hwprobe.py`: read-only probe of CPU (cores, P/E types, L3 groups, ISA), RAM, zram, TPM 2.0, Secure Boot, disks, NICs; model tier (8.2) and RAM plan (8.3); writes the `docs/hardware.md` draft. Run on the PC: `python3 -m gladeon.hwprobe` | done, fake-sysfs tests |
| 5 / 9 | `gladeon/brain/maintenance.py`: snapshots via the Online Backup API (256-page steps, checks, keep 3, skip if unchanged / untrusted clock / low disk), daily vector GC + failed retry + integrity check, calibration runner; wired into `Controller.maintenance_tick()` | done, tests incl. a full calibration run |
| 2 / 10 | `gladeon/privd/synccheck.py`: Syncthing pre-start check (discovery/relays/NAT/reporting off, GUI on 127.0.0.1 with a password, one non-introducer device, one receive-only folder `gladeon-notes`, ignorePerms, 90-day staggered versioning) and pending-device apply (14.4) | done |
| 17 | `tests/test_golden.py`: more §17 cases without the PC: P6c, P6e, P6f, P6g, P6h, P6i, P10d, P15, P18, P32, P44, P45c | pass |
| 1 | `gladeon/deploy.py`: renders nftables ruleset, `.link`/`.network` files, sysctl, logind, coredump, journald, proxy/privd/llm units, vault mount unit, Wi-Fi tune unit, llama-server args (4.2, 4.4, 7.2–7.6, 8.4) | done, unit-file parse and key tests; `nft -c` runs where nft exists |
