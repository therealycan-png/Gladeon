# Build notes (Phase 1, built against spec v2.13)

The build log is in `README.md`. This file covers three things: where each part of the spec lives in the code, every place the code differs from v2.13 and why, and what still needs the PC.

Run all tests: `python3 -m unittest discover -s tests -v` (Python 3.13, standard library only; the phone tests need `curl`, `jq`, `openssl`).

## Code map

| Spec | Code |
|---|---|
| 12.0–12.5 voice: tokenizer, normalization, router, tts_text, fixed replies | `gladeon/voice/` |
| 15, 8.6.4 prompts, sanitizer, header tag, post-filter, leak check | `gladeon/prompts.py` |
| 8.7 context budget, transcript cut, mid-stream truncation | `gladeon/budget.py` |
| 12.2–12.3 full query flow, implicit fallback, follow-ups | `gladeon/pipeline.py` |
| 8.4, 8.6 llama-server client | `gladeon/engine/` |
| 13.1–13.6 chunker, FTS terms, brain.db, hybrid search, watcher | `gladeon/brain/{chunker,fts,index,watcher}.py` |
| 13.6.7–13.6.8 calibration, partial flags | `gladeon/brain/calibrate.py` |
| 13.2.6, 13.7, 14.2, 11.6 daily work, snapshots, disk floor | `gladeon/brain/maintenance.py` |
| 11.1–11.4 TLS server, limits, auth, dedup, endpoints | `gladeon/proxy/{httpcore,gate,server,app,reqcache}.py` |
| 6, 9.4–9.8, 10, 11.5, 6.3.5 state, vault lifecycle, lockout, auto-lock, clock, /status, reconciliation | `gladeon/proxy/{state,lockout,controller}.py` |
| 7.1–7.2 entry point, watchdog, output hygiene | `gladeon/proxy/main.py`, `gladeon/hygiene.py` |
| 5.2 privd protocol, ACL, locking, vault verbs, clock budget, Syncthing check | `gladeon/privd/` |
| 5.3 admin helpers | `gladeon/admin/common.py` |
| 4.2, 4.4, 7.2–7.6, 8.4 system files | `gladeon/deploy.py` |
| 3, 8.2, 8.3 hardware probe, model tier, RAM plan | `gladeon/hwprobe.py` |
| 11.9–11.10 phone scripts | `phone/` |

## Differences from v2.13

1. **Request ID header.** 11.3 says requests carry a UUIDv4 but does not say how. The code uses the header `X-Request-Id`. `/query` without a valid UUIDv4 there gets 400.
2. **Phone file layout.** 3.3.7 says the bundle helper writes `~/.config/gladeon/{cert.pem,token,pin}`. The scripts use `~/.gladeon/{config,cert.pem,auth.header}` (override with `GLADEON_DIR`). `auth.header` holds `Authorization: Bearer <token>` so curl reads the token from a file (`-H @file`) and it never appears in argv. `gladeon-admin client-bundle` and the phone's `gladeon-bundle` use this layout (tested end to end).
3. **Chunk header always has four fields.** 13.4.6 shows `[rank_date | Title | H1 > H2 | first sentence]`. When there is no heading or no first sentence the code still writes the empty field (`[d | t |  | s]`), so the breadcrumb can be read back from the stored header without guessing. Prompts are unaffected (they render their own header without the first sentence).
4. **Event date line for notes whose first section is empty.** 13.4.6 puts `Event date:` in "the first chunk's body". When the text before the first heading is empty, the line goes into the first non-empty section. A note with no body at all gets one chunk with only that line.
5. **Stable-file rule.** 13.3.2 says "stable 30 s". The watcher counts a file as stable when it has been seen unchanged for 30 s **or** its mtime is at least 30 s old. Without the second rule, every note would wait 30 s after each unlock before it could be indexed. Syncthing writes to a temp file and renames, so an old mtime on a final `.md` name means the file is complete.
6. **Passphrase and transcript never in argv.** 9.2 shows `printf | jq -Rs`. The first version of `gladeon-unlock` used `jq --arg`, which puts the passphrase in jq's command line. This is fixed. The transcript and the spoken answer also go through stdin now (`termux-tts-speak` reads stdin).
7. **Busy rule location.** 8.5 says a second interactive request waits at most 5 s, then 503 BUSY. The wait is on the pipeline lock, so it also covers fixed-reply routes. This is stricter than needed but simpler.
8. **Local command replies.** 12.2.1 names the commands but 12.5 has no text for them. The code says "Locking the vault.", "It's 3:05 PM." and "Status: full ready.".

## Not built yet (needs the PC or a decision)

- Real-device runs of privd's cryptsetup, fsck, mount and systemd calls (written and tested against a fake system only).
- Provisioning: BIOS, disks, LUKS root with TPM, dropbear, GRUB lock, users, sudoers (section 3, 5.1).
- Model choice and the model gate (section 18); llama.cpp build and flags (8.1) [VERIFY items].
- `gladeon-admin` command-line verbs (only the helpers exist), offline copy, vault rotation, versions-recent.
- Pre-flight checks that read the live system (19.1 items 1–3, 5, 8–11; 19.2).

## Measurements taken here (not on Shawn's PC)

- **Vector scan gate (13.5.6).** `scan_gate(768)` on this cloud container (Xeon, 4 vCPU, Python 3.13): **6,000 chunks** within 150 ms, just above the spec's 5,000 floor. At ~3 chunks per note that is about 2,000 notes before older notes become FTS-only. If Shawn's PC is similar, two options: a smaller embedding dimension (Matryoshka truncation to 256 dims, 13.5.5, a new `emb_key`), which should roughly triple the ceiling, or a small C scan routine. Re-measure on the PC first.

## Code review, round 1 (after the build)

A review of everything from the brain module onward found 10 issues. All are fixed, each with a regression test:

1. Chat streams broke after 5 s of silence. A timed-out socket file cannot be read again, so a DEEP prompt that took more than 5 s before its first token failed. The stream reader now uses select() on the raw socket and its own buffer, and handles chunked encoding.
2. Notes deleted while the vault was locked (or the proxy was down) were never removed from the index. Database-only paths now go through the same "missing in two scans" rule.
3. Note text from search hits stayed in proxy memory after lock, and that cache grew forever. Hits now live for one request; only the follow-up's hits are kept, and lock wipes them.
4. During a DEEP swap, QUICK requests that need a chat engine did not get 503 LOADING. The pipeline now raises it after routing, and it releases its lock while DEEP loads, so fixed replies are still answered.
5. A failing checkpoint or close of the database stopped the lock sequence, leaving the vault open in LOCKING. Both errors are now logged and the lock carries on.
6. Snapshots ran with an untrusted clock. They now respect `clock_trusted`.
7. `/query` held the connection up to 205 s, longer than the phone's 200 s curl limit, so slow answers pinned all 4 workers. It now answers 202 after 25 s and the phone polls. `retry_window_s` follows the 8.6.3 formula.
8. Two requests with the same ID could both run (check-then-insert race). Registration is now atomic and the cache has a lock.
9. The Syncthing check rejected real configs, because Syncthing lists the PC's own device too. The local device ID is now passed in and kept.
10. `/preflight` compared the clock with floor 0. It now uses privd's floor.

The review also noticed that about 50 `__pycache__` files had been committed. They are removed and ignored.

## Code review, round 2 (on the round-1 fixes)

The second review looked only at the round-1 fix commit and found 7 problems inside those fixes:

1. A DEEP notes question lost its search hits during a model swap, because the swap wiped the pipeline and the hits lived on the shared object. Fixed at the root: a request's hits now live in a local dict passed down the call chain.
2. While DEEP loaded, another request could overwrite the shared hits and follow-up, and two DEEP requests could both start a load. The follow-up is snapshotted before the lock is released, and a DEEP load lock makes a second request wait for the first load.
3. `retry_window_s` raised KeyError when `load_seconds` lacked a key. It now uses defaults, and config values merge over them.
4. A snapshot skipped for an untrusted clock waited a full day. It now runs as soon as the clock is trusted.
5. A refused request was briefly registered, so a quick retry could get 202 and then 404. Refusals are no longer registered.
6. The stream reader ignored Content-Length, and could wait out the deadline on a non-streamed reply. It now reads exactly Content-Length bytes, limits chunked lines, and hands over partial data on early EOF.
7. `/preflight` passed the clock check when privd was unreachable. It now fails it.

## Code review, round 3 (on the round-2 fixes)

Round 3 found 8 more problems, nearly all in the DEEP-load concurrency added in rounds 1 and 2: note text that could outlive a lock, a second waiter getting no QUICK window, follow-up turns lost or mixed, and an unlocked follow-up reset. The cause was the design itself: the request released the pipeline lock halfway and the code then patched shared state around that.

**Replaced with a simpler design.** A DEEP request that needs the engine loaded now gives up everything it holds (hits, follow-up, note text), releases the lock, waits for the load, and then **re-runs from scratch**. Routing is deterministic, so the re-run is correct. A lock during the load is honored naturally (the re-run sees a locked vault and answers "Vault is locked."), and no note text is held across the wait. A test now asserts that the password never reaches the engine when the vault is locked mid-load.

Also fixed in this round: Content-Length bodies are read line by line, with partial data kept on early EOF; chunk sizes are bounded before buffering; a failing snapshot is tried once a day with a `SNAPSHOT_FAILED` warning; and every DEEP waiter gets the loading notice.

## Code review, round 4 (on the redesign)

Round 4 found 9 issues. **None of them were note-text or security leaks** (rounds 1–3 had those), which suggests the redesign converged. They were liveness and edge cases, all fixed with tests: DEEP was unloaded while still in use; a promised re-run could end in BUSY behind a long answer; the follow-up decision changed across a long load; the swap's follow-up wipe could be undone in a small window; `SNAPSHOT_FAILED` never cleared and a failed snapshot waited a day (now retried hourly); the loading notice could be stale; a big chunk of many short lines was refused; and the Content-Length scan was O(n²).

| Round | Scope | Findings | Leaks of note text |
|---|---|---|---|
| 1 | whole build (brain onward) | 10 | 1 (hits kept after lock) |
| 2 | round-1 fixes | 7 | 1 (hits lost or kept across DEEP wait) |
| 3 | round-2 fixes | 8 | 1 (note text after lock during DEEP load) → redesign |
| 4 | redesign | 9 | 0 |
| 5 | round-4 fixes | 8 (2 important, both only with two DEEP requests at once, which the phone's one-command rule prevents) | 0 |

Round 5 fixes: a promised re-run now goes ahead of new requests (a condition-variable handoff) and its deadline counts from when it gets the lock (a small, deliberate change from 8.6.3's "from DEEP READY", so a queued DEEP request is never answered "Out of time"); the idle unload checks and switches while holding the pipeline lock; a re-run ignores a follow-up that another request changed during the load (generation counter); the loading notice is sent only when a load is running; and Content-Length lines are capped.
