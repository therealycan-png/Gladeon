"""gladeon-privd entry point: wires verbs to the daemon (5.2). Run as root by systemd."""
from __future__ import annotations

import argparse
import grp
import logging
import os
import pwd
import signal
import threading

from .. import hygiene
from .clock import ClockState
from .daemon import Call, Privd, Result
from .protocol import BY_NAME
from .vault import MOUNTPOINT, System, Vault

log = logging.getLogger("gladeon.privd")
STATE = "/var/lib/gladeon-privd"
RUN = "/run/gladeon"
SOCK = "/run/gladeon/privd.sock"
UNIT_NAMES = {"llm-quick": "gladeon-llm@quick.service", "llm-deep": "gladeon-llm@deep.service",
              "llm-embed": "gladeon-llm@embed.service", "sync": "gladeon-sync.service",
              "wifi-tune": "gladeon-wifi-tune.service"}


def holders(where: str = MOUNTPOINT) -> list[str]:
    """`comm` names of processes whose cwd, fds or maps reference the vault (5.2.14)."""
    out = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        base = f"/proc/{pid}"
        try:
            refs = [os.readlink(f"{base}/cwd"), os.readlink(f"{base}/root")]
            refs += [os.readlink(f"{base}/fd/{fd}") for fd in os.listdir(f"{base}/fd")]
            with open(f"{base}/maps") as f:
                refs += [line.rsplit(None, 1)[-1] for line in f if "/" in line]
            if any(r == where or r.startswith(where + "/") for r in refs):
                with open(f"{base}/comm") as f:
                    out.add(f.read().strip().encode("unicode_escape").decode()[:32])
        except OSError:
            continue
    return sorted(out)


def build(sys_: System, gladeon_uid: int, sock: str = SOCK) -> Privd:
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    os.makedirs(RUN, exist_ok=True)

    def expected_id():
        try:
            with open(os.path.join(STATE, "vault_id")) as f:
                return f.read().strip() or None
        except OSError:
            return None
    vault = Vault(STATE, RUN, expected_id, sys_)
    import time
    clock = ClockState(STATE, time.time, time.monotonic,
                       lambda t: sys_.run(["date", "-s", f"@{int(t)}"], timeout=10) == 0
                       and sys_.run(["hwclock", "--systohc"], timeout=30) == 0)
    holder = {"p": None}

    def status(c: Call) -> Result:
        return vault.status(c, holder["p"].lock_holder if holder["p"] else None, clock.floor)

    def lock_intent(c: Call) -> Result:
        vault.set_lock_intent(True)
        return Result(0)

    def tick(c: Call) -> Result:
        return Result(0, {"floor": clock.tick()})

    def set_time(c: Call, force: bool = False) -> Result:
        t = c.args.get("epoch")
        if not isinstance(t, int):
            return Result(BY_NAME["PRIVD_INTERNAL"])
        d = clock.set_time(t, force=force)
        if d.ok:
            return Result(0, {"floor": clock.floor, "charged_s": d.charged})
        return Result(BY_NAME.get(d.code, 22) if d.code != "BELOW_FLOOR" else BY_NAME["CLOCK_BUDGET_EXCEEDED"],
                      {"reason": d.code})

    def stopping(c: Call) -> Result:
        state = sys_.output(["systemctl", "is-system-running"], timeout=5)
        return Result(0, {"stopping": state is None or state.strip() == "stopping"})   # error counts as stopping

    def unit(action: str):
        def fn(c: Call) -> Result:
            u = UNIT_NAMES[c.args["unit"]]
            if action in ("start", "restart"):
                sys_.run(["systemctl", "reset-failed", u], timeout=10)
            argv = ["systemctl", "stop", u] if action == "stop" else ["systemctl", "--no-block", action, u]
            rc = sys_.run(argv, timeout=30)
            return Result(0) if rc == 0 else Result(BY_NAME["UNIT_START_REFUSED"], tool_exit_code=rc)
        return fn

    def power(action: str):
        def fn(c: Call) -> Result:
            st = vault.status().data
            if (st["copy_active"] or st["rotation"]["state"] == "erasing") and not (c.uid == 0 and c.args.get("force")):
                return Result(BY_NAME["JOB_IN_PROGRESS"])
            rc = sys_.run(["systemctl", "--no-block", action], timeout=10)
            return Result(0) if rc == 0 else Result(BY_NAME["PRIVD_INTERNAL"], tool_exit_code=rc)
        return fn

    verbs = {
        "vault-status": status, "vault-open": vault.open,
        "vault-close": lambda c: vault.close(c, holders), "lock-intent-set": lock_intent,
        "clock-floor-tick": tick, "set-time": set_time, "set-time-force": lambda c: set_time(c, True),
        "clock-budget-reset": lambda c: (clock.budget_reset(), Result(0))[1],
        "clock-floor-set": lambda c: (clock.floor_set(int(c.args["epoch"])), Result(0))[1],
        "system-stopping": stopping, "unit-start": unit("start"), "unit-stop": unit("stop"),
        "unit-restart": unit("restart"), "poweroff": power("poweroff"), "reboot": power("reboot"),
        "selfcheck": lambda c: Result(0, {"checks": []}),
    }
    p = Privd(sock, gladeon_uid, verbs)
    os.chown(sock, 0, grp.getgrnam("gladeon").gr_gid)
    holder["p"] = p
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-mode", action="store_true")
    ap.parse_args(argv)
    hygiene.install("privd")
    p = build(System(), pwd.getpwnam("gladeon").pw_uid)
    p.start()
    hygiene.sd_notify("READY=1")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    stop.wait()
    # stop handler: close the mapping if systemd already unmounted the vault (6.3.8)
    sys_ = System()
    if not sys_.mounted() and os.path.exists("/dev/mapper/gladeon_vault"):
        sys_.run(["cryptsetup", "close", "gladeon_vault"], timeout=60)
    p.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
