"""Pure helpers for gladeon-admin."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
from typing import Iterable, Optional

from ..atomic import atomic_write

MIN_PASSPHRASE = 20
ID_RE = re.compile(r"^[0-9a-f]{12}$")
ID_TTL_S = 3600


def escape(name: str) -> str:
    """5.3: Cc/Cf/Cs characters (incl. surrogate-escaped bytes) and backslash -> \\xNN / \\u{...}."""
    out = []
    for ch in name:
        o = ord(ch)
        if ch == "\\":
            out.append("\\x5c")
        elif 0xDC80 <= o <= 0xDCFF:                    # surrogateescape byte from os.fsdecode
            out.append(f"\\x{o - 0xDC00:02x}")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            out.append(f"\\x{o:02x}" if o < 0x100 else f"\\u{{{o:x}}}")
        else:
            out.append(ch)
    return "".join(out)


def canonical_passphrase(pw: str) -> bytes:
    """9.2: NFC UTF-8 bytes, nothing else changed; >= 20 characters after canonicalization."""
    c = unicodedata.normalize("NFC", pw)
    if len(c) < MIN_PASSPHRASE:
        raise ValueError("PASSPHRASE_TOO_SHORT")
    return c.encode("utf-8")


def new_passphrase(first: str, second: str) -> bytes:
    """New passphrases are typed twice and compared before privd is called (R6: B6-8)."""
    if unicodedata.normalize("NFC", first) != unicodedata.normalize("NFC", second):
        raise ValueError("PASSPHRASE_MISMATCH")
    return canonical_passphrase(first)


class FileIds:
    """12-hex IDs from a keyed hash of the relpath with a per-listing random key; map kept 1 h."""

    def __init__(self, run_dir: str, listing: str, now=time.time):
        if not re.fullmatch(r"[a-z0-9-]{1,32}", listing):
            raise ValueError("bad listing name")
        self.path = os.path.join(run_dir, f"ids-{listing}.json")
        self.now = now
        self.key = secrets.token_bytes(32)
        self.map: dict[str, str] = {}

    def add(self, relpath: str) -> str:
        i = hmac.new(self.key, relpath.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()[:12]
        self.map[i] = relpath
        return i

    def save(self) -> None:
        atomic_write(self.path, json.dumps({"created": self.now(), "ids": self.map}).encode())

    @staticmethod
    def resolve(run_dir: str, listing: str, file_id: str, now=time.time) -> Optional[str]:
        if not ID_RE.fullmatch(file_id):
            return None
        try:
            with open(os.path.join(run_dir, f"ids-{listing}.json")) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if now() - float(d.get("created", 0)) > ID_TTL_S:
            return None
        return d.get("ids", {}).get(file_id)


BEGIN, END = "-----BEGIN GLADEON {}-----", "-----END GLADEON {}-----"


def armor(data: bytes, kind: str = "BUNDLE") -> str:
    b = base64.b64encode(data).decode()
    lines = [BEGIN.format(kind)] + [b[i:i + 64] for i in range(0, len(b), 64)] + [END.format(kind),
             f"SHA256: {hashlib.sha256(data).hexdigest()}"]
    return "\n".join(lines) + "\n"


def dearmor(text: str, kind: str = "BUNDLE") -> bytes:
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    try:
        a, b = lines.index(BEGIN.format(kind)), lines.index(END.format(kind))
    except ValueError:
        raise ValueError("ARMOR_MISSING")
    data = base64.b64decode("".join(lines[a + 1:b]), validate=True)
    sha = next((ln.split(":", 1)[1].strip() for ln in lines[b + 1:] if ln.startswith("SHA256:")), None)
    if sha != hashlib.sha256(data).hexdigest():
        raise ValueError("ARMOR_HASH_MISMATCH")
    return data


def make_bundle(cert_pem: bytes, token: str, pin: str) -> bytes:
    return json.dumps({"cert.pem": cert_pem.decode(), "token": token, "pin": pin}).encode()


def spki_pin(cert_pem: bytes) -> str:
    """curl --pinnedpubkey format: base64(sha256(DER SubjectPublicKeyInfo))."""
    import subprocess
    pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=cert_pem, capture_output=True, check=True).stdout
    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pub, capture_output=True, check=True).stdout
    return base64.b64encode(hashlib.sha256(der).digest()).decode()
