import unittest

from gladeon.privd.synccheck import SyncDevice, apply_device, check

DEV = SyncDevice("AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH", "tcp://192.168.1.20:22000")
LOCAL = "LLLLLLL-LLLLLLL-LLLLLLL-LLLLLLL-LLLLLLL-LLLLLLL-LLLLLLL-LLLLLLL"
GOOD = f"""<configuration version="37">
  <folder id="gladeon-notes" path="/srv/gladeon/vault/notes" type="receiveonly" ignorePerms="true">
    <device id="{LOCAL}"></device>
    <device id="{DEV.device_id}"></device>
    <versioning type="staggered"><param key="maxAge" val="7776000"></param></versioning>
  </folder>
  <device id="{LOCAL}" name="gladeon"><address>dynamic</address></device>
  <device id="{DEV.device_id}" introducer="false" autoAcceptFolders="false"><address>{DEV.address}</address></device>
  <gui enabled="true"><address>127.0.0.1:8384</address><password>$2a$10$hash</password></gui>
  <options><globalAnnounceEnabled>false</globalAnnounceEnabled><localAnnounceEnabled>false</localAnnounceEnabled>
  <relaysEnabled>false</relaysEnabled><natEnabled>false</natEnabled><crashReportingEnabled>false</crashReportingEnabled>
  <autoUpgradeIntervalH>0</autoUpgradeIntervalH><startBrowser>false</startBrowser><urAccepted>-1</urAccepted></options>
</configuration>"""


class SyncCheckTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(check(GOOD, DEV, "/srv/gladeon/vault/notes", LOCAL), [])

    def test_failures(self):
        bad = GOOD.replace('type="receiveonly"', 'type="sendreceive"').replace("<relaysEnabled>false", "<relaysEnabled>true")
        bad = bad.replace("127.0.0.1:8384", "0.0.0.0:8384").replace("<password>$2a$10$hash</password>", "")
        errs = check(bad, DEV, "/srv/gladeon/vault/notes", LOCAL)
        for e in ("FOLDER_TYPE", "OPTION:relaysEnabled", "GUI_NOT_LOCAL", "GUI_NO_PASSWORD"):
            self.assertIn(e, errs)
        self.assertIn("DEVICE_ID", check(GOOD, SyncDevice("X", DEV.address), "/srv/gladeon/vault/notes", LOCAL))
        intro = GOOD.replace('introducer="false"', 'introducer="true"')
        self.assertIn("DEVICE_INTRODUCER", check(intro, DEV, "/srv/gladeon/vault/notes", LOCAL))
        self.assertEqual(check("<nope", DEV, "/x", LOCAL), ["CONFIG_UNPARSEABLE"])

    def test_third_device_rejected(self):
        extra = GOOD.replace("</configuration>", '<device id="EVIL"><address>tcp://1.2.3.4:22000</address></device></configuration>')
        self.assertIn("DEVICE_COUNT", check(extra, DEV, "/srv/gladeon/vault/notes", LOCAL))

    def test_apply_device(self):
        new = SyncDevice("ZZZZZZZ-YYYYYYY-XXXXXXX-WWWWWWW-VVVVVVV-UUUUUUU-TTTTTTT-SSSSSSS", "tcp://192.168.1.21:22000")
        out = apply_device(GOOD, new, LOCAL)
        self.assertIn(LOCAL, out)
        self.assertEqual(check(out, new, "/srv/gladeon/vault/notes", LOCAL), [])
        self.assertIn("DEVICE_ID", check(out, DEV, "/srv/gladeon/vault/notes", LOCAL))


if __name__ == "__main__":
    unittest.main()
