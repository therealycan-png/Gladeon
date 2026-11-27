"""gladeon-admin (root, via sudo over SSH). Only the verbs that need no live vault are here so far."""
from __future__ import annotations

import argparse
import json
import sys

from .common import armor, make_bundle, spki_pin

DEFAULT_CONF = "/etc/gladeon/proxy.json"


def client_bundle(conf_path: str, out=sys.stdout) -> int:
    with open(conf_path) as f:
        conf = json.load(f)
    with open(conf["cert"], "rb") as f:
        cert = f.read()
    with open(conf["token_file"]) as f:
        token = f.read().strip()
    ips = conf.get("pc_ips", {})
    data = json.loads(make_bundle(cert, token, spki_pin(cert)))
    data.update({"pc_ip_eth": ips.get("eth", ""), "pc_ip_wlan": ips.get("wlan", ""), "port": conf.get("port", 8443)})
    out.write(armor(json.dumps(data).encode()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gladeon-admin")
    ap.add_argument("--config", default=DEFAULT_CONF)
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("client-bundle", help="print the phone bundle, ASCII-armored (3.3.7)")
    a = ap.parse_args(argv)
    if a.verb == "client-bundle":
        return client_bundle(a.config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
