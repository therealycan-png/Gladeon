# GLADEON (ALIAS: GLADION) — PHASE 1 ARCHITECTURAL SPECIFICATION BLUEPRINT
**Status:** Final Unified Master Handoff Specification (3-Company Zero-Defect Streak Verified)
**Target Execution Environment:** Windows 11 Desktop PC (CPU-Only, Headless / No Monitor Attached, Offline Local Wi-Fi, Philippines / `Asia/Manila` `UTC+8`)
**Implementation Language Target:** Pure Python Standard Library (zero bloated web frameworks, zero Docker/containers, zero external vector DLLs) + Upstream `llama.cpp` v1.0 (`llama-server`) + SQLite (`>= 3.53.0`) + VeraCrypt

---

## 0. MANDATORY DIRECTIVES TO CLAUDE (READ FIRST — DO NOT SKIP)

1. **ZERO-CODE-FIRST & INDEPENDENT DEEP-CLEAN MANDATE:**
   - Do **NOT** immediately generate implementation code upon reading this specification.
   - You must independently audit every single subsystem and cross-subsystem boundary defined below, actively hunting for hidden OS, hardware, concurrency, security, and edge-case flaws.
   - Run an iterative verification loop per micro-topic until each topic achieves **3 consecutive clean passes (`0` new defects)**, followed by an end-to-end whole-system integration loop until the entire stack achieves **3 consecutive clean passes (`0` new defects)**.
   - Only after completing and reporting your independent deep-clean verification—and confirming all pre-flight checks—may you proceed to implementation.

2. **CRITICAL HARDWARE CONSTRAINT — NO MONITOR IS PLUGGED IN YET (HEADLESS PC):**
   - The desktop PC currently has **NO physical monitor (no HDMI or DisplayPort display) connected**.
   - You must specifically audit and account for every headless Windows 11 side effect caused by zero EDID/monitor presence:
     - **No Display Output / Blind Console:** The user cannot see Windows lock screens, UAC dialogs, VeraCrypt GUI prompts, or console error popups on a PC screen. Never use blocking GUI modal dialogs (`MessageBox` or GUI file pickers) anywhere in the stack.
     - **No HDMI/DisplayPort Audio Device:** With no monitor connected, Windows has no monitor audio endpoint. Never route Text-to-Speech (TTS) audio to Windows default speakers; all voice/text outputs must be serialized in the HTTP response back to the mobile phone for playback and display.
     - **iGPU Headless Driver Behavior:** Without a monitor attached, integrated graphics drivers may enter low-power headless states or fail EDID queries; locking `llama-server` to `-ngl 0` (pure CPU inference) avoids all iGPU driver dependencies.
     - **Headless Status Telemetry:** Because the user cannot look at a PC monitor to see why a command failed or whether the vault is still locked, every readiness state, CLI exit code, pre-flight error, clock-drift warning, and disk-space alert must be returned cleanly via the Gatekeeper Proxy's JSON payload/status endpoint to the mobile phone and logged to disk.
     - **Headless Boot & VeraCrypt Unlock Path:** Design the startup lifecycle assuming the user presses the physical PC power button and unlocks the VeraCrypt volume from their phone over local TLS without relying on an attached monitor or blind Windows password entry. Never store the VeraCrypt passphrase in plaintext on disk.

3. **PHASE 1 SCOPE BOUNDARIES (NO GAMING, NO EXTERNAL WAKE HARDWARE YET):**
   - **No Gaming Until Discrete GPU Upgrade:** The user will not play games on this PC until a discrete GPU is installed in a future phase. Do **NOT** include any background `tasklist` game-polling watchdog in Phase 1. Keep the hardware profile modular (`cpu_only` active now; `gpu_hybrid` + game-killer watchdog reserved as a clean configuration seam for Phase 2).
   - **Manual Power-On Only:** Phase 1 uses manual physical power-button press to boot the PC (no external mechanical Fingerbot servo and no internal `PWR_SW` relay yet).

---

## 1. PHYSICAL HARDWARE, HEADLESS & OFFLINE NETWORK TOPOLOGY

1. **Air-Gapped Offline Wi-Fi Loop:**
   - The local Wi-Fi router stays powered on with the WAN/internet cable physically disconnected (or internet blocked at the router).
   - Router DHCP assigns **Static IPv4 Reservations** to both the Windows 11 PC and the mobile phone.
   - **OS-Level Static IPv4 Assignment:** Also configure the PC's static IPv4 address, subnet mask, and gateway directly on the Windows Wi-Fi network adapter (matching the router reservation) so `COLD_BOOT_PREFLIGHT` never fails with `WinError 10049` if the PC boots faster than the router after a power outage.
   - **All-Users Pre-Logon WLAN Profile (`user=all`):** Export the saved Wi-Fi profile (`netsh wlan export profile name="<SSID>" key=clear`), delete the user-scoped profile, re-import with `netsh wlan add profile filename="<xml>" user=all`, and set `netsh wlan set profileparameter name="<SSID>" connectionmode=auto` so Windows 11 connects to the offline router automatically at the pre-logon screen before desktop sign-in. Disable Windows NCSI active internet probing delays.
   - Disable IPv6 link-local (`fe80::`) preference and `.local` mDNS resolution in the mobile client; the mobile app must target the PC's exact **static IPv4 address** directly.
   - On the mobile phone, disable **Wi-Fi Assist / Adaptive Connectivity / Auto-Switch to Mobile Data** so the phone never drops off the internet-free Wi-Fi network.
2. **Gaming Bloat Purged:**
   - Disable or remove background game launchers (Steam, Discord, Riot Client) and Riot Vanguard / kernel anti-cheat services so background drivers and Virtualization-Based Security (HVCI) overhead do not degrade CPU inference latency or memory bandwidth.
3. **Quarantined Write Boundary (`ai_inbox`):**
   - The LLM (`llama-server`) has **zero** shell execution, network access, or direct filesystem write privileges.
   - The user's Markdown vault is accessed **strictly read-only** by the indexer.
   - Place the quarantined `ai_inbox` staging directory on the local host filesystem so file drops work even while the encrypted vault is locked; background finance/vault ingestion workers hold off consuming `ai_inbox` until `FULL_READY` mounts `finance.db`.

---

## 2. GLOBAL SYSTEM STATE MACHINE

The Gatekeeper Proxy orchestrates the entire PC lifecycle through **7 deterministic states**, preventing cold-boot races, mid-batch sleep interruptions, and unmounted-vault crashes:

1. `COLD_BOOT_PREFLIGHT`: Runs hardware, OS, binary hash, SQLite version, CMOS monotonic clock, network bind-retry, and disk-space checks on startup before accepting traffic.
2. `LOADING_ENGINE`: `llama-server` loads `.gguf` weights into physical RAM and executes a 1-token warmup ping to fault weight pages into RAM; incoming phone queries receive a clean `503 Loading Model` status rather than a raw connection reset.
3. `VAULT_LOCKED_PUBLIC_ONLY`: `llama-server` is warm, but the VeraCrypt encrypted volume is not yet mounted. General Q&A, quarantined `ai_inbox` staging drops, and `POST /vault/unlock` function normally while `brain.db` and `finance.db` queries return an explicit `"Vault Locked"` status without crashing.
4. `FULL_READY`: VeraCrypt volume mounted and resolved via persistent NTFS Volume GUID, Cold-Boot Recovery Sweep passed, databases verified (`PRAGMA quick_check`), and Markdown vault watcher + `ai_inbox` ingestion workers active.
5. `MAINTENANCE_BUSY`: Global mutex active during sequential pre-sleep SQLite Online Backups and `llama-server` RSS-leak process recycles. Incoming phone queries receive `503 Maintenance`, triggering the mobile client's exponential retry seamlessly.
6. `S3_SLEEP_PREPARING`: Checkpoints SQLite WAL files (`TRUNCATE`), closes all database file handles inside the encrypted volume, verifies zero open handles, holds `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` for a deliberate 90-second tail buffer, and releases sleep inhibition to enter pure S3 Suspend-to-RAM.
7. `POST_WAKE_STABILIZING`: Triggered on `PBT_APMRESUMEAUTOMATIC`. Enforces a mandatory **3-second stabilization gate** so Windows storage drivers and the Wi-Fi adapter finish resuming before probing `127.0.0.1` `/health`, verifying encrypted volume reachability, and running `PRAGMA quick_check`.

---

## 3. SUBSYSTEM 1: `llama.cpp v1.0` INFERENCE ENGINE (`llama-server`)

1. **Binary & CLI Flag Integrity (`llama.cpp` v1.0+):**
   - Pin the exact upstream `llama-server` binary and validate CLI flags against `llama-server --version` during pre-flight.
   - Never use deprecated/removed flags (`--no-mmap`, `--defrag-thold`).
   - Validate `.gguf` file existence, non-zero size, and SHA-256 checksum in the launcher before spawning `llama-server` so a missing `-m` path never triggers 2026 `llama-server`'s silent Router-Mode fallback.
   - Explicitly disable all built-in tools, MCP servers, and external `--connect` tunnels at launch.
2. **Loopback, Telemetry & Credential Hardening:**
   - Bind strictly to `--host 127.0.0.1` on an explicit pinned `--port` (e.g., `9931`—never assume legacy default `8080`).
   - Explicitly pass `--slots` on the CLI so the Gatekeeper Proxy's S3 sleep-inhibitor thread can poll `/slots` for active inference generation.
   - Load the local Bearer API token via `--api-key-file` (never pass `--api-key` on the command line where Windows process lists expose it in plaintext).
3. **CPU-Only Memory & Cache Determinism:**
   - `-ngl 0`: Force zero GPU layers so current builds never offload weights onto the CPU's integrated graphics over the same slow system RAM bus.
   - `-lm none`: Disable memory-mapped file loading so Windows never silently evicts model weights to SSD pagefiles under memory pressure.
   - `-np 1`: Lock to a single processing slot.
   - `-c <fixed_size>`: Explicitly pin context window size so `llama-server`'s default `--fit on` auto-fitter never silently alters memory allocations.
   - `--cache-ram 512`: Hard-cap prompt KV RAM caching to `512 MB` (overriding the 2026 default of `8,192 MB` which otherwise balloons RAM usage over time).
   - `--no-cache-idle-slots` + disable automatic per-request slot state-copy (which halves throughput) + send explicit slot-reset commands whenever switching context domains (General Chat vs. `brain.db` vs. `finance.db`) or exceeding the 90-second follow-up window to prevent cross-turn KV cache bleed.
4. **CPU Topology, Affinity & Thermal Control:**
   - Set thread count strictly to `physical_cores - 1` (never logical hyper-threads, which cause SIMD pipeline contention).
   - Pin `llama-server` CPU affinity mask strictly to physical Performance cores (P-cores) within a single L3 cache complex; avoid Efficiency cores (E-cores).
   - Set process priority to `ABOVE_NORMAL` (never `REALTIME`, which starves Windows network/storage drivers).
   - Cap physical prompt-evaluation micro-batch size (`-ub 512`) so prompt ingestion stays within CPU L3 cache thermal headroom without throttling.
5. **Generation Guardrails & Long-Uptime RSS Leak Recycling:**
   - Route all inference strictly through `/v1/chat/completions` using the model family's verified Jinja chat template and explicit EOS stop-token arrays.
   - Force `reasoning_budget = 0` on interactive voice turns so reasoning-capable models never burn the token cap on internal `<think>` chains.
   - Enforce a hard `max_tokens` ceiling (e.g., `350` tokens for voice replies), repeat penalties, and a monotonic wall-clock timeout per request.
   - **RSS Leak Mitigation:** Because long-running `llama-server` processes exhibit a ~3 MB/request cumulative RSS memory leak, poll `llama-server`'s private working set before every S3 sleep; if RSS exceeds the configured ceiling or cumulative requests reach `200`, gracefully terminate and respawn `llama-server` sequentially inside `MAINTENANCE_BUSY`.

---

## 4. SUBSYSTEM 2: WINDOWS PROCESS SUPERVISION & OS LIFECYCLE

1. **Pre-Logon Startup Execution (Zero Blind Windows Login Required):**
   - Register the Gatekeeper Proxy to launch via a **Windows Task Scheduler "At Startup" task** (running in Session 0 prior to interactive desktop logon) rather than a user-session `Startup` folder shortcut.
   - Pressing the physical PC power button boots `llama-server` and the Gatekeeper Proxy automatically into `VAULT_LOCKED_PUBLIC_ONLY` without requiring a monitor or blind Windows password entry before the phone can send `POST /vault/unlock`.
   - Configure `bcdedit /set {default} bootstatuspolicy IgnoreAllFailures` (once initial OS stability is verified) so a sudden power outage never traps the headless PC on an invisible Windows Automatic Repair recovery menu.
2. **Windows Job Object Binding:**
   - Attach `llama-server` and any auxiliary OCR/TTS worker processes to a Windows **Job Object** configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. If the Gatekeeper Proxy crashes or exits, the Windows kernel immediately terminates all child processes so no orphaned `llama-server` holds port `9931` or RAM.
3. **Anonymous Pipe & Console Freeze Prevention:**
   - Never capture child `stdout`/`stderr` via unread in-memory pipes (Windows' 4 KB anonymous pipe buffer fills within seconds and deadlocks the child process). Redirect child output directly to size-capped rotating log files or `DEVNULL`.
   - Programmatically disable `ENABLE_QUICK_EDIT_MODE` on the Windows console handle at startup so accidental terminal clicks never freeze the proxy process.
   - Register a Win32 `SetConsoleCtrlHandler` routine that checkpoints SQLite WALs, closes database handles, and closes the Job Object within 2 seconds on close/logoff signals.
4. **Pure S3 Sleep & Power Policy Hardening (`powercfg`):**
   - Confirm hardware S3 support (`Standby (S3)`) vs. Modern Standby (`S0ix`) during pre-flight.
   - Run `powercfg /h off` to disable Hibernation and Windows Fast Startup (`hiberfil.sys`), preventing multi-GB SSD writes, stale NIC driver states across boots, and VeraCrypt hibernation-resume cold-boot bugs.
   - Disable Hybrid Sleep and Hibernate-after-Sleep.
   - Disable Windows Update wake timers and automatic reboots outside manual maintenance.
   - Disable both `"Wake on Magic Packet"` and `"Wake on Pattern Match"` in the Wi-Fi adapter's advanced driver properties so router ARP/mDNS broadcasts never wake the PC from S3 sleep.
   - Set minimum processor state to `100%` and disable CPU core parking while awake (eliminates C6 core-wake latency on the first generated token), relying on aggressive 3–5 minute idle S3 sleep for power savings.
   - Manage `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` exclusively on a single dedicated lifecycle thread driven by a composite activity bitmask (active LLM slots, vault indexer, OCR worker, SQLite backup) plus a 90-second post-activity tail hold.
5. **OS Security, Privacy & Path Hardening:**
   - Add Windows Defender real-time scan exclusions for the `models/` folder and the mounted VeraCrypt volume (`MsMpEng.exe` scanning multi-GB `.gguf` files causes severe latency spikes).
   - Enable Win32 Long Paths in Windows registry/policy so nested vault folders never hit the 260-character `MAX_PATH` crash.
   - Disable Windows kernel crash dumps (`MEMORY.DMP`) and enable `ClearPageFileAtShutdown` so decrypted RAM pages never persist on unencrypted disk.

---

## 5. SUBSYSTEM 3: VERACRYPT ENCRYPTED BOUNDARY & HEADLESS UNLOCK

1. **Windows VeraCrypt Keyfile-Mode Silent Mount (`POST /vault/unlock`):**
   - Initialize the VeraCrypt container during setup in **Keyfile-only mode** using raw BOM-free UTF-8 bytes of the user's chosen passphrase (`[System.IO.File]::WriteAllBytes(...)` in PowerShell—never `Out-File` or `echo`, which inject a UTF-8 BOM and trailing `CRLF`).
   - Expose an authenticated `POST /vault/unlock` endpoint on the Gatekeeper Proxy (active only while in `VAULT_LOCKED_PUBLIC_ONLY` state).
   - Encode the received passphrase as exact BOM-free UTF-8 bytes (`"wb"` mode), write to a `SYSTEM`-ACL-locked temporary file, explicitly `flush()` and `close()` the file handle *before* invoking `VeraCrypt.exe` (preventing Windows file-sharing locks), execute:
     `VeraCrypt.exe /v "<container_path>" /a /m label=GLADEON_VAULT /p "" /k "<ephemeral_keyfile>" /q /s`
     (explicitly passing `/p ""` alongside `/k` so Windows `VeraCrypt.exe` never aborts on an unspecified password, and avoiding Linux-only `/m system`), and immediately zero-overwrite + delete the temp keyfile inside a `finally` block.
   - If `VeraCrypt.exe` returns a non-zero exit code, return the exact exit code to the mobile client in the JSON response.
2. **Session 0 Volume GUID Resolution & Sleep Safety:**
   - Resolve the mounted encrypted volume root dynamically via Win32 `FindFirstVolumeW` / `GetVolumeInformationW` matching volume label `GLADEON_VAULT` to obtain its persistent **NTFS Volume GUID path (`\\?\Volume{<GUID>}\`)**, ensuring Session 0 services never race against Windows Mount Manager drive-letter assignment or USB drive-letter shifts.
   - Disable VeraCrypt's auto-dismount-on-sleep option so Windows sleep never severs open database handles before the proxy's pre-sleep hook closes them.
3. **Preventing Plaintext Leakage & Virtual-Volume Crashes:**
   - Place `.metadata_never_index` at the volume root, exclude the encrypted volume from Windows Search Indexing (`SearchIndexer.exe`), and disable Explorer `Thumbs.db` caching on the drive so plaintext snippets never leak onto `C:`.
   - Lock `PRAGMA mmap_size = 0` on every SQLite connection inside the VeraCrypt volume to prevent uncatchable Windows `EXCEPTION_IN_PAGE_ERROR` crashes during virtual-volume I/O.
4. **Offline Clock-Rollback & Disk-Space Floor Guards:**
   - Maintain a persistent **Monotonic Epoch Floor** updated on every clean sleep/shutdown. If a weak CR2032 CMOS battery resets the PC clock backward (e.g., to `2020-01-01`), block all database writes and backup pruning until forward time is verified via phone header or manual clock adjustment.
   - Enforce a `2 GB` free-disk-space floor: if free space drops below `2 GB`, halt non-essential writes and emit a status alert before SQLite or logs can hit `SQLITE_FULL`.

---

## 6. SUBSYSTEM 4: GATEKEEPER PROXY, LOCAL TLS & API CONTRACT

1. **Encrypted Local Transport (`HTTPS` with Native Windows 11 PEM Export & Static IPv4 SAN):**
   - Wrap the Gatekeeper Proxy HTTP listener in TLS using Python's built-in `ssl.SSLContext.load_cert_chain()`.
   - Generate a 10-year self-signed X.509 certificate during one-time setup via Windows PowerShell 5.1:
     `New-SelfSignedCertificate -KeyAlgorithm RSA -KeyLength 2048 -KeyExportPolicy Exportable -Subject "CN=GladeonLocal" -TextExtension @("2.5.29.17={text}IPAddress=<PC_STATIC_IP>") -NotAfter (Get-Date).AddYears(10)`
   - Extract the RSA private key primes natively in built-in PowerShell 5.1 via `[System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert).ExportParameters($true)` and serialize `key.pem` + `cert.pem` using pure-stdlib Python ASN.1 DER encoding (zero third-party OpenSSL binaries required).
   - Pin/trust `cert.pem` in the mobile client so Android's TLS validator accepts raw static-IPv4 HTTPS handshakes offline with zero external CRL/OCSP lookups.
2. **Network Binding & Firewall Lockdown:**
   - Bind the Gatekeeper Proxy listener exclusively to the PC's static IPv4 Wi-Fi interface address (never `0.0.0.0`), wrapped in a startup bind-retry loop.
   - Configure a Windows Defender Firewall inbound rule allowing TCP on the proxy port **only** from the mobile phone's exact static IPv4 address, applied across **both `Private` and `Public` profiles** (survives Windows profile flips after router reboots).
   - Reject any HTTP request containing browser `Origin` or `Sec-Fetch-Site` headers to block local browser DNS-rebinding/CORS probes.
   - Authenticate requests with a custom Bearer token verified via constant-time `hmac.compare_digest`.
3. **Bounded Concurrency, Deadlines & Transport Reliability:**
   - Use a fixed 4-worker HTTP thread pool with a bounded connection backlog (`max_queue = 8`, returning `503 Busy` when saturated).
   - Enforce a header-level `Content-Length` cap (`64 KB` max) before reading the request body.
   - Enable `SO_KEEPALIVE`, 10-second socket read/write timeouts, and a hard **3-second total request-body read deadline** from TCP accept to full payload receipt (prevents Slowloris/drip-feed Wi-Fi stalls from pinning worker threads).
   - Maintain a single persistent HTTP `keep-alive` connection pool for internal proxy-to-`127.0.0.1:9931` `/health` and `/slots` checks (eliminates Windows `TIME_WAIT` ephemeral port exhaustion).
   - Enforce a priority mutex so interactive mobile voice requests always preempt background tasks before acquiring the single `llama-server` slot.
   - Use monotonic clock timers (`time.monotonic`) exclusively for all deadlines, cooldowns, and idle timers.
   - Deduplicate requests via client-generated `Request-UUID` headers (60-second TTL) and expose a `GET /result/{request_id}` recovery polling endpoint if the mobile client disconnects mid-inference.
   - Buffer and validate complete JSON responses inside the proxy before sending `200 OK` to the phone.
4. **Explicit JSON API Endpoints:**
   - Lock down exact request/response schemas for:
     - `GET /status` -> returns `state`, `vault_mounted`, `engine_ready`, `clock_drift_sec`, `free_disk_mb`, `active_model`.
     - `POST /vault/unlock` -> accepts `{"passphrase": "..."}`, returns `{"unlocked": bool, "state": "...", "cli_exit_code": int, "error": null|str}`.
     - `POST /query` -> accepts `{"request_id": "uuid", "client_epoch": int, "transcript": "..."}`, returns `{"request_id": "uuid", "state": "...", "display_text": "...", "tts_text": "...", "warnings": []}`.
     - `GET /result/{request_id}` -> returns cached completed JSON payload or `202 Processing`.

---

## 7. SUBSYSTEM 5: VOICE PIPELINE (`GLADEON` / `GLADION`) & MOBILE BRIDGE

1. **Canonical Identity & Mobile Trigger Bridge:**
   - Use **Gladeon** (or **Gladion**) as the single canonical assistant name in the static system prompt (keeping system framing compact under ~40 tokens).
   - Specify a concrete, zero-bloat offline mobile trigger bridge (e.g., an Android Tasker/MacroDroid or Termux workflow wired to the phone's built-in offline speech-to-text and text-to-speech engines) targeting `https://<PC_STATIC_IP>:<PORT>`.
2. **Deterministic Input Normalization Pipeline (Execute in Exact Order):**
   - **Step 1 — Unicode & Control Hygiene:** Run `NFKC` Unicode normalization; strip zero-width characters (`\u200b`), Unicode Format/Bidirectional override characters (`Cf`, e.g., `U+202E`), and Control characters (`Cc`, preserving `\n` and `\t`); normalize curly quotes and non-breaking spaces (`\u00a0`).
   - **Step 2 — Leading-Prefix Wake-Alias Filter:** Match `gladeon`, `gladion`, `glad eon`, `glad ian`, `glade on`, and `gladiator` (with optional leading filler like `"hey"` or `"okay"`) **strictly within the first 1–2 words** of the transcript—never mid-sentence. Strip the matched wake alias and its immediate trailing punctuation while preserving all subsequent sentences intact.
   - **Step 3 — Zero-Token Empty-Payload Short-Circuit:** If the stripped command text is empty (e.g., user only said *"Gladeon"*), immediately return a lightweight local acknowledgment without invoking `llama-server`.
   - **Step 4 — Trailing Hallucination & Name-Echo Cleanup:** Strip known offline STT trailing artifacts (`"...thank you"`, `"...thank you for watching"`) and trailing `gladeon`/`gladion` echoes.
   - **Step 5 — Philippine Domain & Spoken-Number Canonicalization:** Normalize local banking/entity variants (`GCash`, `G cash`, `G-cash`, `G C A S H` -> `GCash`; `BPI`/`PPI` -> `BPI`), expand spoken shorthand numbers (`"fifteen hundred"`, `"1.5k"`, `"1,500"` -> canonical numeric tokens / centavos), and fold Unicode diacritics (`Parañaque` -> `Paranaque`).
3. **Bounded Same-Domain Follow-Up Context (90s Window):**
   - Retain a bounded **2-turn rolling conversation buffer for up to 90 seconds** *only* when consecutive queries remain inside the **same domain** (`finance.db` -> `finance.db` or `brain.db` -> `brain.db`), allowing natural follow-ups (*"What about the week before?"*).
   - Immediately wipe the rolling buffer and reset `llama-server` slot state whenever switching domains or after 90 seconds of inactivity.
4. **Dual-Payload Output & Local TTS Sanitization:**
   - Return two distinct fields in every proxy response:
     - `display_text`: Full rich Markdown for the phone screen.
     - `tts_text`: Sanitized plaintext stripping all Markdown syntax (`**`, `#`, bullet asterisks, code fences, table pipes), expanding currency figures (`₱12,450.75` -> *"12,450 pesos and 75 centavos"*), and applying a **Philippine & Proper-Noun Pronunciation Lexicon** (`BGC` -> `B G C`, `SSS` -> `S S S`, and `Gladeon`/`Gladion` mapped to phonetic `"Glad-eon"` / `GLAD-ee-on` so English TTS rules never mispronounce it as `"GLAY-dee-on"`).
   - If local TTS synthesis exceeds a 2-second timeout or fails, deliver `display_text` immediately without blocking the response.
5. **Mobile Client Rules:**
   - Send every request with a unique `Request-UUID` and local phone timestamp header (flag a drift warning if PC clock diverges by more than `60 seconds`).
   - Implement a 3-step exponential retry (`0.5s`, `1.5s`, `3.0s`) with the identical `Request-UUID` to bridge post-S3-wake router ARP refresh.
   - Hold an active foreground audio/wake session from mic tap through reply receipt; enforce half-duplex microphone muting during TTS playback plus a 400 ms acoustic tail buffer (prevents speaker echo loops); cap offline pending queue to 1 active command (30s expiry).

---

## 8. SUBSYSTEM 6: 2ND BRAIN (`brain.db`), VAULT WATCHER & HYBRID SEARCH

1. **SQLite Engine & Concurrency Rules (`brain.db`):**
   - During setup, place the official Windows x64 `sqlite3.dll` (`>= 3.53.0`) into Python's `DLLs\` directory, and assert at startup that `sqlite3.sqlite_version >= 3.53.0` (avoids pre-3.51.3 WAL-reset corruption bugs and Windows `winsqlite3.dll` vulnerabilities).
   - Set `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`, `PRAGMA busy_timeout = 5000`, `PRAGMA mmap_size = 0`.
   - Route **all** writes and checkpoints through a **single dedicated serialized writer queue thread** using explicit `BEGIN IMMEDIATE` transactions; close every connection explicitly inside `finally` blocks; run `PRAGMA wal_checkpoint(TRUNCATE)` before sleep/backup.
   - Never attach chained `AFTER UPDATE` triggers to external-content `FTS5` tables; configure `FTS5` with `tokenize='unicode61 remove_diacritics 2'`.
2. **Lightweight Embedding Runtime & CPU Mutex:**
   - Do **NOT** import PyTorch or heavy ML frameworks to generate `brain.db` embeddings. Use a lightweight CPU-only runtime (`onnxruntime` with a small quantized embedding model or a dedicated tiny GGUF embedding model).
   - Gate all embedding batches behind the Gatekeeper Proxy's concurrency mutex so vault indexing yields immediately to interactive phone voice requests and never runs simultaneously with `llama-server` generation.
3. **Markdown Vault Watcher & File Hygiene:**
   - Hash-polling scanner (30-second save cooldown) with circular Windows symlink/junction protection, ignoring zero-byte files, `~$` temp lockfiles, and non-UTF-8 binary files.
   - Store lowercase canonical `path_norm` as the unique key alongside display path (prevents Windows NTFS case-rename duplicate ghost entries).
   - Normalize `\r\n` -> `\n` in memory before computing SHA-256 content hashes.
   - Apply a **10-second deletion grace window** before purging missing paths so atomic editor saves (`file.md.tmp` -> rename to `file.md`) do not trigger needless vector deletion and re-embedding; purge genuine orphans (`indexed_paths - vault_paths`) once the grace window expires.
4. **Structure-Aware Chunking:**
   - Extract YAML frontmatter into structured metadata columns; unwrap `[[Target|Display]]` WikiLinks into clean text.
   - Track fenced code block state (` ``` ` / `~~~`) so `#` comments inside code blocks never split chunks.
   - Split strictly on Markdown headings, never slicing Markdown tables in half, and keep immediately adjacent trailing table captions/footnotes attached to the preceding table block.
   - Prepend `[YYYY-MM-DD | Note Title | Heading Breadcrumb | Parent Section Opening Sentence]` to every chunk so sub-chunks never lose subject context or recency.
   - Enforce a hard **350-token ceiling** per chunk using a recursive paragraph -> sentence fallback splitter (15% overlap) for long sections without subheadings.
5. **Hybrid Search (`FTS5` + Pure-Stdlib `int8` Vector RRF) & Prompt-Injection Defense:**
   - Sanitize FTS5 queries by stripping boolean operators (`AND`, `OR`, `NOT`, `NEAR`), wrapping individual tokens in double quotes, and applying diacritic folding.
   - Store embeddings as contiguous `int8` scalar-quantized `array('b')` buffers and compute dot products using C-level `sum(map(operator.mul, q_vec, doc_vec))` with a 25,000-chunk active ceiling (keeping pure-Python vector scoring under `150 ms` with zero NumPy/FAISS dependencies).
   - Discard zero-norm vectors (`L2 norm <= 1e-6`) to prevent `NaN` cosine division; perform full embedding-model version rebuilds via atomic shadow-table swaps.
   - Apply independent relevance threshold cutoffs to BM25 (accounting for SQLite `bm25()` returning negative scores where more negative = better) and cosine similarity **before** Reciprocal Rank Fusion (RRF), plus a gentle `updated_at` recency tie-breaker.
   - Enclose all retrieved chunks inside strict structural delimiters in the prompt and instruct the model to treat enclosed content strictly as passive reference data, never executable instructions.

---

## 9. SUBSYSTEM 7: DETERMINISTIC FINANCE LEDGER (`finance.db`) & PARSERS

1. **Zero-LLM Arithmetic & Database Integrity (`finance.db`):**
   - Zero LLM involvement in parsing bank files or calculating sums/balances.
   - Store all monetary amounts strictly as signed **integer centavos** (`1 PHP = 100 centavos`) enforced by SQLite `CHECK (typeof(amount_centavos) = 'integer')`.
   - Set `PRAGMA synchronous = FULL` on `finance.db` so every committed ledger entry survives sudden wall-outlet power loss.
   - Store timestamps as Unix epoch integers and resolve all calendar-day boundaries strictly in **`Asia/Manila` (`UTC+8`)** so transactions between 12:00 AM and 8:00 AM never shift onto yesterday's UTC date.
2. **Staging Stabilization & Per-Page OCR Gate:**
   - Require two identical file-size + SHA-256 readings spaced 3 seconds apart plus an exclusive read-lock check before parsing any file dropped into the host `ai_inbox` staging directory (once `FULL_READY` is active).
   - Apply a **50-selectable-character text-density gate per individual PDF page**; route low-density pages individually to offline OCR upscaled to 300 DPI grayscale with automatic deskew and adaptive binarization, flagging OCR-imported batches for user review.
3. **Philippine Statement Parsing & Dual-Mode Centavo Reconciliation (BDO, BPI, UnionBank, GCash, Maya):**
   - Apply Unicode NFKC normalization, resolve parentheses `(500.00)` and Unicode en-dashes (`−`) into negative signs before stripping currency symbols (`₱`, `PHP`), and verify decimal separators precede exactly 2 fractional digits.
   - Cluster PDF text blocks by vertical bounding-box `y`-coordinates to merge multi-line wrapped merchant descriptions upward into their parent transaction row.
   - Resolve `MM/DD` dates against the statement's explicit coverage period (`start_date` to `end_date`) so Dec -> Jan rollovers never misdate into the future.
   - For foreign-currency card rows, ingest settled PHP centavos into the primary ledger while retaining original FX currency/amount as metadata.
   - **Dual-Mode Centavo Reconciliation Invariant:**
     1. **Row-by-Row Running-Balance Mode:** Whenever a statement template includes a running-balance column (e.g., BDO, BPI), verify `Balance[i-1] +/- Amount[i] == Balance[i]` across every consecutive row.
     2. **Statement Net-Delta Mode:** Whenever an e-wallet export omits per-row running balances (e.g., GCash, Maya) and provides header/footer totals, verify `Opening_Balance + Sum(Credits) - Sum(Debits) == Closing_Balance`.
     - If either invariant is off by even **1 centavo**, abort ingestion and quarantine the entire statement.
   - **Source-Aware Idempotency & Lifecycle:** Quarantine cross-file duplicate rows while preserving legitimate same-day repeat purchases and same-day authorization + reversal pairs; automatically reconcile `PENDING` authorizations into settled `POSTED` rows; append immutable offsetting credit entries linked via `parent_tx_id` for refunds.

---

## 10. SUBSYSTEM 8: BACKUPS, GOLDEN EVALUATION & DISASTER RECOVERY

1. **Write-Verify-Swap Online Backups:**
   - Execute backups sequentially inside `MAINTENANCE_BUSY` after `PRAGMA wal_checkpoint(TRUNCATE)` using SQLite's native **Online Backup API** (never raw file copies).
   - Write to `.bak.tmp` -> run `PRAGMA integrity_check`, `FTS5` `integrity-check`, and table row-count verification -> atomically rename to timestamped `.bak` -> prune older rolling backups **only after** verification passes.
   - Retain **3 verified rolling snapshots** plus **1 immutable monthly anchor snapshot** (never auto-pruned).
2. **Cold-Boot Power-Loss Recovery Sweep:**
   - Upon VeraCrypt mount, purge any leftover `.bak.tmp` or zero-byte temp files, run `PRAGMA wal_checkpoint(TRUNCATE)`, `PRAGMA integrity_check`, and `FTS5` `integrity-check` on both databases, and automatically restore from the newest verified backup if corruption is detected.
3. **Metadata-Only Rotating Logs & Golden Evaluation Gate:**
   - Log timestamps, state transitions, latencies, token counts, and error codes **only**—never raw prompts, note text, or financial numbers. Cap logs at `5 MB x 3 files = 15 MB` total.
   - Maintain an offline **15-question Golden Evaluation Set** covering RAG grounding, `Asia/Manila` & centavo queries, prompt-injection resistance, token speed, and CPU thermal stability; never delete an existing `.gguf` model until its replacement passes all 15 checks.

---

## 11. MANDATORY PRE-FLIGHT HARDWARE REALITY CHECKS

Before declaring the system live, implement and execute an automated/guided pre-flight check verifying:
1. **Headless Boot & No-Monitor Sanity:** Confirm BIOS does not halt on missing VGA/keyboard, zero GUI modal dialogs exist in the codebase, Windows audio is never targeted for TTS, and phone status/unlock queries succeed cleanly via pre-logon `user=all` Wi-Fi with no monitor plugged in.
2. **Firmware S3 Verification:** `powercfg /a` confirms `Standby (S3)` is active (not Modern Standby `S0ix`), hibernation/Fast Startup (`hiberfil.sys`) is disabled, and S3 sleep/wake works cleanly without EDID (flagging a $2 HDMI dummy plug only if the specific motherboard/iGPU exhibits headless S3 resume quirks).
3. **Offline Router Static IPv4 & TLS SAN Verification:** With the WAN/internet cable unplugged, the mobile phone reliably verifies the pinned self-signed TLS certificate (`SAN = IP:<PC_STATIC_IP>`) and reaches the PC across sleep/wake cycles and simultaneous cold power-ups.
4. **Real Statement Invariant Verification:** Sample exports from the user's actual Philippine bank/e-wallet accounts pass the Dual-Mode integer-centavo reconciliation check without false quarantine.