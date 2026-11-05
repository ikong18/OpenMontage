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


def test_build_app_uses_claude_cli_with_given_work_dir(tmp_path):
    app = cli.build_app(tmp_path, work_dir=tmp_path / "w")
    assert isinstance(app.state.ai, ClaudeCli)
    assert app.state.ai.work_dir == tmp_path / "w"
    assert app.state.projects_dir == tmp_path


def test_default_work_dir_is_outside_the_repo_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    work = cli.default_work_dir()
    assert not work.resolve().is_relative_to(REPO.resolve())
    monkeypatch.delenv("LOCALAPPDATA")
    assert not cli.default_work_dir().resolve().is_relative_to(REPO.resolve())


def test_readme_states_the_safety_rules():
    text = (REPO / "studio" / "README.md").read_text(encoding="utf-8")
    for needle in ("127.0.0.1", "token", "claude", "private", "approve"):
        assert needle in text.lower(), needle


def test_index_and_main_readme_mention_studio():
    assert "studio" in (REPO / "skills" / "INDEX.md").read_text(encoding="utf-8").lower()
    assert "python -m studio" in (REPO / "README.md").read_text(encoding="utf-8")


def test_open_when_ready_waits_for_the_server_then_opens_once(monkeypatch):
    alive = iter([False, False, True])
    opened = []
    monkeypatch.setattr(cli, "_server_alive", lambda port: next(alive))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli._open_when_ready(4760, timeout=5) is True
    assert opened == ["http://127.0.0.1:4760/"]


def test_open_when_ready_gives_up_without_opening(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "_server_alive", lambda port: False)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    clock = iter([0, 1, 2, 99, 100])
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    assert cli._open_when_ready(4760, timeout=5) is False and opened == []


def test_serve_parser_accepts_open_flag():
    assert cli.build_parser().parse_args(["serve", "--open"]).open is True
