from pathlib import Path

import studio.__main__ as cli
from studio.ai import ClaudeCli

REPO = Path(__file__).resolve().parents[2]


def test_port_default_and_env_override(monkeypatch):
    monkeypatch.delenv("STUDIO_PORT", raising=False)
    assert cli._port() == 4760
    monkeypatch.setenv("STUDIO_PORT", "4999")
    assert cli._port() == 4999
    monkeypatch.setenv("STUDIO_PORT", "not-a-number")
    assert cli._port() == 4760


def test_open_builds_batch_url_and_opens_browser(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "_server_alive", lambda port: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.delenv("STUDIO_PORT", raising=False)
    assert cli.main(["open", "shorts-demo"]) == 0
    assert opened == ["http://127.0.0.1:4760/batch/shorts-demo"]
    assert cli.main(["open"]) == 0 and opened[-1] == "http://127.0.0.1:4760/"


def test_open_starts_server_when_not_running(monkeypatch):
    calls = {"spawned": 0, "alive": iter([False, True])}
    monkeypatch.setattr(cli, "_server_alive", lambda port: next(calls["alive"]))
    monkeypatch.setattr(cli, "_spawn_server", lambda port: calls.__setitem__("spawned", calls["spawned"] + 1))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    assert cli.main(["open"]) == 0 and calls["spawned"] == 1


def test_build_app_uses_claude_cli_outside_the_repo_root(tmp_path):
    app = cli.build_app(tmp_path)
    assert isinstance(app.state.ai, ClaudeCli)
    assert app.state.ai.work_dir == tmp_path / ".studio-work"
    assert app.state.projects_dir == tmp_path


def test_readme_states_the_safety_rules():
    text = (REPO / "studio" / "README.md").read_text(encoding="utf-8")
    for needle in ("127.0.0.1", "token", "claude", "private", "approve"):
        assert needle in text.lower(), needle


def test_index_and_main_readme_mention_studio():
    assert "studio" in (REPO / "skills" / "INDEX.md").read_text(encoding="utf-8").lower()
    assert "python -m studio" in (REPO / "README.md").read_text(encoding="utf-8")
