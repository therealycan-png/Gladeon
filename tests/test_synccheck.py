import unittest

from gladeon.privd.synccheck import SyncDevice, apply_device, check

DEV = SyncDevice("AAAAAAA-BBBBBBB-CCCCCCC-DDDDDDD-EEEEEEE-FFFFFFF-GGGGGGG-HHHHHHH", "tcp://192.168.1.20:22000")
GOOD = f"""<configuration version="37">
  <folder id="gladeon-notes" path="/srv/gladeon/vault/notes" type="receiveonly" ignorePerms="true">
    <device id="{DEV.device_id}"></device>
    <versioning type="staggered"><param key="maxAge" val="7776000"></param></versioning>
  </folder>
  <device id="{DEV.device_id}" introducer="false" autoAcceptFolders="false"><address>{DEV.address}</address></device>
  <gui enabled="true"><address>127.0.0.1:8384</address><password>$2a$10$hash</password></gui>
  <options><globalAnnounceEnabled>false</globalAnnounceEnabled><localAnnounceEnabled>false</localAnnounceEnabled>
  <relaysEnabled>false</relaysEnabled><natEnabled>false</natEnabled><crashReportingEnabled>false</crashReportingEnabled>
  <autoUpgradeIntervalH>0</autoUpgradeIntervalH><startBrowser>false</startBrowser><urAccepted>-1</urAccepted></options>
</configuration>"""


class SyncCheckTest(unittest.TestCase):
    def test_good(self):
        self.assertEqual(check(GOOD, DEV, "/srv/gladeon/vault/notes"), [])

    def test_failures(self):
        bad = GOOD.replace('type="receiveonly"', 'type="sendreceive"').replace("<relaysEnabled>false", "<relaysEnabled>true")
        bad = bad.replace("127.0.0.1:8384", "0.0.0.0:8384").replace("<password>$2a$10$hash</password>", "")
        errs = check(bad, DEV, "/srv/gladeon/vault/notes")
        for e in ("FOLDER_TYPE", "OPTION:relaysEnabled", "GUI_NOT_LOCAL", "GUI_NO_PASSWORD"):
            self.assertIn(e, errs)
        self.assertIn("DEVICE_ID", check(GOOD, SyncDevice("X", DEV.address), "/srv/gladeon/vault/notes"))
        intro = GOOD.replace('introducer="false"', 'introducer="true"')
        self.assertIn("DEVICE_INTRODUCER", check(intro, DEV, "/srv/gladeon/vault/notes"))
        self.assertEqual(check("<nope", DEV, "/x"), ["CONFIG_UNPARSEABLE"])

    def test_apply_device(self):
        new = SyncDevice("ZZZZZZZ-YYYYYYY-XXXXXXX-WWWWWWW-VVVVVVV-UUUUUUU-TTTTTTT-SSSSSSS", "tcp://192.168.1.21:22000")
        out = apply_device(GOOD, new)
        self.assertEqual(check(out, new, "/srv/gladeon/vault/notes"), [])
        self.assertIn("DEVICE_ID", check(out, DEV, "/srv/gladeon/vault/notes"))


if __name__ == "__main__":
    unittest.main()
