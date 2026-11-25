# Gladeon Phase 1 v2: Audit Log

Process (spec §22): three fresh auditors per round (A Linux/reliability, B security/privacy, C AI/retrieval/data). The editor triages each finding as accepted, rejected (with reason) or [VERIFY]. A round is clean when it yields zero accepted defects. The gate passes after three consecutive clean rounds.

| Round | Spec revision audited | Raw findings (A/B/C) | Unique after merge | Accepted | Rejected | Clean? |
|---|---|---|---|---|---|---|
| 1 | 2.0 (`SPEC_PHASE1_v2.md`) | 61 (19/18/24) | 53 | 53 | 0 | No |
| 2 | 2.1 (`SPEC_PHASE1_v2.1.md`) | 54 (17/17/20) | 49 | 49 | 0 | No |
| 3 | 2.2 (`SPEC_PHASE1_v2.2.md`) | 45 (14/15/16) | 37 | 37 | 0 | No |
| 4 | 2.3 (`SPEC_PHASE1_v2.3.md`) | 32 (12/6/14) | 29 | 29 | 0 | No |
| 5 | 2.4 (`SPEC_PHASE1_v2.4.md`) | 17 (4/3/10) | 16 | 16 | 0 | No |
| 6 | 2.5 (`SPEC_PHASE1_v2.5.md`) | 21 (5/8/8) | 18 | 18 | 0 | No |
| 7 | 2.6 (`SPEC_PHASE1_v2.6.md`) | 24 (9/10/5) | 19 | pending | pending | No |

Clean-round streak: **0 / 3**. Current revision: 2.6 (`SPEC_PHASE1_v2.6.md`, self-contained). **Loop paused by Shawn after round 7 (2026-11-25); round 7 findings logged but not yet applied.**

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

---

## Round 2 (spec rev 2.1 → fixes in rev 2.2)

Duplicates merged: B2-4 = A2-6; B2-6 = A2-13; B2-17 = C2-12; B2-10 = C2-16 (+ A2 note 5); C2-20 ⊂ B2-9. All accepted; fixes tagged `(R2: id)` in rev 2.2. Round 2 found mostly gaps in round 1 fixes (A-1, A-15, A-17, A-19, B-1, B-3, C-1, C-6, C-10, C-14, C-20, C-21) plus new areas (IPv6, update window, phone replacement, privd protocol).

| ID | Sev | Finding | Fix in 2.2 |
|---|---|---|---|
| A2-1 | HIGH | dropbear host-key clash, stale keys in initramfs, `ip=` naming | 3.2.4, 19.2.7, 19.3 |
| A2-2 | HIGH | copy-lock has no lifetime | 5.2.13 transient unit, 14.3 |
| A2-3 | MED | adoption reopens a locked vault; embed/sync not restarted | 5.2.8 `lock_intent`, 6.3.5, P23c |
| A2-4 | MED | start limit counts deliberate starts | 5.2.11, P36 |
| A2-5 | MED | route metrics ineffective; ARP claim wrong | 4.2.3, 19.3 |
| A2-6 | MED | phone loss needs a console (= B2-4) | 0.3, 3.3.7 offline admin key, 5.3 `phone-replace`, 16 |
| A2-7 | MED | root fsck failure stops headless boot | 0.3, 3.2.1 |
| A2-8 | MED | watchdog cannot see a dead listener | 7.2 heartbeat rule, P31 |
| A2-9 | MED | Syncthing perms can lock the watcher out | 14.4 `ignorePerms`, P32 |
| A2-10 | MED | no network procedure for provisioning/updates | 3.3.3, 3.3.8 update window |
| A2-11 | MED | Syncthing runs while the clock is untrusted | 9.4.4, 10.7 |
| A2-12 | MED | networkd has no hooks | 4.2.4 wifi-tune unit |
| A2-13 | MED | IPv6 unfiltered (= B2-6) | 4.2.5, 4.4 |
| A2-14 | MED | `nofail` can hide an unmounted /boot | 3.2.1, 19.2.4 |
| A2-15 | MED | privd can hit its start limit | 5.2.1 |
| A2-16 | MED | P25 misses the namespace case | 5.2.12, P25b |
| A2-17 | MED | OpenBLAS build package missing | 3.3.2 |
| A2 notes | – | shutdown ordering; stop timeouts; AF_ALG/sshd order; config check before start; calibration file on root | 6.3.8, 7.4.3, 5.2.1, 7.1.3, 9.4.4, 13.6.7 |
| B2-1 | HIGH | 24 h bound per request, not cumulative | 10.6 per-boot budget, 9.6 code 22, P29b |
| B2-2 | MED | BRAIN answers finishing after lock get cached | 9.5.3, P14 |
| B2-3 | MED | restart resets auto-lock timer | 5.4, 9.8, 6.3.5, P23b |
| B2-4 | MED | stolen-phone runbook not executable | (= A2-6) |
| B2-5 | MED | token rotation not applied live | 5.3, 11.2.10, P30 |
| B2-6 | MED | IPv6 link-local SSH (= A2-13) | 4.2.5, 4.4 |
| B2-7 | MED | passphrase change does not revoke; header backup staged on disk | 5.3, 9.1.3 |
| B2-8 | MED | privd protocol/ACL insufficient | 5.2.2–5.2.5, 5.2.12, 9.6 code 21 |
| B2-9 | MED | malicious future date wins conflicts; regex bypass | 13.2, 13.4.2, 15.2.4, P11, P11c |
| B2-10 | MED | calibration file on root (= C2-16) | 13.6.7 |
| B2-11 | MED | keyboard/clipboard learning and sync | 3.3.7, 11.11.3 |
| B2-12 | MED | app-private storage in cloud backup | 11.11.4 |
| B2-13 | MED | PC Send & Receive can overwrite the phone's notes | D-15, 14.4 receive-only/Send Only |
| B2-14 | MED | offline-copy device not validated | 14.3, P34 |
| B2-15 | MED | pin format and cert delivery wrong for curl | 3.3.7 |
| B2-16 | MED | peer-credential self-test cannot fail | 19.2.3 |
| B2-17 | MED | USER_DEFINED tokens (= C2-12) | 8.2 |
| B2 notes | – | dropbear host key; slot erase at lock; hidden comments; passphrase rule; proxy address families | 3.2.4, 9.5.4, 13.4.3, 5.2.10, 7.2 |
| C2-1 | HIGH | first-person guards catch ordinary requests | 12.2.2–3, P8, P8b |
| C2-2 | HIGH | chunks without vectors unreachable | 13.6.3, 13.6.8, P6d, P6e |
| C2-3 | HIGH | DEEP swap has no failure path | 6.2.4, 8.3, P33 |
| C2-4 | MED | finance guard misses pay/bill/magkano | 12.2.2, P7 |
| C2-5 | MED | token and case rules undefined | 12.0, P8b |
| C2-6 | MED | stickiness over-captures; route set undefined | 12.3, P10c |
| C2-7 | MED | retries/fallback exceed client budget | 8.6.3, 11.9.3 |
| C2-8 | MED | RAM plan misses compute buffers | 8.3 |
| C2-9 | MED | EMBED sized to max seq | 8.3, 8.4 |
| C2-10 | MED | free EMBED slot not guaranteed | 11.7.2, P35 |
| C2-11 | MED | CPU plan cannot be applied unprivileged | 3.3.4, 8.5 |
| C2-12 | MED | USER_DEFINED tokens may be plain text | 8.2, 8.6.5 |
| C2-13 | MED | EMBED change has no driver | 13.2 vectors status, 13.5.7 |
| C2-14 | MED | over-ceiling notes unreachable | 13.5.6, 13.6.3 |
| C2-15 | MED | fresh notes get false "not in notes" | 13.3.6, 13.6.8, P6c |
| C2-16 | MED | calibration as a second writer, lost on rebuild | 13.1, 13.6.7 |
| C2-17 | MED | suite does not say if EMBED is stubbed | 17 header |
| C2-18 | MED | M11 untestable outside swap | 8.3, 18 |
| C2-19 | MED | emphasis stripping mangles arithmetic | 12.4.1, P12 |
| C2-20 | MED | header-lookalike regex bypass | 15.2.4, P11 |
| C2 notes | – | chunk cap vs budget; `/slots` flag; `P` rule; emb key by dim; indexing idle definition; retry temperature | 13.4.7, 8.4, 12.4.3, 13.2, 11.7.1, 8.6.4 |

---

## Round 3 (spec rev 2.2 → fixes in rev 2.3)

After round 3, Shawn was offered a scope reduction (explicit notes phrase instead of the guessing router, manual admin procedures, no DEEP swap, no remote rescue shell) and chose to **keep full scope** (D-16).

Duplicates merged: A3-2 = B3-8; A3-3 = B3-5; A3-4 = B3-2; A3-7 = B3-7; A3-8 = B3-9; C3-13 = B3-6; C3-14 = B3-10; C3-7 ≈ B3-14. All 37 unique findings accepted; fixes tagged `(R3: id)`. Rev 2.3 is self-contained (C3-15: "as rev 2.1" references had dropped rules).

| ID | Sev | Finding | Fix in 2.3 |
|---|---|---|---|
| A3-1 | HIGH | watchdog heartbeat kills the proxy during long normal operations | 7.2 liveness monitor with operation deadlines, P31b |
| A3-2 | HIGH | `phone-replace` misses unit IP filters, Syncthing device, dropbear keys | 5.2.18, 5.3, 14.4, 16, P37 |
| A3-3 | HIGH | `ssh … sudo … > file` cannot work with password sudo | 3.3.7 `sudo -S` + armored output, 5.3, P40 |
| A3-4 | MED | `lock_intent` set too late | 5.2.4/5.2.9 `lock-intent-set`, 9.5.1, P23c |
| A3-5 | MED | shutdown deadlock proxy ↔ mount ↔ privd | 5.2.1 `After=` mount, 6.3.8, P39 |
| A3-6 | MED | remote path for root fsck failure does not exist | 0.3(b) console, 3.2.4 per-key forced command |
| A3-7 | MED | detached copy unit cannot get the passphrase | 5.2.6, 5.2.15 `offline-copy-verify`, 14.3 |
| A3-8 | MED | reencrypt holds privd's lock for hours | 5.2.16 transient rekey unit, 9.6 code 24 |
| A3-9 | MED | update window lock undefined; unlock not blocked | 3.3.8, 9.6 code 23, P38 |
| A3-10 | MED | new keyslots use default argon2 memory | 5.2.12, 19.2.5 |
| A3-11 | MED | P31 bound below the worst case | 7.2 timings, P31 45 s |
| A3-12 | MED | `ip=` may create a competing network file | 4.2.2 mask generator, 19.2.1 |
| A3-13 | MED | switch-over check cannot fail | 3.3.3 reboot before purge |
| A3-14 | MED | PSK change order needs a console | 16 step 1 |
| A3 notes | – | lock budget sum; systemd-resolved; wifi-tune install; dropbear `from=`/TTY; SIGKILL in P23b | 9.5, 3.3.2, 4.2.4, 3.2.4, P23b |
| B3-1 | HIGH | reencrypt keeps the compromised passphrase | 5.2.16 rekey keeps only new keyslots, P41 |
| B3-2 | MED | (= A3-4) | |
| B3-3 | MED | clock budget resets each reboot | 5.2.17 persistent budget, 10.6, P29b |
| B3-4 | MED | privd/copy unit output leaks file names | 5.2.2, P28 |
| B3-5 | MED | (= A3-3) | |
| B3-6 | MED | fake date headers via header fields | 13.4.6, 15.2.4, P11 |
| B3-7 | MED | (= A3-7) | |
| B3-8 | MED | (= A3-2) | |
| B3-9 | MED | (= A3-8) | |
| B3-10 | MED | unclosed comments not stripped | 13.4.3, P11d |
| B3-11 | MED | re-bootstrap has no MITM check | 3.3.7 pinned `known_hosts` + pin on admin USB, 16 |
| B3-12 | MED | untrusted-clock rule contradicts the budget | 10.6 |
| B3-13 | MED | replacement phone cannot get notes back | 14.4, 16 step 7 |
| B3-14 | MED | clamped date unstable (≈ C3-7) | 13.4.2 |
| B3-15 | MED | P30 cannot pass | P30 |
| B3 notes | – | dropbear test with admin key; lockout count persisted; token file mode; header blob channel; copy-target identity by device | 19.3, 9.7, 5.3, 5.2.5, 14.3 |
| C3-1 | HIGH | FTS-only hits almost never fire | 13.6.1 term building, 13.6.3 ≥ 70% rule, P6d |
| C3-2 | HIGH | finance guard catches general questions | 12.2.2, P8 |
| C3-3 | HIGH | personal-reference catches general questions | 12.2.3 subject rule + fallback rule, P8b, P8c |
| C3-4 | MED | marker forms the tokenizer cannot match | 12.0, P5, P7 |
| C3-5 | MED | permanent causes give "still reading" forever | 13.6.8 transient/permanent, 13.4.8, P6f |
| C3-6 | MED | notes synced < 30 s ago get "not in notes" | 13.3.6–7, P6c |
| C3-7 | MED | future-date clamp corrupts legitimate dates | 13.2 `declared_date`/`rank_date`, 13.4.2, 13.4.6, P11c |
| C3-8 | MED | contradictory P12 vectors | 12.4.3, P12 |
| C3-9 | MED | `emb_key` misses most vector-space changes | 13.2.2 |
| C3-10 | MED | calibration on an incomplete index | 13.6.7 |
| C3-11 | MED | strict floor disables implicit retrieval | 13.6.7, M12b |
| C3-12 | MED | paragraph-break warmup is model-dependent | 8.6.5 |
| C3-13 | MED | (= B3-6, plus Unicode confusables) | 15.2.4 |
| C3-14 | MED | comment stripping vs code (≈ B3-10) | 13.4.3 |
| C3-15 | MED | rewrite dropped normative rules | 13.2.1–6; rev 2.3 self-contained |
| C3-16 | MED | DEEP BRAIN may exceed 200 s | 8.6.7, M9b |
| C3 notes | – | fallback matching; vault state in router tests; RAM comparator / EMBED ceiling / duplicate `-np`; swap deadline; calibration markers and stale route | 12.2, 17 header, 8.3, 8.4, 12.3.2, 13.6.7 |

---

## Round 4 (spec rev 2.3 → fixes in rev 2.4)

Auditors were told to apply the defect bar strictly (router gaps count only when a common phrasing breaks a stated invariant or listed test). Security reported 0 HIGH for the first time. Duplicates merged: A4-1 = B4-2; A4-2 = B4-1; A4-6 = B4-3. All 29 unique findings accepted; fixes tagged `(R4: id)`.

| ID | Sev | Finding | Fix in 2.4 |
|---|---|---|---|
| A4-1 | HIGH | dropbear `no-pty` vs `RequestTTY yes` breaks remote root unlock (= B4-2) | 3.2.4 pty allowed, global `-c` kept |
| A4-2 | HIGH | interrupted rekey undetected; unlock proceeds (= B4-1) | 5.2.7, 5.2.8, 5.2.10, 5.2.16, 9.6 codes 25–26, P41b |
| A4-3 | MED | proxy `After=` mount deadlocks proxy-only stops | 6.3.8, 9.5.7 direct umount, P42 |
| A4-4 | MED | privd `After=` mount reverses shutdown order | 5.2.1 `Before=` |
| A4-5 | MED | system-stopping check depends on D-Bus | 5.2.19 privd `system-stopping`, 6.3.8 |
| A4-6 | MED | proxy-writable floor bypasses the clock budget (= B4-3) | 5.2.17 privd floor, 10.3, P29d |
| A4-7 | MED | P38 impossible (proxy stopped) | 3.3.8 restart proxy, P38 |
| A4-8 | MED | `--pbkdf-memory` is only a cap | 5.2.12 forced iterations + check, 9.1, P43 |
| A4-9 | MED | job results not persisted | 5.2.15, 5.2.16, 5.4 |
| A4-10 | MED | fresh install never sets the Syncthing device | 3.3.7 pairing, 5.3 `sync-device` |
| A4-11 | MED | GRUB `--unrestricted` mechanism and check missing | 3.2.5, 19.2.7 |
| A4-12 | MED | cpu-plan runs before models exist | 3.3.4–5 |
| A4 notes | – | key changes while /boot unmounted; lock-intent before 202; fsck deadline; idempotent phone-replace; llmkey group | 3.2.4, 9.5.1, 9.4.2, 5.3, 5.1 |
| B4-4 | MED | Python stderr writers bypass excepthooks | 7.2 output hygiene, 5.2.2, P28 |
| B4-5 | MED | secret-generation rules dropped in 2.3 | 3.2.3, 3.3.7 pin formula, 5.1, 5.4 |
| B4-6 | MED | phone note attachments reach photo/cloud backup | 11.11.4–5 `.nomedia` |
| B4 notes | – | status verbs vs ACL; known_hosts keys; global `-c`; symlink-safe config writes; phone residual + bundle deletion | 5.2.4, 3.3.7, 3.2.4, 5.2.18, 16 |
| C4-1 | HIGH | "What did I write…" not personal | 12.2.3(a) base forms after did/have |
| C4-2 | HIGH | postposed Tagalog POSS missed | 12.2.3(b) |
| C4-3 | HIGH | "should I put / do I put" matched as personal | 12.2.3(a) |
| C4-4 | HIGH | "How do I pay with GCash?" → finance | 12.2.2(b) |
| C4-5 | MED | QUICK-on-DEEP may miss the 60 s deadline | 8.3, 8.6.7 |
| C4-6 | MED | zram subtraction makes the small tier DEGRADED | 8.3 zram `mem_limit` |
| C4-7 | MED | rescan ignores skip rules | 13.3.7, P6g |
| C4-8 | MED | stale Syncthing temp files keep the index partial | 13.3.6, P6h |
| C4-9 | MED | permanent faults counted as transient | 13.3.6, 13.6.8, P6i |
| C4-10 | MED | "How much money do I have?" missed | 12.2.2(d), P7 |
| C4-11 | MED | FTS stopwords omit markers/particles | 13.6.1 |
| C4-12 | MED | contraction tokens not mapped | 12.0.2 |
| C4-13 | MED | cancel during prompt processing untested | 8.4 batch size, 8.6.1 |
| C4-14 | MED | `dated` suffix reintroduces future date to the conflict rule | 13.4.6, P11c, M4b |
| C4 notes | – | fallback vs leak check order; PHP rule; P33 skip; LOADING scope and deep_failed effect; date grammar | 12.2, 15.3, 12.4.3, 8.3, 13.4.2 |

---

## Round 5 (spec rev 2.4 → fixes in rev 2.5)

Auditor A was interrupted by a usage limit and resumed to finish. Duplicate merged: A5-1 = B5-1. All 16 unique findings accepted; fixes tagged `(R5: id)`. The two HIGH findings were both defects introduced by round 4 fixes (rekey design, mount ordering); the ordering cycle was reproduced by the auditor with `systemd-analyze verify`.

| ID | Sev | Finding | Fix in 2.5 |
|---|---|---|---|
| A5-1 | HIGH | rekey cannot keep the new keyslots; power cut can lose the vault (= B5-1) | 5.2.16 `--init-only` with all secrets, then `--resume-only`; 5.2.6; P41b |
| A5-2 | HIGH | privd `Before=` mount creates an ordering cycle at shutdown | 7.5 mount `DefaultDependencies=no`; selfcheck `systemd-analyze verify`; P39 |
| A5-3 | MED | copy/rekey results not crash-safe | 5.2.15 "running" record; 5.2.16 crash safety |
| A5-4 | MED | `JOB_IN_PROGRESS` missing from the API | 11.4, P44 |
| A5 notes | – | divert path; READY=1 timing; read-only verbs bypass lock; tick per boot; fsck runbook | 3.2.5, 7.2, 5.2.7, 5.2.17, 5.3 `vault-fsck` |
| B5-2 | MED | keyslot check can delete the only working keyslot | 5.2.12, 9.6 code 27, P41c |
| B5-3 | MED | terminal control characters from notes reach Termux | 15.2.1, 8.6.4, 11.10, P11, M6 |
| B5 notes | – | initramfs mode; untrusted-clock future dates; PC device ID record; runbook order + version check; lock clears vectors/snapshot; privd floor returned | 3.2.4, 13.4.2, 3.3.7, 16, 9.5.5, 5.2.17 |
| C5-1 | HIGH | EMBED outage marks all pending chunks failed | 13.4.8, P6e2 |
| C5-2 | MED | EMBED-down timing undefined; disk-floor case | 13.6.8 `embed_down_since`, P6e, P6i |
| C5-3 | MED | save/saved, Tagalog enclitics, `pay maya` merge | 12.2.2, 12.1.6, P7 |
| C5-4 | MED | DEEP not READY in resident mode has no deadline | 8.3, P33b |
| C5-5 | MED | fallback skipped → abstention for a general question | 12.2 fallback budget, 12.5 "Out of time" |
| C5-6 | MED | filename future dates in the header title | 13.4.6, P11c |
| C5-7 | MED | FTS keeps "notes" and `ca`/`wo` stubs | 13.6.1, 12.0.2, P6d |
| C5-8 | MED | two budgets for QUICK on DEEP | 8.7 |
| C5-9 | MED | budget derivation has no order/minimum/source | 8.6.7 |
| C5-10 | MED | untrusted clock uses future dates for ranking | 13.4.2 |
| C5 notes | – | garbled currency sentence; list-marker definition; rescan timeout; calibration trigger; ₱ unused | 12.4.2–3, 13.3.7, 13.6.7, 12.2.2 |

---

## Round 6 (spec rev 2.5 → fixes in rev 2.6)

Duplicates merged: A6-1 ≈ B6-1; A6-2 = B6-2; A6-4 = B6-5. All 18 unique findings accepted.

**Design change (editor's decision):** in-place LUKS re-encryption ("rekey") produced new defects in rounds 3, 4, 5 and 6 (B3-1, A4-2, B4-1, A5-1, B5-1, B5-2, A6-1, A6-2, B6-1, B6-2, B6-3, B6-8). It is replaced by **vault rotation** (5.2.16): build a new vault with a new passphrase and re-sync the notes from the phone (the source of truth, D-15), with a fresh offline copy and a hash manifest as the safety net. The capability (recover from a compromised passphrase) is kept; the mechanism is far simpler. This is not a scope reduction.

| ID | Sev | Finding | Fix in 2.6 |
|---|---|---|---|
| A6-1 | HIGH | resume keys never unlock the old volume key (≈ B6-1) | rekey removed → 5.2.16 vault rotation, P41, P41b |
| A6-2 | MED | recovery key lost on interruption (= B6-2) | rotation returns it immediately (5.2.16 step 4) |
| A6-3 | MED | lock-intent waits behind vault-open; naive bypass loses the lock | 5.2.7, 5.2.8, 5.2.9, code 28, P23c |
| A6-4 | MED | no privd verbs for vault-fsck / lowering the floor (= B6-5) | 5.2.4, 5.3, 5.2.17 `clock-floor-set` |
| A6-5 | MED | proxy cannot set privd-call deadlines | 5.2.7 `PRIVD_BUSY`, 5.2.10 `fsck_last_s`/`last_close_clean`, 9.4.2 |
| A6 notes | – | directory modes, `InaccessiblePaths=/boot` in units; floor mutex; copy "interrupted" race; update-window close/open guards; Syncthing pre-start check on every start | 5.4, 7.2, 8.4, 14.4, 5.2.7, 5.2.15, 3.3.8 |
| B6-1 | HIGH | rekey init drops old keyslots (≈ A6-1) | rekey removed |
| B6-3 | MED | stale temporary keyslot record deletes a real keyslot | rekey removed |
| B6-4 | MED | vault-status bypass lets a late vault-open leave the vault open | 5.2.8, 5.2.10 `lock_holder`, P23d |
| B6-6 | MED | vault file names reach the admin terminal unescaped | 5.3 escaping, P28 |
| B6-7 | MED | restoring on a receive-only PC never reaches the phone | 5.3 `versions-export`, 16 step 7 |
| B6-8 | MED | new passphrases typed once; resume only with new passphrase | 5.3 typed twice, P41c (resume moot) |
| B6 notes | – | tick vs set-time race; staging path; reencrypt keyslot in selfcheck; hidden HTML (accepted residual); rekey key cleanup | 5.2.7, 5.2.17, 5.4, 19.2.5 |
| C6-1 | HIGH | fallback reserve leaves no room for BRAIN budgets | 12.2 streamed first-sentence decision, P45 |
| C6-2 | MED | transient conditions persist under permanent faults | 13.6.8 "indexing allowed", permanent overrides, P6i |
| C6-3 | MED | `embed_down_since` undefined from unlock / stale after lock | 13.6.8 |
| C6-4 | MED | enclitics break personal-reference adjacency | 12.2.3, P5 |
| C6-5 | MED | own-money questions with SUBJ markers miss the guard | 12.2.2(a), P7 |
| C6-6 | MED | header fields still carry dates | 13.4.6, 15.3 rule 3, M4c |
| C6-7 | MED | rank_date jumps to the event date | 13.4.2, P11c, M4d |
| C6-8 | MED | DEEP tests without skip/force rules | M9b, P33b, 8.3 |
| C6 notes | – | fillers/lock hint; stoplist and compounds; fixed-reply classes; ₱ clause; rank clamp and "0 left" wording | 12.2.1, 13.6.1, 12.3.2, 12.2.2(e), 13.4.2, 12.5 |

---

## Round 7 (spec rev 2.6) — LOGGED, NOT YET APPLIED (loop paused by Shawn)

Duplicates merged: A7-1 = B7-2 (unlock forbidden during `syncing`); A7-4 ≈ B7-2 (stuck `manifest` state, no finish/abort verb); A7-2 ≈ B7-5 (tmpfs manifest lost after erase); A7-9 = B7-6 (root follows symlinks in `notes/`); A7-8 ≈ B7-9 (export verbs have no content channel or mount names). 19 unique findings. All look valid on first read; formal triage happens when the loop resumes.

**Pattern:** 13 of the 19 concern the vault-rotation procedure and admin export verbs added in round 6. Planned direction (editor's proposal, not yet applied): use the verified offline copy as the comparison reference instead of a tmpfs manifest; one write-ahead persisted state (`copy_verified → erasing → new → none`) with explicit `rotation-finish`/`rotation-abort` verbs and unlock allowed except while `erasing`; admin verbs take opaque file IDs instead of paths; privd walks `notes/` with `O_NOFOLLOW` and regular files only.

| ID | Sev | Finding (short) |
|---|---|---|
| A7-1 / B7-2 | HIGH | rotation forbids the unlock its own step 5 needs; `manifest` state cannot be cleared; no finish/abort verbs |
| A7-2 / B7-5 | HIGH | tmpfs manifest lost on reboot after erase → nothing to verify against |
| A7-3 | HIGH | `--verify` cannot finish on normal differences; recovery advice can overwrite newer phone edits |
| A7-4 | HIGH | (merged into A7-1 / B7-2) |
| A7-5 | MED | `vault-erase` not write-ahead (state written after the erase) |
| A7-6 | MED | proxy `vault-open`/`vault-close` block behind long root verbs past their deadlines |
| A7-7 | MED | privd crash mid-`vault-open` leaves the vault mounted while the proxy says locked |
| A7-8 / B7-9 | MED | export verbs have no blob channel, mapping name or mount point |
| A7-9 / B7-6 | MED | root walks a Syncthing-writable tree following symlinks (`/dev/zero`, root files) |
| A7 notes | – | `set-time` in both lock lists; failed update-window `open` must clear the flag; `isLuks` exception only in `erased`; reconciliation rule for `lock_holder = vault-open` missing from 6.3.5; how admin triggers a lock; synccheck helper interface |
| B7-1 | HIGH | vault file names passed as shell arguments over SSH → command injection by a note name |
| B7-3 | MED | phone keeps trusting the old PC Syncthing device after rotation |
| B7-4 | MED | safety-net copy may predate the manifest and need not be verified |
| B7-7 | MED | 6.3.5 has no rule for an in-flight `vault-open` (P23d not normative) |
| B7-8 | MED | sudo logs vault relpaths to the journal |
| B7-10 | MED | runbook cannot see files a thief added (only replaced/deleted are in `.stversions`) |
| B7 notes | – | `set-time` lock contradiction; two-secret helper for `offline-copy-export`; escape `Cs`; mark copy and header stale at erase; SSD remanence after erase |
| C7-1 | MED | "permanent overrides" brings back false "not in notes" for fresh notes |
| C7-2 | MED | budgets leave no room for abstain-then-GENERAL in swap QUICK-on-DEEP |
| C7-3 | MED | rule 3 "date right after [" matched by pipe-less body dates |
| C7-4 | MED | `retry_window_s` does not cover the 2 × load wait |
| C7-5 | MED | rotation note-count check compares against `index.notes`, which cannot match |
| C7 notes | – | filler stripping on patterns; "Can't search" vs "still reading" in P6c/P6e; P45 force/skip rule; edits vs "permanently"; `YYYY/MM/DD` speech and contraction-suffix scope |
