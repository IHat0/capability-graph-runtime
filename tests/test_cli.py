import json

import pytest

from cgr.apps.cli.main import main


def test_main_prints_echo_payload_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main()

    output = capsys.readouterr().out.strip()
    assert json.loads(output) == {"message": "Hello CGR!"}
    assert output == '{"message": "Hello CGR!"}'
    assert exit_code == 0
