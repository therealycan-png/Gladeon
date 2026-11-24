# Gladeon Phase 1 v2: Audit Log

Process (spec §22): three fresh auditors per round (A Linux/reliability, B security/privacy, C AI/retrieval/data). The editor triages each finding as accepted, rejected (with reason) or [VERIFY]. A round is clean when it yields zero accepted defects. The gate passes after three consecutive clean rounds.

| Round | Spec revision audited | Raw findings (A/B/C) | Unique after merge | Accepted | Rejected | Clean? |
|---|---|---|---|---|---|---|
| 1 | 2.0 (`SPEC_PHASE1_v2.md`) | 61 (19/18/24) | 53 | 53 | 0 | No |

Clean-round streak: **0 / 3**.

---

## Round 1 (spec rev 2.0 → fixes in rev 2.1)

Duplicates merged: A-2 = B-4; A-3 + A-4 ≈ B-5; A-6 = B-2; A-9 ≈ B-3; A-12 = B-18; A-14 ≈ B-9; A-18 = C-23.

All findings were accepted. The fix location in rev 2.1 is tagged in the spec text as `(R1: <id>)`.

### Auditor A — Linux platform and reliability (5 HIGH, 14 MEDIUM)
| ID | Sev | Finding | Fix in 2.1 |
|---|---|---|---|
| A-1 | HIGH | Proxy restart forgets a mounted vault; no auto-lock; unlock fails | 6.3.5 reconciliation, 5.2.7 idempotent verbs, P23 |
| A-2 | HIGH | Privileged pre-flight checks cannot run as `gladeon` | 5.2.11, 19.2 privd `selfcheck` |
| A-3 | HIGH | Failed TPM unseal blocks headless boot | 0.3 exception, 3.2.4 dropbear, 3.2.6 regen rule |
| A-4 | HIGH | PCR 7 only: whole stolen PC can read root | 3.1.3, 3.2.5 GRUB lock, 9.9.4, 16 |
| A-5 | HIGH | `wlan0` name does not exist on Debian 13 | 4.2.1 `.link` names |
| A-6 | MED | ERROR vault state is a dead end and shown as locked | 6.1–6.3, `VAULT_LOCK_FAILED` |
| A-7 | MED | Unit ordering, stop timeout, fail-open firewall | 4.4, 5.2.6, 7.1.3, 7.2 |
| A-8 | MED | Bind mount of the mountpoint may keep the device busy [VERIFY] | 7.2 parent path, P25 |
| A-9 | MED | Clock floor written from an untrusted clock | 10.3, 10.6, 3.2.10 |
| A-10 | MED | `hwclock` package missing | 3.3.2 |
| A-11 | MED | Argon2 memory not in RAM plan; exit 3 unmapped | 8.3, 9.1.1, 9.6 |
| A-12 | MED | Offline LV copy while mounted is inconsistent | 14.3 |
| A-13 | MED | Non-atomic state files can crash-loop the proxy | 5.4, 7.2 |
| A-14 | MED | Journal collects Syncthing/llama output | 5.2.3, 7.4.3 |
| A-15 | MED | Start-limit hit blocks `/engine/restart`; backoff claimed but absent | 5.2.10, 8.4 |
| A-16 | MED | ifupdown left in place, networkd not enabled | 3.3.3, 19.2.6 |
| A-17 | MED | Two NICs on one subnet, no metrics, ARP flux | 4.2.3 |
| A-18 | MED | SQLite library file name inconsistent | 3.4, 13.1 |
| A-19 | MED | `/boot` fsck failure drops to emergency mode | 3.2.1, 19.2.4 |
| Notes 1–5 | – | ref typo; static mount unit; panic/watchdog/OOM; zram size, `f_bavail`; netlink for privd | all applied (7.5, 3.2.8–9, 5.2.1, 11.6) |

### Auditor B — security and privacy (6 HIGH, 12 MEDIUM)
| ID | Sev | Finding | Fix in 2.1 |
|---|---|---|---|
| B-1 | HIGH | Chat-template control tokens in notes become real role tokens | 8.2 `control_tokens`, 15.2, P11b, pre-flight 19.1.9 |
| B-2 | HIGH | ERROR dead end (merged with A-6) | 6.x |
| B-3 | HIGH | Phone epoch moves the clock without bound: lockout bypass, version loss | 9.7, 10.5–10.8, P29 |
| B-4 | HIGH | Security pre-flight checks cannot run unprivileged (= A-2) | 19.2 |
| B-5 | HIGH | PCR 7 + editable GRUB: stolen PC root shell | 3.1.3, 3.2.5, 16 |
| B-6 | HIGH | Phone STT/TTS may use cloud services | 2, 11.11, 19.3 |
| B-7 | MED | Keep-alive lets slow clients pin workers | 11.2.4, 11.2.8, P16b |
| B-8 | MED | `read -s` alters passphrase; setup path undefined | 5.3, 9.1.1, 9.2 |
| B-9 | MED | Journal collects vault paths (≈ A-14) | 7.2 excepthooks, 7.4.3, P28 |
| B-10 | MED | Answers in Android notifications leak notes | 11.10 |
| B-11 | MED | `vault-setup` could reformat an existing vault | 5.2.9 |
| B-12 | MED | No passphrase floor; offline guessing | 5.2.9, 9.1.1, 16 |
| B-13 | MED | Stolen phone SSH → sudo; revocation incomplete | 5.1, 16 runbook |
| B-14 | MED | Token/pin bootstrap undefined | 3.3.7 |
| B-15 | MED | Syncthing unsandboxed, home on root | 5.1, 14.4 |
| B-16 | MED | Delimiter neutralizing ASCII-only, header not covered | 15.2, P11 |
| B-17 | MED | P15 never reaches the queue-full path | P15 (16 connections) |
| B-18 | MED | Offline copy while open (= A-12) | 14.3 |
| Notes 1–5 | – | check order; header backup to tmpfs; phone-side Syncthing; jq not eval; publisher hashes, `O_NOFOLLOW` | all applied (11.2.7, 9.1.3, 14.4, 11.10, 3.3.5, 13.3.2) |

### Auditor C — AI, retrieval and data (6 HIGH, 18 MEDIUM)
| ID | Sev | Finding | Fix in 2.1 |
|---|---|---|---|
| C-1 | HIGH | Follow-up stickiness could bypass finance/personal/locked guards | 12.2 order, 12.3, P10b |
| C-2 | HIGH | BRAIN follow-up has no retrieval query or prompt slot | 12.3.3, 15.3, P10 |
| C-3 | HIGH | Stopwords pass the FTS gate | 13.6.1, 13.6.3, P6 corpus |
| C-4 | HIGH | On-demand DEEP needs the same peak RAM | 8.3 resident/swap/unavailable |
| C-5 | HIGH | FTS orphans + rowid reuse break re-indexing | 13.2 AUTOINCREMENT + delete rule, P20 |
| C-6 | HIGH | Tagalog word order missed by personal-reference patterns | 12.2.3, P5 |
| C-7 | MED | Abstention stated as fact while index incomplete | 12.2.3 "still reading", P6b |
| C-8 | MED | Local commands not anchored | 12.2.1, P19 |
| C-9 | MED | Empty check before echo/mode strip | 12.1 order, P2b |
| C-10 | MED | Finance guard false positives/negatives | 12.2.2, P7, P8 |
| C-11 | MED | Implicit route abstains on general knowledge | 12.2 fallback, M7 |
| C-12 | MED | EMBED model change mixes vector spaces; floors per model | 13.5.7, 13.6.7, `meta` |
| C-13 | MED | Rename reuses stale headers | 13.2 `text_hash` vectors, 13.3.7, P22 |
| C-14 | MED | Unsplittable tables vs size cap; embed `-ub` | 13.4.5, 13.4.8, 8.4, P27 |
| C-15 | MED | Obsidian frontmatter forms not parsed | 13.4.1–2, P26 |
| C-16 | MED | `-c` not tied to prompt budget; wrong tokenizer | 8.7 |
| C-17 | MED | RSS ceiling too low | 8.2, 8.6.6 |
| C-18 | MED | Timeout leaves slot busy (non-streaming) [VERIFY] | 8.6.1 streaming |
| C-19 | MED | QUICK and DEEP can generate at once on the same cores | 8.5.4, 11.7.3 |
| C-20 | MED | 150 ms scan target likely missed at higher dims | 13.5.6 scan gate |
| C-21 | MED | DEEP loading flow does not fit the API | 11.4, 11.9.3, 8.6.3 |
| C-22 | MED | P12 has no expected strings | 12.4, P12 |
| C-23 | MED | SQLite file name (= A-18) | 13.1 |
| C-24 | MED | Skipped files keep stale chunks | 13.3.5, P21 |
| Notes 1–5 | – | nonce out of system prefix; repeat_penalty 1.0 on BRAIN; embed query slot; fixed seed + k-of-n; more currency forms | all applied (15.3, 8.6.2, 8.4, 18, P12) |
