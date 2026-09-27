from typer.testing import CliRunner

from clipper.cli import app


def test_cli_help():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "process" in result.output
    assert "daemon" in result.output
