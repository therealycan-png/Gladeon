import os
import tempfile
import unittest

from gladeon.hwprobe import markdown, need_gib, probe, ram_plan, tier


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


class HwProbeTest(unittest.TestCase):
    def test_fake_tree(self):
        with tempfile.TemporaryDirectory() as r:
            w(r, "proc/cpuinfo", "processor\t: 0\nmodel name\t: Test CPU 9000\nflags\t\t: fpu avx avx2 fma\n")
            w(r, "proc/meminfo", "MemTotal:       16314212 kB\n")
            for c, core in ((0, 0), (1, 0), (2, 1), (3, 1)):
                b = f"sys/devices/system/cpu/cpu{c}"
                w(r, f"{b}/topology/package_id", "0")
                w(r, f"{b}/topology/core_id", str(core))
                w(r, f"{b}/cache/index3/level", "3")
                w(r, f"{b}/cache/index3/shared_cpu_list", "0-3")
            w(r, "sys/class/tpm/tpm0/tpm_version_major", "2")
            w(r, "sys/block/nvme0n1/size", str(1000 * 1024 * 1024 * 2))
            w(r, "sys/block/nvme0n1/queue/rotational", "0")
            w(r, "sys/block/loop0/size", "8")
            w(r, "sys/class/net/enp3s0/address", "aa:bb:cc:dd:ee:ff")
            w(r, "sys/class/net/enp3s0/operstate", "up")
            w(r, "sys/class/net/enp3s0/device/x", "")
            w(r, "sys/class/net/wlp2s0/address", "11:22:33:44:55:66")
            w(r, "sys/class/net/wlp2s0/device/x", "")
            os.makedirs(os.path.join(r, "sys/class/net/wlp2s0/wireless"))
            hw = probe(r)
        self.assertEqual((hw.cpu_model, hw.logical_cpus, hw.physical_cores), ("Test CPU 9000", 4, 2))
        self.assertEqual(hw.flags, ["avx", "avx2", "fma"])
        self.assertEqual(hw.l3_groups, [[0, 1]])
        self.assertAlmostEqual(hw.mem_total_gib, 15.56, places=1)
        self.assertTrue(hw.tpm2)
        self.assertEqual([d["name"] for d in hw.disks], ["nvme0n1"])
        self.assertEqual([(n["name"], n["wireless"]) for n in hw.nics], [("enp3s0", False), ("wlp2s0", True)])
        md = markdown(hw)
        self.assertIn("model tier 12-24", md)
        self.assertIn("Wi-Fi 11:22:33:44:55:66", md)

    def test_this_machine_runs(self):
        hw = probe()
        self.assertGreater(hw.logical_cpus, 0)

    def test_tiers_and_plan(self):
        self.assertEqual(tier(8)["tier"], "<12")
        self.assertEqual(tier(32)["deep"], "12-14B Q4_K_M")
        self.assertEqual(need_gib([3, 0.5]), round((3.5 + 2.0) * 1.2, 2))
        self.assertEqual(ram_plan(16, 2, quick=3.0, embed=0.6, deep=7.0), "swap")
        self.assertEqual(ram_plan(32, 2, quick=3.0, embed=0.6, deep=9.0), "resident")
        self.assertEqual(ram_plan(6, 2, quick=3.0, embed=0.6, deep=None), "DEGRADED(RAM_INSUFFICIENT)")
        self.assertEqual(ram_plan(16, 2, quick=3.0, embed=0.6, deep=11.0), "unavailable")


if __name__ == "__main__":
    unittest.main()
