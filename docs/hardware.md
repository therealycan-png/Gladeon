# Hardware (from Shawn's photos, 2026-11-28)

Draft. Items marked **?** need a check on the PC (BIOS screen, stick labels, or `python3 -m gladeon.hwprobe`).

| Part | What the photos show | Notes for Gladeon |
|---|---|---|
| Motherboard | ASUS PRIME B250M-A (LGA1151, Intel B250) | 4 DDR4 slots, 6 SATA, 2 M.2 (both look empty), Intel I219-V Ethernet with LANGuard (good Linux support), TPM header (no module fitted) |
| CPU | Intel 6th/7th gen (Skylake/Kaby Lake), stock Intel cooler E97379-003 | **? exact model.** Must have AVX2 for usable llama.cpp speed: i3/i5/i7 have it; Pentium/Celeron of this generation do **not** |
| TPM | No module on the header | Intel PTT (firmware TPM 2.0) should be available on this chipset; **? enable "PTT" in BIOS** → Clevis PCR 7 root unlock (3.2.3) can work |
| RAM | 2 × Kingston HyperX FURY DDR4 in 2 of 4 slots | **? size per stick** (label on the stick). Decides the model tier (8.2) |
| Storage | 1 × 2.5" SSD (top, dark label), 1 × WD Green 240 GB SATA SSD, 2–3 more WD Green 2.5" drives in the lower cage | **? what is on each, and which to keep.** One 240 GB SSD is enough for Gladeon (root ≥ 64 GB + vault). A separate SSD can hold Windows later, which keeps the dual-boot simpler |
| Expansion card | One card in the x16 slot with a long cable | **? graphics card or Wi-Fi card.** The board has no Wi-Fi; Wi-Fi needs a card or USB adapter. Gladeon is CPU-only, so a graphics card is not needed |
| PSU | Thermaltake Litepower 650 W (LTP-650AL), not 80 Plus rated | Plenty of power (load is about 60–120 W). It is a budget unit; fine to start, consider a better one for years of 24/7 use |
| Condition | Heavy dust on the PSU, case floor and CPU heatsink | Clean the CPU heatsink and fans before setup. Long LLM runs hold the CPU at full load and a clogged heatsink makes it throttle |

## Rough speed expectations (estimates, to be measured by M9)

Assumes a 4-core i5/i7 with AVX2 and dual-channel DDR4-2400.

| Model | Answer speed | Fits the spec deadlines? |
|---|---|---|
| QUICK 3–4B Q4_K_M | about 8–12 tokens/s | likely yes (60 s QUICK deadline) |
| DEEP 7–9B Q4_K_M | about 4–6 tokens/s | tight; pre-flight will shrink the DEEP budget (8.6.7) or mark DEEP unavailable |

If the CPU is a Pentium/Celeron (no AVX2), expect roughly half these speeds or less.
