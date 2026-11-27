"""Syncthing pre-start config check (14.4) and pending-device apply. Pure XML logic; the caller
reads/writes config.xml as gladeon-sync with O_NOFOLLOW (5.2.18)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

FOLDER_ID = "gladeon-notes"
VERSIONING_DAYS = 90


@dataclass
class SyncDevice:
    device_id: str
    address: str           # e.g. tcp://192.168.1.20:22000


def _text(el: Optional[ET.Element], default: str = "") -> str:
    return (el.text or "").strip() if el is not None else default


def check(xml_text: str, expected: SyncDevice, notes_path: str, local_id: str) -> list[str]:
    """Returns a list of failed checks; empty = OK to start. `local_id` is the PC's own device ID,
    which Syncthing lists as a device and in each folder's device list."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ["CONFIG_UNPARSEABLE"]
    errs = []
    opts = root.find("options")
    want_off = {"globalAnnounceEnabled": "false", "localAnnounceEnabled": "false", "relaysEnabled": "false",
                "natEnabled": "false", "crashReportingEnabled": "false", "autoUpgradeIntervalH": "0",
                "startBrowser": "false"}
    for k, v in want_off.items():
        if _text(opts.find(k) if opts is not None else None) != v:
            errs.append(f"OPTION:{k}")
    if _text(opts.find("urAccepted") if opts is not None else None) not in ("-1",):
        errs.append("OPTION:urAccepted")
    gui = root.find("gui")
    if gui is None or not _text(gui.find("address")).startswith("127.0.0.1:"):
        errs.append("GUI_NOT_LOCAL")
    if gui is None or not _text(gui.find("password")):
        errs.append("GUI_NO_PASSWORD")
    devices = [d for d in root.findall("device") if d.get("id") != local_id]
    if len(devices) != 1:
        errs.append("DEVICE_COUNT")
    for d in devices:
        if d.get("id") != expected.device_id:
            errs.append("DEVICE_ID")
        if d.get("introducer", "false") != "false":
            errs.append("DEVICE_INTRODUCER")
        if d.get("autoAcceptFolders", "false") != "false":
            errs.append("DEVICE_AUTOACCEPT")
        if [_text(a) for a in d.findall("address")] != [expected.address]:
            errs.append("DEVICE_ADDRESS")
    folders = root.findall("folder")
    if len(folders) != 1:
        errs.append("FOLDER_COUNT")
    for f in folders:
        if f.get("id") != FOLDER_ID:
            errs.append("FOLDER_ID")
        if f.get("path", "").rstrip("/") != notes_path.rstrip("/"):
            errs.append("FOLDER_PATH")
        if f.get("type") != "receiveonly":
            errs.append("FOLDER_TYPE")
        if f.get("ignorePerms") != "true":
            errs.append("FOLDER_IGNOREPERMS")
        devs = [x.get("id") for x in f.findall("device")]
        if [x for x in devs if x not in (expected.device_id, local_id)]:
            errs.append("FOLDER_SHARED_WITH_OTHER")
        v = f.find("versioning")
        if v is None or v.get("type") != "staggered":
            errs.append("VERSIONING_TYPE")
        else:
            params = {p.get("key"): p.get("val") for p in v.findall("param")}
            if params.get("maxAge") != str(VERSIONING_DAYS * 86400):
                errs.append("VERSIONING_MAXAGE")
    return errs


def apply_device(xml_text: str, dev: SyncDevice, local_id: str) -> str:
    """Replace the phone device (and the folder's share) with the pending one; the local device stays."""
    root = ET.fromstring(xml_text)
    for d in root.findall("device"):
        if d.get("id") != local_id:
            root.remove(d)
    nd = ET.SubElement(root, "device", {"id": dev.device_id, "name": "phone", "compression": "metadata",
                                        "introducer": "false", "skipIntroductionRemovals": "false",
                                        "introducedBy": "", "autoAcceptFolders": "false"})
    ET.SubElement(nd, "address").text = dev.address
    for f in root.findall("folder"):
        for d in f.findall("device"):
            if d.get("id") != local_id:
                f.remove(d)
        ET.SubElement(f, "device", {"id": dev.device_id, "introducedBy": ""})
    return ET.tostring(root, encoding="unicode")
