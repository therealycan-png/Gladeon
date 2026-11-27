"""gladeon-proxy entry point (7.1-7.2). Run as: python3 -m gladeon.proxy.main [--config PATH]."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time

from .. import hygiene
from ..engine.client import Engine
from ..pipeline import Profile
from ..privd.client import PrivdClient
from .app import App
from .controller import Config, Controller
from .reqcache import RequestCache
from .server import Server, tls_context

log = logging.getLogger("gladeon.proxy")

DEFAULT_CONFIG = "/etc/gladeon/proxy.json"
HEALTH_S = 5.0
FLOOR_TICK_S = 600.0


def load_profile(path: str) -> Profile:
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return Profile()
    return Profile(control_tokens=tuple(d.get("control_tokens", ())), stop=tuple(d.get("stop", ())),
                   supports_enable_thinking=bool(d.get("supports_enable_thinking")),
                   pp=d.get("pp"), tg=d.get("tg"))


def read_secret(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    a = ap.parse_args(argv)
    hygiene.install("proxy")
    with open(a.config) as f:
        conf = json.load(f)
    token = read_secret(conf["token_file"]).encode()
    llm_key = read_secret(conf["llm_key_file"])
    phone_ips = frozenset(conf["phone_ips"])
    profiles = {n: load_profile(conf.get("profiles", {}).get(n, "")) for n in ("quick", "deep")}
    ports = conf.get("engine_ports", {})
    engines = {n: Engine(n, llm_key, port=ports.get(n)) for n in ("quick", "deep", "embed")}
    cache = RequestCache()
    ctl = Controller(Config(vault_root=conf.get("vault_root", "/srv/gladeon/vault"),
                            state_dir=conf.get("state_dir", "/var/lib/gladeon"),
                            run_dir=conf.get("run_dir", "/run/gladeon-proxy"),
                            emb_key=conf.get("emb_key", "embed@0@0@0"), profiles=profiles),
                     PrivdClient(conf.get("privd_socket", "/run/gladeon/privd.sock")), engines, cache)
    srv = Server(conf.get("listen", "0.0.0.0"), int(conf.get("port", 8443)),
                 tls_context(conf["cert"], conf["key"]), token, phone_ips, App(ctl, cache))
    srv.start()
    hygiene.sd_notify("READY=1")                     # listener first (7.1.3)
    live = hygiene.Liveness()
    stop = threading.Event()

    def on_term(signum, frame):
        stop.set()
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    def health_loop():
        while not stop.is_set():
            live.beat("health")
            for n, e in engines.items():
                with live.op("health", 6):
                    ok = e.health()
                cur = ctl.s.engines.get(n)
                if ok and cur != "READY":
                    ctl.set_engine(n, "READY")
                elif not ok and cur == "READY":
                    ctl.set_engine(n, "FAILED")
            stop.wait(HEALTH_S)

    def timer_loop():
        last_tick = 0.0
        while not stop.is_set():
            live.beat("timers")
            ctl.auto_lock_tick()
            with live.op("timers", 600):
                ctl.maintenance_tick()
            if time.monotonic() - last_tick >= FLOOR_TICK_S:
                with live.op("timers", 10):
                    ctl.privd.call("clock-floor-tick")
                last_tick = time.monotonic()
            stop.wait(5)

    for fn in (health_loop, timer_loop):
        threading.Thread(target=fn, name=fn.__name__, daemon=True).start()
    def startup():
        ctl.clock_check()
        ctl.reconcile()
    threading.Thread(target=startup, name="reconcile", daemon=True).start()   # 7.1.4
    ctl.privd.call("unit-start", {"unit": "llm-quick"})
    while not stop.wait(5):
        if not live.stale():
            hygiene.sd_notify("WATCHDOG=1")
    # 6.3.8: system stopping -> lock steps 1-6 + lock-intent-set; proxy-only stop -> full lock
    hygiene.sd_notify("STOPPING=1")
    stopping = ctl.privd.call("system-stopping")
    if ctl.s.vault in ("READY", "UNLOCKING", "RECOVERING", "LOCK_FAILED"):
        ctl.lock()
        end = time.monotonic() + 100
        while ctl.s.vault not in ("LOCKED", "LOCK_FAILED") and time.monotonic() < end:
            time.sleep(0.2)
    if not stopping.ok or stopping.data.get("stopping", True):
        ctl.privd.call("lock-intent-set")
    srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
