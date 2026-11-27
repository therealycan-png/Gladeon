import configparser
import shutil
import subprocess
import tempfile
import unittest

from gladeon.deploy import NetConfig, llm_args, nftables, render_all

C = NetConfig("192.168.1.20", "192.168.1.30", "192.168.1.10/24", "192.168.1.11/24", eth_mac="aa:bb:cc:dd:ee:ff")


class DeployTest(unittest.TestCase):
    def test_bad_ip_rejected(self):
        with self.assertRaises(ValueError):
            NetConfig("192.168.1.300", "1.1.1.1", "x", "y")

    def test_nft_rules(self):
        r = nftables(C)
        self.assertIn("table inet gladeon", r)
        self.assertEqual(r.count("policy drop"), 3)
        self.assertIn("ip saddr 192.168.1.20 tcp dport 8443 accept", r)
        self.assertNotIn("dport 2222", r)
        self.assertIn("ip saddr { 192.168.1.20, 192.168.1.30 } tcp dport 22 accept", r)

    @unittest.skipUnless(shutil.which("nft"), "nft not installed")
    def test_nft_syntax(self):
        with tempfile.NamedTemporaryFile("w", suffix=".nft") as f:
            f.write(nftables(C))
            f.flush()
            subprocess.run(["nft", "-c", "-f", f.name], check=True)

    def test_units_parse_and_keys(self):
        files = render_all(C)
        for path, body in files.items():
            if path.endswith((".service", ".mount", ".network", ".link", ".conf")) and "nftables" not in path \
                    and "sysctl" not in path:
                cp = configparser.ConfigParser(strict=False, interpolation=None)
                cp.optionxform = str
                cp.read_string(body)
        for path, body in files.items():
            if path.endswith((".service", ".mount")) and "StartLimitIntervalSec" in body:
                unit_part = body.split("[Service]")[0]
                self.assertIn("StartLimitIntervalSec", unit_part, path)   # 7.6: [Unit] key
        proxy = files["/etc/systemd/system/gladeon-proxy.service"]
        for key in ("Type=notify", "WatchdogSec=20", "TimeoutStopSec=120", "IPAddressDeny=any", "UMask=0077"):
            self.assertIn(key, proxy)
        self.assertIn("IPAddressAllow=192.168.1.20 localhost", files["/etc/systemd/system/gladeon-proxy.service.d/ip.conf"])
        self.assertIn("RouteMetric=100", files["/etc/systemd/network/10-gleth0.network"])
        self.assertIn("RouteMetric=600", files["/etc/systemd/network/10-glwlan0.network"])
        self.assertIn("/etc/systemd/network/10-gleth0.link", files)
        self.assertNotIn("/etc/systemd/network/10-glwlan0.link", files)

    def test_llm_args(self):
        a = llm_args("quick", "/m/q.gguf", 3072, 6)
        self.assertIn("--port 9931", a)
        self.assertIn("-c 3072 -np 1", a)
        self.assertIn("--host 127.0.0.1", a)
        e = llm_args("embed", "/m/e.gguf", 0, 2)
        self.assertIn("--embeddings", e)
        self.assertIn("-c 1024 -b 512 -ub 512", e)


if __name__ == "__main__":
    unittest.main()
