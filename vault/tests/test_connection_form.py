import base64
import json
from typing import Dict
from vault.components.connection_form import ConnectionForm


def test_connection_form_load_config(monkeypatch, qtbot, tmp_path):
    # Use a temp credentials.json as the single source of truth
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("vault.components.connection_form.CREDENTIALS_PATH", str(creds))

    data = {
        "default_mode": "local",
        "local": {
            "server": "srv",
            "share": "share",
            "username": "user",
            "password": "pass",
        },
        "cloud": {"base_url": "", "username": "", "password": ""},
    }
    creds.write_text(json.dumps(data))

    received: Dict[str, str] = {}
    form = ConnectionForm(lambda info: received.update(info))
    qtbot.addWidget(form)

    # In Local mode, values from credentials.json populate fields
    assert form.server_input.text() == "srv"
    assert form.share_input.text() == "share"
    assert form.username_input.text() == "user"
    assert form.password_input.text() == "pass"


def test_connection_form_on_connect_and_save(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    # Redirect unified credentials path
    monkeypatch.setattr("vault.components.connection_form.CREDENTIALS_PATH", str(creds))

    captured: Dict[str, str] = {}
    form = ConnectionForm(lambda info: captured.update(info))
    qtbot.addWidget(form)

    # Stay in Local mode and fill form fields
    form.server_input.setText("a")
    form.share_input.setText("b")
    form.username_input.setText("c")
    form.password_input.setText("d")

    form.on_connect()

    assert captured == {"server": "a", "share": "b", "username": "c", "password": "d"}
    # Credentials saved
    assert creds.exists()
    written = json.loads(creds.read_text())
    assert written.get("default_mode") == "local"
    loc = written.get("local", {})
    assert loc.get("server") == "a"
    assert loc.get("share") == "b"
    assert loc.get("username") == "c"
    # Password is stored prudently (base64 marker) and decodes back to original
    pwd = loc.get("password", "")
    if isinstance(pwd, str) and pwd.startswith("b64:"):
        decoded = base64.b64decode(pwd[4:].encode("ascii")).decode("utf-8")
    else:
        decoded = pwd
    assert decoded == "d"


def test_connection_form_missing_config(monkeypatch, qtbot, tmp_path):
    # Only credentials path is used; provide a non-existent file
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("vault.components.connection_form.CREDENTIALS_PATH", str(creds))

    form = ConnectionForm(lambda _: None)
    qtbot.addWidget(form)

    assert form.server_input.text() == ""
    assert form.share_input.text() == ""
    assert form.username_input.text() == ""
    assert form.password_input.text() == ""
