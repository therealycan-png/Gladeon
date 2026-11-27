"""gladeon-admin (root, via sudo over SSH). Verbs that need no live vault.

Files are written atomically. `--root DIR` stages every file under DIR and runs no system
command, for tests and for a dry look before applying."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from typing import Optional

from ..atomic import atomic_write
from ..deploy import NetConfig, render_all
from . import keys as K
from .common import armor, make_bundle, spki_pin

DEFAULT_CONF = "/etc/gladeon/proxy.json"
NET_CONF = "/etc/gladeon/network.json"
KEYS_DB = "/etc/gladeon/ssh_keys.json"
SSHD_KEYS = "/home/shawn/.ssh/authorized_keys"
DROPBEAR_KEYS = "/etc/dropbear/initramfs/authorized_keys"
DROPINS = ("/etc/nftables.conf", "/etc/systemd/system/gladeon-proxy.service.d/ip.conf",
           "/etc/systemd/system/gladeon-sync.service.d/ip.conf")


class Admin:
    def __init__(self, root: str = "", run=None, out=sys.stdout):
        self.root = root.rstrip("/")
        self.out = out
        self.ran: list[list[str]] = []
        self._run = run or (lambda argv: subprocess.run(argv, stdout=subprocess.DEVNULL,
                                                       stderr=subprocess.DEVNULL).returncode)

    def p(self, path: str) -> str:
        return self.root + path

    def read(self, path: str, default: Optional[str] = None) -> str:
        try:
            with open(self.p(path)) as f:
                return f.read()
        except FileNotFoundError:
            if default is None:
                raise
            return default

    def write(self, path: str, text: str, mode: int = 0o600) -> None:
        os.makedirs(os.path.dirname(self.p(path)), exist_ok=True)
        atomic_write(self.p(path), text.encode(), mode)

    def run(self, *argv: str) -> int:
        self.ran.append(list(argv))
        return 0 if self.root else self._run(list(argv))

    # ---- config ----------------------------------------------------------------------------
    def net(self) -> NetConfig:
        return NetConfig(**json.loads(self.read(NET_CONF)))

    def boot_mounted(self) -> bool:
        if self.root:
            return True
        try:
            with open("/proc/self/mountinfo") as f:
                points = {ln.split()[4] for ln in f}
        except OSError:
            return False
        return "/boot" in points and "/boot/efi" in points

    # ---- verbs -----------------------------------------------------------------------------
    def client_bundle(self, conf_path: str) -> int:
        conf = json.loads(self.read(conf_path))
        cert = self.read(conf["cert"]).encode()
        token = self.read(conf["token_file"]).strip()
        ips = conf.get("pc_ips", {})
        data = json.loads(make_bundle(cert, token, spki_pin(cert)))
        data.update({"pc_ip_eth": ips.get("eth", ""), "pc_ip_wlan": ips.get("wlan", ""),
                     "port": conf.get("port", 8443)})
        self.out.write(armor(json.dumps(data).encode()))
        return 0

    def _write_keys(self, keys: list) -> int:
        n = self.net()
        self.write(KEYS_DB, K.dump(keys))
        self.write(SSHD_KEYS, K.authorized_keys(keys, n.phone_ip, n.admin_ip))
        self.write(DROPBEAR_KEYS, K.dropbear_keys(keys))
        return self.run("update-initramfs", "-u", "-k", "all")

    def key_add(self, role: str, pubkey: str) -> int:
        if not self.boot_mounted():
            print("BOOT_NOT_MOUNTED", file=sys.stderr)
            return 3
        keys = K.add(K.load(self.read(KEYS_DB, "[]")), K.parse_pubkey(role, pubkey))
        return self._write_keys(keys)

    def key_remove(self, fingerprint: str) -> int:
        if not self.boot_mounted():
            print("BOOT_NOT_MOUNTED", file=sys.stderr)
            return 3
        keys = K.remove(K.load(self.read(KEYS_DB, "[]")), fingerprint)
        return self._write_keys(keys)

    def token_rotate(self, conf_path: str) -> int:
        """New API token; restarting the proxy closes every connection and locks the vault (P30).
        Then run client-bundle again for the phone."""
        import secrets
        conf = json.loads(self.read(conf_path))
        self.write(conf["token_file"], secrets.token_urlsafe(32) + "\n", 0o640)
        return self.run("systemctl", "restart", "gladeon-proxy.service")

    def phone_replace(self, new_ip: str, device_id: str) -> int:
        """Idempotent: every step can be re-run (5.3)."""
        if not K.valid_device_id(device_id):
            print("BAD_DEVICE_ID", file=sys.stderr)
            return 2
        n = dataclasses.replace(self.net(), phone_ip=new_ip)     # validates the IP
        self.write(NET_CONF, json.dumps(dataclasses.asdict(n), indent=1), 0o644)
        files = render_all(n)
        for path in DROPINS:
            self.write(path, files[path], 0o644)
        keys = K.load(self.read(KEYS_DB, "[]"))
        self.write(SSHD_KEYS, K.authorized_keys(keys, n.phone_ip, n.admin_ip))
        proxy_conf = json.loads(self.read(DEFAULT_CONF, "{}") or "{}")
        if proxy_conf:
            proxy_conf["phone_ips"] = [new_ip]
            self.write(DEFAULT_CONF, json.dumps(proxy_conf, indent=1), 0o640)
        steps = [("nft", "-f", "/etc/nftables.conf"),
                 ("systemctl", "daemon-reload"),
                 ("systemctl", "restart", "gladeon-proxy.service"),
                 ("pkill", "-u", "shawn", "-x", "sshd", "--older", "1")]
        for i, argv in enumerate(steps):
            if i == 2 and not self.privd_call("sync-device-set", {"device_id": device_id,
                                                                  "address": f"tcp://{new_ip}:22000"}):
                print("STEP_FAILED sync-device-set", file=sys.stderr)
                return 1
            if self.run(*argv) != 0:
                print(f"STEP_FAILED {argv[0]}", file=sys.stderr)
                return 1
        return 0

    def privd_call(self, verb: str, args: dict) -> bool:
        self.ran.append(["privd", verb, json.dumps(args, sort_keys=True)])
        if self.root:
            return True
        from ..privd.client import PrivdClient
        return PrivdClient().call(verb, args).ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gladeon-admin")
    ap.add_argument("--config", default=DEFAULT_CONF)
    ap.add_argument("--root", default="", help="stage files under this directory; run nothing")
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("client-bundle", help="print the phone bundle, ASCII-armored (3.3.7)")
    ka = sub.add_parser("key-add")
    ka.add_argument("role", choices=K.ROLES)
    ka.add_argument("pubkey", nargs="+")
    kr = sub.add_parser("key-remove")
    kr.add_argument("fingerprint")
    sub.add_parser("token-rotate", help="new API token; restarts the proxy (locks the vault)")
    pr = sub.add_parser("phone-replace")
    pr.add_argument("new_ip")
    pr.add_argument("device_id")
    a = ap.parse_args(argv)
    adm = Admin(a.root)
    try:
        if a.verb == "client-bundle":
            return adm.client_bundle(a.config)
        if a.verb == "token-rotate":
            return adm.token_rotate(a.config)
        if a.verb == "key-add":
            return adm.key_add(a.role, " ".join(a.pubkey))
        if a.verb == "key-remove":
            return adm.key_remove(a.fingerprint)
        if a.verb == "phone-replace":
            return adm.phone_replace(a.new_ip, a.device_id)
    except ValueError as e:
        print(str(e), file=sys.stderr)          # fixed codes only, never file contents
        return 2
    return 2


def client_bundle(conf_path: str, out=sys.stdout) -> int:
    """Kept for the earlier call site and tests."""
    return Admin(out=out).client_bundle(conf_path)


if __name__ == "__main__":
    raise SystemExit(main())
