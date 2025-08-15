from vault.components.file_tree_viewer import FileExplorer


def test_format_modified_and_size(monkeypatch, qtbot):
    # Stub load_files to avoid any backend calls during init
    monkeypatch.setattr(FileExplorer, "load_files", lambda self: True)

    # Build a FileExplorer in sync mode to exercise UI formatting logic
    fe = FileExplorer(
        {
            "server": "s",
            "share": "sh",
            "username": "u",
            "password": "p",
            "storage": "local",
        },
        async_load=False,
    )
    qtbot.addWidget(fe)

    # Manually populate with crafted entries to test formatting branches
    entries = [
        {
            "name": "folder",
            "path": "folder",
            "size": "0",
            "is_dir": "true",
            "modified": "2025-01-01T00:00:00Z",
        },
        {
            "name": "small.txt",
            "path": "small.txt",
            "size": "512",
            "is_dir": "false",
            "modified": 1735689600,
        },
        {
            "name": "mid.bin",
            "path": "mid.bin",
            "size": "2048",
            "is_dir": "false",
            "modified": "2025-01-02T10:00:00Z",
        },
        {
            "name": "big.iso",
            "path": "big.iso",
            "size": str(5 * 1024 * 1024),
            "is_dir": "false",
            "modified": None,
        },
    ]

    fe._root_handle = object()  # type: ignore[assignment]
    fe.file_tree.clear()
    fe._populate_files(entries)

    # Verify row count and formatted columns
    assert fe.file_tree.topLevelItemCount() == 4

    # Collect rows by name to avoid relying on sort order
    rows = {}
    for i in range(fe.file_tree.topLevelItemCount()):
        it = fe.file_tree.topLevelItem(i)
        assert it is not None
        rows[it.text(0)] = [it.text(c) for c in range(4)]

    # Folder row shows blank size
    assert rows["folder"][1] == ""
    # Small file should show bytes
    assert rows["small.txt"][1].endswith(" B")
    # Mid file in KB
    assert "KB" in rows["mid.bin"][1]
    # Big file in MB
    assert "MB" in rows["big.iso"][1]
