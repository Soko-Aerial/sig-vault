import json
from typing import Dict

from components.connection_form import ConnectionForm


def test_connection_form_load_config(monkeypatch, qtbot, tmp_path):
    # Redirect CONFIG_PATH
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("components.connection_form.CONFIG_PATH", str(cfg))

    data = {"server": "srv", "share": "share", "username": "user", "password": "pass"}
    cfg.write_text(json.dumps(data))

    received: Dict[str, str] = {}
    form = ConnectionForm(lambda info: received.update(info))
    qtbot.addWidget(form)

    assert form.server_input.text() == "srv"
    assert form.share_input.text() == "share"
    assert form.username_input.text() == "user"
    assert form.password_input.text() == "pass"


def test_connection_form_on_connect_and_save(monkeypatch, qtbot, tmp_path):
    cfg = tmp_path / "config_saved.json"
    monkeypatch.setattr("components.connection_form.CONFIG_PATH", str(cfg))

    captured: Dict[str, str] = {}
    form = ConnectionForm(lambda info: captured.update(info))
    qtbot.addWidget(form)

    form.server_input.setText("a")
    form.share_input.setText("b")
    form.username_input.setText("c")
    form.password_input.setText("d")

    form.on_connect()

    assert captured == {"server": "a", "share": "b", "username": "c", "password": "d"}
    assert cfg.exists()
    written = json.loads(cfg.read_text())
    assert written == captured


def test_connection_form_missing_config(monkeypatch, qtbot, tmp_path):
    cfg = tmp_path / "does_not_exist.json"
    monkeypatch.setattr("components.connection_form.CONFIG_PATH", str(cfg))

    form = ConnectionForm(lambda _: None)
    qtbot.addWidget(form)

    assert form.server_input.text() == ""
    assert form.share_input.text() == ""
    assert form.username_input.text() == ""
    assert form.password_input.text() == ""
