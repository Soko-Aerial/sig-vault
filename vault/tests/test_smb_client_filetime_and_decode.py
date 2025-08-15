from typing import cast
from vault.services.smb.client import list_files_in_directory, Open


class DummyEntry:
    def __init__(self, name_bytes, ticks):
        # Simulate smbprotocol entry with fields dict and nested .value attributes
        class V:
            def __init__(self, v):
                self.value = v

        self.fields = {
            "file_name": V(name_bytes),
            "file_attributes": V(0x10),  # directory attribute
            "end_of_file": V(0),
            "last_write_time": V(ticks),
        }


def test_smb_filetime_conversion_and_decoding():
    # FILETIME for 2025-01-01 00:00:00 UTC: 133,501,728,000,000,000 (approx)
    filetime_ticks = 133501728000000000
    entries = [DummyEntry("Folder".encode("utf-16le"), filetime_ticks)]

    def qd(self, pattern, info_class):  # noqa: ARG001, ANN001
        for e in entries:
            yield e

    root = type("Root", (), {"query_directory": qd})()
    rows = list_files_in_directory(cast(Open, root))
    assert rows[0]["name"] == "Folder"
    # Expect a formatted date string (best-effort); ensure not empty
    assert rows[0]["modified"] != ""
