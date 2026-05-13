import json
from pathlib import Path

from power_aiops import __version__
from power_aiops.cli import main

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_incident.json"


def test_cli_run_demo(capsys):
    assert main(["run", "--demo"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["incident_id"] == "INC-DEMO"
    assert data["trace_id"] == "trace-demo"
    assert "shared_board" in data


def test_cli_run_json(tmp_path):
    p = tmp_path / "req.json"
    p.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["run", "--json", str(p)]) == 0


def test_cli_run_demo_and_json_error(capsys):
    assert main(["run", "--demo", "--json", str(FIXTURE)]) == 2
    assert "either" in capsys.readouterr().err.lower()


def test_cli_run_missing_args(capsys):
    assert main(["run"]) == 2


def test_cli_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_cli_pretty(capsys):
    main(["run", "--demo", "--pretty"])
    out = capsys.readouterr().out
    assert "\n" in out
    assert json.loads(out)["incident_id"] == "INC-DEMO"
