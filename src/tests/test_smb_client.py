import types
import pytest
from typing import cast

from src.services.smb import client


class DummyConnection:
    def __init__(self, *args, **kwargs):
        self.connected = False

    def connect(self):
        self.connected = True


class DummySession:
    def __init__(self, conn, username=None, password=None):
        self.conn = conn
        self.username = username
        self.password = password
        self.connected = False
        self.raise_on_connect = False

    def connect(self):
        if self.raise_on_connect:
            # Will be patched in test to custom exception
            raise client.LogonFailure("bad creds")  # type: ignore[call-arg]
        self.connected = True


class DummyTree:
    def __init__(self, session, path):
        self.session = session
        self.path = path
        self.connected = False

    def connect(self):
        self.connected = True


class DummyOpen:
    def __init__(self, tree, path, access=None):
        self.tree = tree
        self.path = path
        self.access = access
        self.created = False
        self.closed = False
        self._reads = []
        self._write_calls = []
        # iteration support for context manager

    # Methods mimicking smbprotocol Open
    def create(self, *args, **kwargs):
        self.created = True

    def write(self, data, offset):
        self._write_calls.append((data, offset))

    def read(self, offset, length, wait=True):  # noqa: ARG002
        # Simulate sequential chunk reads from preloaded bytes
        all_data = b"".join(self._reads)
        if offset >= len(all_data):
            return b""  # Normal EOF condition
        return all_data[offset : offset + length]

    def preload_read_data(self, *chunks):
        self._reads = list(chunks)

    def close(self):
        self.closed = True

    # Context manager protocol
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def test_connect_to_smb_share_success(monkeypatch):
    dummy_root = DummyOpen(None, "")

    def conn_factory(*a, **k):
        return DummyConnection()

    def session_factory(conn, username, password):
        return DummySession(conn, username=username, password=password)

    def tree_factory(session, path):
        return DummyTree(session, path)

    def open_factory(tree, path):
        # Return dummy root for the root open
        return dummy_root

    monkeypatch.setattr(client, "Connection", conn_factory)
    monkeypatch.setattr(client, "Session", session_factory)
    monkeypatch.setattr(client, "TreeConnect", tree_factory)
    monkeypatch.setattr(client, "Open", open_factory)

    root = client.connect_to_smb_share("server", "share", "user", "pass")
    assert root is dummy_root
    assert dummy_root.created is True


def test_connect_to_smb_share_auth_failure(monkeypatch):
    # Provide a dummy exception to simplify raising/expectation
    class DummyLogonFailure(Exception):
        def __init__(self, *a, **k):
            super().__init__("dummy logon failure")

    monkeypatch.setattr(client, "LogonFailure", DummyLogonFailure, raising=False)

    def conn_factory(*a, **k):
        return DummyConnection()

    failing_session = DummySession(DummyConnection(), username="u", password="p")
    failing_session.raise_on_connect = True

    def session_factory(conn, username, password):
        return failing_session

    monkeypatch.setattr(client, "Connection", conn_factory)
    monkeypatch.setattr(client, "Session", session_factory)
    # tree and open should not be reached, but supply anyway
    monkeypatch.setattr(client, "TreeConnect", lambda *a, **k: DummyTree(*a, **k))
    monkeypatch.setattr(client, "Open", lambda *a, **k: DummyOpen(*a, **k))

    with pytest.raises(DummyLogonFailure):
        client.connect_to_smb_share("server", "share", "user", "bad")


def test_list_files_in_directory(monkeypatch):
    names = [".", "..", "file1.txt", "subdir", "unicodé.txt"]

    class Entry:
        def __init__(self, name_bytes):
            self.fields = {
                "file_name": types.SimpleNamespace(value=name_bytes),
            }

    # Provide one malformed entry to exercise decode error handling
    valid_entries = [Entry(n.encode("utf-16le")) for n in names] + [
        Entry(b"\xff\xfe\x00")
    ]  # invalid sequence

    def query_directory(pattern, info_class):  # noqa: ARG001
        for e in valid_entries:
            yield e

    dummy_root = cast(
        client.Open, types.SimpleNamespace(query_directory=query_directory)
    )

    files = client.list_files_in_directory(dummy_root)
    assert {f["name"] for f in files} == {"file1.txt", "subdir", "unicodé.txt"}


def test_upload_file(monkeypatch, tmp_path):
    # Prepare local file
    local_file = tmp_path / "upload_me.bin"
    content = b"Hello SMB Upload"
    local_file.write_bytes(content)

    dummy_tree = DummyTree(None, "path")
    captured = {}

    def fake_get_tree_and_path(session_info, remote_path):  # noqa: ARG001
        return dummy_tree, remote_path

    dummy_open = DummyOpen(dummy_tree, "upload_me.bin")

    def open_factory(tree, path):
        assert tree is dummy_tree
        assert path == "upload_me.bin"
        return dummy_open

    def write_override(data, offset):
        captured["data"] = data
        captured["offset"] = offset

    dummy_open.write = write_override  # type: ignore[assignment]

    monkeypatch.setattr(client, "_get_tree_and_path", fake_get_tree_and_path)
    monkeypatch.setattr(client, "Open", open_factory)

    client.upload_file(
        {"server": "s", "share": "sh", "username": "u", "password": "p"},
        str(local_file),
    )

    assert captured["data"] == content
    assert captured["offset"] == 0
    assert dummy_open.closed is True


@pytest.mark.parametrize(
    "remote_input, expected_rel",
    [
        (r"\folder\file.txt", r"folder\file.txt"),
        ("/folder/file.txt", "folder/file.txt"),
        ("folder/file.txt", "folder/file.txt"),
        (r"folder\file.txt", r"folder\file.txt"),
    ],
)
def test_get_tree_and_path(monkeypatch, remote_input, expected_rel):
    server = "srv"
    share = "sh"
    session_info = {"server": server, "share": share, "username": "u", "password": "p"}

    # Monkeypatch SMB classes to dummies
    monkeypatch.setattr(client, "Connection", lambda *a, **k: DummyConnection())
    monkeypatch.setattr(
        client,
        "Session",
        lambda conn, username=None, password=None: DummySession(
            conn, username, password
        ),
    )
    monkeypatch.setattr(
        client, "TreeConnect", lambda session, path: DummyTree(session, path)
    )

    tree, rel = client._get_tree_and_path(session_info, remote_input)

    # UNC path used to connect
    assert isinstance(tree, DummyTree)
    assert tree.path == rf"\\{server}\{share}"

    # Underlying connections should be established
    assert tree.connected is True
    assert tree.session.connected is True
    assert tree.session.conn.connected is True

    # Relative path stripping
    assert rel == expected_rel


def test_download_file(monkeypatch, tmp_path):
    # Set up fake infrastructure for single Open usage
    dummy_tree = DummyTree(None, "path")
    dummy_open = DummyOpen(dummy_tree, "remote.bin")
    dummy_open.preload_read_data(b"Hello ", b"World")

    def fake_get_tree_and_path(session_info, remote_path):  # noqa: ARG001
        return dummy_tree, remote_path

    monkeypatch.setattr(client, "_get_tree_and_path", fake_get_tree_and_path)
    monkeypatch.setattr(client, "Open", lambda *a, **k: dummy_open)

    local_file = tmp_path / "downloaded.bin"
    client.download_file(
        {"server": "s", "share": "sh", "username": "u", "password": "p"},
        "remote.bin",
        str(local_file),
    )

    assert local_file.read_bytes() == b"Hello World"
    assert dummy_open.closed is True


def test_download_file_status_end_of_file(monkeypatch, tmp_path):
    """Ensure STATUS_END_OF_FILE style error is treated as normal EOF."""
    dummy_tree = DummyTree(None, "path")
    dummy_open = DummyOpen(dummy_tree, "remote.bin")
    dummy_open.preload_read_data(b"OnlyChunk")

    # Monkeypatch SMBResponseException to a simple Exception subclass we can raise with a message
    class FakeSMBResponseException(Exception):
        pass

    monkeypatch.setattr(client, "SMBResponseException", FakeSMBResponseException)

    class EOFOnceDummyOpen(DummyOpen):
        def __init__(self, base: DummyOpen):
            super().__init__(base.tree, base.path)
            self._reads = base._reads
            self._raised = False

        def read(self, offset, length, wait=True):  # noqa: ARG002
            if not self._raised:
                self._raised = True
                # Return full content in first chunk
                return b"OnlyChunk"
            # Second call simulate STATUS_END_OF_FILE
            raise FakeSMBResponseException("STATUS_END_OF_FILE: 0xc0000011")

    eof_dummy = EOFOnceDummyOpen(dummy_open)

    def fake_get_tree_and_path(session_info, remote_path):  # noqa: ARG001
        return dummy_tree, remote_path

    monkeypatch.setattr(client, "_get_tree_and_path", fake_get_tree_and_path)
    monkeypatch.setattr(client, "Open", lambda *a, **k: eof_dummy)

    local_file = tmp_path / "downloaded_eof.bin"
    client.download_file(
        {"server": "s", "share": "sh", "username": "u", "password": "p"},
        "remote.bin",
        str(local_file),
    )

    assert local_file.read_bytes() == b"OnlyChunk"
    assert eof_dummy.closed is True
