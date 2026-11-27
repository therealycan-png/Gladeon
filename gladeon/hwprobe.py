"""Hardware probe for pre-flight and docs/hardware.md (3.x, 8.2, 8.3, 8.5).
Read-only: reads /proc and /sys only. Run on the PC: python3 -m gladeon.hwprobe [--json]."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

GIB = 1024 ** 3


@dataclass
class Hardware:
    cpu_model: str = ""
    logical_cpus: int = 0
    physical_cores: int = 0
    core_types: dict = field(default_factory=dict)     # "P"/"E"/"?" -> core count
    l3_groups: list = field(default_factory=list)      # list of core-id lists sharing one L3
    flags: list = field(default_factory=list)          # selected ISA flags (avx2, avx512f, ...)
    mem_total_gib: float = 0.0
    zram_limit_gib: float = 0.0
    tpm2: bool = False
    secure_boot: Optional[bool] = None
    disks: list = field(default_factory=list)          # {name, size_gib, rotational, model}
    nics: list = field(default_factory=list)           # {name, mac, wireless, state}
    gpu_present: bool = False


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def probe(root: str = "/") -> Hardware:
    j = lambda *p: os.path.join(root, *p)   # noqa: E731
    hw = Hardware()
    cpuinfo = _read(j("proc/cpuinfo"))
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    hw.cpu_model = m.group(1).strip() if m else ""
    fl = re.search(r"^flags\s*:\s*(.+)$", cpuinfo, re.M)
    wanted = ("avx", "avx2", "fma", "f16c", "avx512f", "avx512_vnni", "avx_vnni", "amx_tile")
    have = set(fl.group(1).split()) if fl else set()
    hw.flags = [f for f in wanted if f in have]
    cpus = sorted(int(d[3:]) for d in os.listdir(j("sys/devices/system/cpu")) if re.fullmatch(r"cpu\d+", d)) \
        if os.path.isdir(j("sys/devices/system/cpu")) else []
    hw.logical_cpus = len(cpus) or cpuinfo.count("processor\t:")
    cores, l3 = {}, {}
    for c in cpus:
        base = j(f"sys/devices/system/cpu/cpu{c}")
        core = (_read(f"{base}/topology/package_id").strip(), _read(f"{base}/topology/core_id").strip())
        cores.setdefault(core, []).append(c)
        for idx in range(8):
            if _read(f"{base}/cache/index{idx}/level").strip() == "3":
                l3.setdefault(_read(f"{base}/cache/index{idx}/shared_cpu_list").strip(), set()).add(core)
    hw.physical_cores = len(cores) or hw.logical_cpus
    hw.l3_groups = [sorted(int(k[1]) for k in v if k[1].isdigit()) for v in l3.values()]
    # hybrid CPUs: Intel exposes cpu_core / cpu_atom PMUs listing their CPUs
    pcpus = _cpulist(_read(j("sys/devices/cpu_core/cpus")))
    ecpus = _cpulist(_read(j("sys/devices/cpu_atom/cpus")))
    if pcpus or ecpus:
        hw.core_types = {"P": len({k for k, v in cores.items() if set(v) & pcpus}),
                         "E": len({k for k, v in cores.items() if set(v) & ecpus})}
    else:
        hw.core_types = {"?": hw.physical_cores}
    mi = _read(j("proc/meminfo"))
    mt = re.search(r"^MemTotal:\s+(\d+) kB", mi, re.M)
    hw.mem_total_gib = round(int(mt.group(1)) * 1024 / GIB, 2) if mt else 0.0
    zl = _read(j("sys/block/zram0/mem_limit")).strip()
    hw.zram_limit_gib = round(int(zl) / GIB, 2) if zl.isdigit() else 0.0
    hw.tpm2 = _read(j("sys/class/tpm/tpm0/tpm_version_major")).strip() == "2"
    sb = [f for f in (os.listdir(j("sys/firmware/efi/efivars")) if os.path.isdir(j("sys/firmware/efi/efivars")) else [])
          if f.startswith("SecureBoot-")]
    if sb:
        try:
            with open(j("sys/firmware/efi/efivars", sb[0]), "rb") as f:
                hw.secure_boot = f.read()[-1:] == b"\x01"
        except OSError:
            hw.secure_boot = None
    blk = j("sys/block")
    for name in sorted(os.listdir(blk)) if os.path.isdir(blk) else []:
        if name.startswith(("loop", "ram", "zram", "dm-", "sr")):
            continue
        size = _read(f"{blk}/{name}/size").strip()
        hw.disks.append({"name": name, "size_gib": round(int(size) * 512 / GIB, 1) if size.isdigit() else 0,
                         "rotational": _read(f"{blk}/{name}/queue/rotational").strip() == "1",
                         "model": _read(f"{blk}/{name}/device/model").strip()})
    net = j("sys/class/net")
    for name in sorted(os.listdir(net)) if os.path.isdir(net) else []:
        if name == "lo" or not os.path.exists(f"{net}/{name}/device"):
            continue
        hw.nics.append({"name": name, "mac": _read(f"{net}/{name}/address").strip(),
                        "wireless": os.path.isdir(f"{net}/{name}/wireless"),
                        "state": _read(f"{net}/{name}/operstate").strip()})
    drm = j("sys/class/drm")
    hw.gpu_present = os.path.isdir(drm) and any(re.fullmatch(r"card\d+", d) for d in os.listdir(drm))
    return hw


def _cpulist(s: str) -> set[int]:
    out: set[int] = set()
    for part in s.strip().split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


# ---- 8.2 model tier, 8.3 RAM plan -----------------------------------------------------------
def tier(mem_gib: float) -> dict:
    if mem_gib < 12:
        return {"tier": "<12", "quick": "3-4B Q4_K_M", "deep": "none (or same family Q6_K)"}
    if mem_gib < 24:
        return {"tier": "12-24", "quick": "3-4B Q4_K_M", "deep": "7-9B Q4_K_M"}
    if mem_gib < 48:
        return {"tier": "24-48", "quick": "4B Q5_K_M", "deep": "12-14B Q4_K_M"}
    return {"tier": ">=48", "quick": "4B Q8_0", "deep": "12-14B Q6_K"}


OS_GIB = 1.5
LUKS_GIB = 0.5


def need_gib(engines_gib: list[float]) -> float:
    return round((sum(engines_gib) + OS_GIB + LUKS_GIB) * 1.2, 2)


def ram_plan(mem_total_gib: float, zram_limit_gib: float, quick: float, embed: float, deep: Optional[float]) -> str:
    avail = mem_total_gib - zram_limit_gib
    if need_gib([quick, embed]) > avail:
        return "DEGRADED(RAM_INSUFFICIENT)"
    if deep is None:
        return "unavailable"
    if need_gib([quick, embed, deep]) <= avail:
        return "resident"
    if need_gib([deep, embed]) <= avail:
        return "swap"
    return "unavailable"


def markdown(hw: Hardware) -> str:
    t = tier(hw.mem_total_gib)
    lines = ["# Hardware (auto-detected)", "",
             f"- CPU: {hw.cpu_model or 'unknown'}; {hw.physical_cores} cores / {hw.logical_cpus} threads; "
             f"types {hw.core_types}; ISA {', '.join(hw.flags) or 'none detected'}",
             f"- L3 groups (core ids): {hw.l3_groups or 'unknown'}",
             f"- RAM: {hw.mem_total_gib} GiB (zram limit {hw.zram_limit_gib} GiB) → model tier {t['tier']}: "
             f"QUICK {t['quick']}, DEEP {t['deep']}",
             f"- TPM 2.0: {'yes' if hw.tpm2 else 'no (root stays unencrypted, ROOT_NOT_ENCRYPTED)'}; "
             f"Secure Boot: {hw.secure_boot}",
             f"- GPU device present: {hw.gpu_present} (Gladeon is CPU-only either way)", "", "## Disks"]
    lines += [f"- {d['name']}: {d['size_gib']} GiB, {'HDD' if d['rotational'] else 'SSD'} {d['model']}" for d in hw.disks]
    lines += ["", "## Network interfaces"]
    lines += [f"- {n['name']}: {'Wi-Fi' if n['wireless'] else 'Ethernet'} {n['mac']} ({n['state']})" for n in hw.nics]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    hw = probe()
    print(json.dumps(asdict(hw), indent=2) if "--json" in argv else markdown(hw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
