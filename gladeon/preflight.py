"""Proxy-side pre-flight checks (19.1) that need no model: SQLite, TLS, disk, interfaces, post-filter."""
from __future__ import annotations

import datetime as dt
import ipaddress
import math
import os
import sqlite3
import ssl
import subprocess
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional

from .brain.maintenance import DISK_FLOOR_BYTES
from .prompts import post_filter


@dataclass
class Check:
    id: str
    status: str          # PASS | WARN | FAIL
    detail: str = ""


def sqlite_check(min_version=(3, 53, 0), lib_path: Optional[str] = "/opt/gladeon/bin/libsqlite3.so.0") -> Check:
    if sqlite3.sqlite_version_info < min_version:
        return Check("SQLITE", "FAIL", f"version {sqlite3.sqlite_version}")
    try:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')")
        db.close()
    except sqlite3.OperationalError:
        return Check("SQLITE", "FAIL", "no FTS5")
    if not hasattr(math, "sumprod"):
        return Check("SQLITE", "FAIL", "math.sumprod missing")
    if lib_path:
        try:
            with open("/proc/self/maps") as f:
                loaded = any(lib_path in line for line in f)
        except OSError:
            loaded = False
        if not loaded:
            return Check("SQLITE", "WARN", "pinned libsqlite3 not loaded")
    return Check("SQLITE", "PASS", sqlite3.sqlite_version)


def tls_check(cert_path: str, ips: Iterable[str], now: Optional[dt.datetime] = None) -> Check:
    try:
        info = ssl._ssl._test_decode_cert(cert_path)        # stdlib decoder, no network
    except (OSError, ssl.SSLError, ValueError):
        return Check("TLS", "FAIL", "unreadable certificate")
    sans = {v for k, v in info.get("subjectAltName", ()) if k == "IP Address"}
    missing = [ip for ip in ips if ip and str(ipaddress.ip_address(ip.split("/")[0])) not in sans]
    if missing:
        return Check("TLS", "FAIL", f"SAN missing {','.join(missing)}")
    exp = dt.datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if exp <= now:
        return Check("TLS", "FAIL", "expired")
    if (exp - now).days < 60:
        return Check("TLS", "WARN", f"expires in {(exp - now).days} days")
    return Check("TLS", "PASS")


def disk_check(path: str = "/", floor: int = DISK_FLOOR_BYTES) -> Check:
    st = os.statvfs(path)
    free = st.f_bavail * st.f_frsize
    return Check("DISK_ROOT", "PASS" if free >= floor else "FAIL", f"{free // (1024 ** 2)} MB free")


def clock_check(now_epoch: int, floor: Optional[int]) -> Check:
    if floor is None:
        return Check("CLOCK", "FAIL", "privd unreachable; floor unknown")
    return Check("CLOCK", "PASS" if now_epoch >= floor else "WARN", "" if now_epoch >= floor else "CLOCK_UNTRUSTED")


def iface_check(names=("gleth0", "glwlan0"), root: str = "/sys/class/net") -> Check:
    states = {}
    for n in names:
        try:
            with open(os.path.join(root, n, "operstate")) as f:
                states[n] = f.read().strip()
        except OSError:
            states[n] = "missing"
    up = [n for n, s in states.items() if s == "up"]
    detail = ", ".join(f"{n}={s}" for n, s in states.items())
    if not up:
        return Check("IFACES", "FAIL", detail)
    return Check("IFACES", "PASS" if states.get("gleth0") == "up" else "WARN", detail)


FIXED_STRINGS = ["a\n\nb", "x\ny", "Pwede ba tayong mag-meet bukas? Sure, 3pm works.",
                 "| a | b |\n|---|---|\n| 1 | 2 |"]


def postfilter_check(control_tokens: Iterable[str], corpus: Iterable[str]) -> Check:
    """8.6.5 deterministic part: fixed strings unchanged; no control token in a reference corpus."""
    toks = [t for t in control_tokens if t and not t.isspace()]
    for s in FIXED_STRINGS:
        if post_filter(s, toks) != s:
            return Check("POSTFILTER", "FAIL", "fixed string changed")
    for line in corpus:
        if any(t in line for t in toks):
            return Check("POSTFILTER", "FAIL", "control token in corpus")
    return Check("POSTFILTER", "PASS")


def run_all(cert: str, pc_ips: Iterable[str], control_tokens: Iterable[str], corpus: Iterable[str],
            floor_epoch: Optional[int], now_epoch: int) -> list[dict]:
    checks = [sqlite_check(), tls_check(cert, pc_ips), disk_check("/"), clock_check(now_epoch, floor_epoch),
              iface_check(), postfilter_check(control_tokens, corpus)]
    return [asdict(c) for c in checks]
