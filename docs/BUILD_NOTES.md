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
2. **Phone file layout.** 3.3.7 says the bundle helper writes `~/.config/gladeon/{cert.pem,token,pin}`. The scripts use `~/.gladeon/{config,cert.pem,auth.header}` (override with `GLADEON_DIR`). `auth.header` holds `Authorization: Bearer <token>` so curl reads the token from a file (`-H @file`) and it never appears in argv. The bundle importer still has to be written to this layout.
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
- Swap-mode DEEP loading with the 202 "Deep loading" reply (8.3).
