"""privd vault verbs (5.2.8-5.2.10, 9.4.2, 9.5.7). All tool calls go through a System object."""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from ..atomic import atomic_write
from .daemon import Call, Result
from .protocol import BY_NAME

DEVICE = "/dev/gladeon/vault"
MAPPING = "gladeon_vault"
MAPPER = f"/dev/mapper/{MAPPING}"
MOUNTPOINT = "/srv/gladeon/vault"
MOUNT_UNIT = "srv-gladeon-vault.mount"
ID_FILE = ".gladeon-vault-id"
CRYPT_CODES = {2: "BAD_PASSPHRASE", 4: "DEVICE_MISSING", 5: "DEVICE_BUSY_OR_OPEN", 3: "UNLOCK_OUT_OF_MEMORY"}


class System:
    """Real system access. Child stdout is captured and discarded, stderr goes to DEVNULL (5.2.2)."""

    def run(self, argv: Sequence[str], stdin: Optional[bytes] = None, timeout: float = 60) -> int:
        try:
            p = subprocess.run(list(argv), input=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=timeout)
            return p.returncode
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 127

    def output(self, argv: Sequence[str], timeout: float = 10) -> Optional[str]:
        """Short stdout of a query tool (never logged); None on error."""
        try:
            p = subprocess.run(list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
            return p.stdout[:4096].decode("utf-8", "replace")
        except (subprocess.TimeoutExpired, OSError):
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def mounted(self, where: str = MOUNTPOINT) -> bool:
        try:
            with open("/proc/self/mountinfo") as f:
                return any(line.split()[4] == where for line in f if len(line.split()) > 4)
        except OSError:
            return False

    def read_id(self) -> Optional[str]:
        try:
            fd = os.open(os.path.join(MOUNTPOINT, ID_FILE), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                return f.read(128).strip()
        except OSError:
            return None

    def unit_active(self, unit: str) -> bool:
        return self.run(["systemctl", "is-active", "--quiet", unit], timeout=10) == 0

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def monotonic(self) -> float:
        return time.monotonic()


class Vault:
    def __init__(self, state_dir: str, run_dir: str, expected_id: Callable[[], Optional[str]],
                 sys: Optional[System] = None):
        self.state_dir, self.run_dir = state_dir, run_dir
        self.expected_id = expected_id
        self.sys = sys or System()

    # ---- small persisted flags -------------------------------------------------------------
    def _flag(self, base: str, name: str) -> str:
        return os.path.join(base, name)

    @property
    def lock_intent(self) -> bool:
        return os.path.exists(self._flag(self.run_dir, "lock_intent"))

    def set_lock_intent(self, on: bool) -> None:
        p = self._flag(self.run_dir, "lock_intent")
        if on:
            atomic_write(p, b"1")
        elif os.path.exists(p):
            os.unlink(p)

    def _meta(self) -> dict:
        try:
            with open(self._flag(self.state_dir, "vault_meta.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _set_meta(self, **kw) -> None:
        m = self._meta()
        m.update(kw)
        atomic_write(self._flag(self.state_dir, "vault_meta.json"), json.dumps(m).encode())

    def _update_window(self) -> bool:
        return os.path.exists(self._flag(self.state_dir, "update_window"))

    def _rotation_state(self) -> str:
        try:
            with open(self._flag(self.state_dir, "rotation.json")) as f:
                return json.load(f).get("state", "none")
        except (OSError, ValueError):
            return "none"

    # ---- status (5.2.10) -------------------------------------------------------------------
    def status(self, call: Optional[Call] = None, lock_holder: Optional[str] = None, floor: int = 0) -> Result:
        s = self.sys
        mapping = s.exists(MAPPER)
        mounted = s.mounted()
        exp = self.expected_id()
        m = self._meta()
        return Result(0, {
            "mapping_open": mapping, "mounted": mounted,
            "id_ok": bool(mounted and exp and s.read_id() == exp),
            "lock_intent": self.lock_intent, "lock_holder": lock_holder,
            "copy_active": s.unit_active("gladeon-offline-copy.service"),
            "copy_progress": None, "copy_result": None,
            "rotation": {"state": self._rotation_state()},
            "header_backup_stale": bool(m.get("header_backup_stale")),
            "update_window": self._update_window(),
            "fsck_last_s": m.get("fsck_last_s", 0), "last_close_clean": m.get("last_close_clean", False),
            "floor": floor})

    # ---- open (5.2.8, 9.4.2) ---------------------------------------------------------------
    def open(self, call: Call) -> Result:
        self.set_lock_intent(False)               # cleared when the verb lock is acquired
        if self._update_window():
            return Result(BY_NAME["UPDATE_WINDOW_OPEN"])
        if self.sys.unit_active("gladeon-offline-copy.service"):
            return Result(BY_NAME["COPY_IN_PROGRESS"])
        if self._rotation_state() == "erasing":
            return Result(BY_NAME["ROTATION_IN_PROGRESS"])
        if not call.client_alive():
            return Result(BY_NAME["PRIVD_INTERNAL"])
        s, exp = self.sys, self.expected_id()
        if s.exists(MAPPER):
            if s.mounted() and exp and s.read_id() == exp:
                return self._reply_or_unwind(call, Result(0, {"already_open": True}))
            self._unwind()
        if not call.secrets:
            return Result(BY_NAME["PRIVD_INTERNAL"])
        rc = s.run(["cryptsetup", "open", "--type", "luks2", "--key-file=-", DEVICE, MAPPING],
                   stdin=bytes(call.secrets[0]), timeout=120)
        if rc != 0:
            return Result(BY_NAME.get(CRYPT_CODES.get(rc, "PRIVD_INTERNAL"), 19), tool_exit_code=rc)
        self._set_meta(last_close_clean=False)    # written (fsync) before mounting (R10: A10-1)
        t0 = s.monotonic()
        rc = s.run(["fsck.ext4", "-p", MAPPER], timeout=3600)
        self._set_meta(fsck_last_s=round(s.monotonic() - t0, 1))
        if rc >= 4:
            self._unwind()
            return Result(BY_NAME["FSCK_FAILED"], tool_exit_code=rc)
        if s.run(["systemctl", "start", MOUNT_UNIT], timeout=60) != 0 or not s.mounted():
            self._unwind()
            return Result(BY_NAME["MOUNT_FAILED"])
        if not exp or s.read_id() != exp:
            self._unwind()
            return Result(BY_NAME["VAULT_ID_MISMATCH"])
        return self._reply_or_unwind(call, Result(0))

    def _reply_or_unwind(self, call: Call, ok: Result) -> Result:
        if self.lock_intent:
            self._unwind()
            return Result(BY_NAME["LOCK_REQUESTED"])
        if not call.client_alive():               # client gone at reply time (R7: B7-7)
            self._unwind()
            return Result(BY_NAME["PRIVD_INTERNAL"])
        return ok

    def _unwind(self) -> bool:
        s = self.sys
        if s.mounted():
            if not self._umount():
                return False
        if s.exists(MAPPER):
            return s.run(["cryptsetup", "close", MAPPING], timeout=60) == 0
        return True

    def _umount(self) -> bool:
        s = self.sys
        for i in range(3):                        # retry 3x over 10 s, directly, not via the mount unit
            if s.run(["umount", MOUNTPOINT], timeout=20) == 0 and not s.mounted():
                self._set_meta(last_close_clean=True)
                return True
            if i < 2:
                s.sleep(5)
        return False

    # ---- close (9.5.7) ---------------------------------------------------------------------
    def close(self, call: Optional[Call] = None, holders: Callable[[], list] = lambda: []) -> Result:
        self.set_lock_intent(True)
        s = self.sys
        s.run(["systemctl", "stop", "gladeon-sync.service"], timeout=20)
        if s.mounted() and not self._umount():
            return Result(BY_NAME["UMOUNT_BUSY"], {"holders": holders()})
        if s.exists(MAPPER) and s.run(["cryptsetup", "close", MAPPING], timeout=60) != 0:
            return Result(BY_NAME["DEVICE_BUSY_OR_OPEN"])
        return Result(0)
