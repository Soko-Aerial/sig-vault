from src.services.storage_interface import get_backend, download_file, upload_file


def test_smb_backend_wrappers(monkeypatch, tmp_path):
    # Force SMB backend selection
    sess = {
        "storage": "local",
        "server": "s",
        "share": "sh",
        "username": "u",
        "password": "p",
    }

    # Patch SMB client functions used by the backend
    import src.services.smb.client as smb

    called = {"connect": False, "list": False, "download": False, "upload": False}

    monkeypatch.setattr(
        smb,
        "connect_to_smb_share",
        lambda server, share, username, password: (
            called.__setitem__("connect", True) or object()
        ),
    )
    monkeypatch.setattr(
        smb,
        "list_files_in_directory",
        lambda handle: (called.__setitem__("list", True) or []),
    )
    monkeypatch.setattr(
        smb, "download_file", lambda s, r, loc: called.__setitem__("download", True)
    )
    monkeypatch.setattr(
        smb, "upload_file", lambda s, lp: called.__setitem__("upload", True)
    )

    be = get_backend(sess)
    h = be.connect(sess)
    assert called["connect"] is True
    assert isinstance(be.list_files(h), list)

    out = tmp_path / "x.bin"
    download_file(sess, "r", str(out))
    assert called["download"] is True

    src = tmp_path / "p.bin"
    src.write_bytes(b"x")
    upload_file(sess, str(src))
    assert called["upload"] is True
