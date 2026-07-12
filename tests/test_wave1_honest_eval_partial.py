from pathlib import Path

import pytest

from scripts.analysis.wave1_honest_eval_partial import log_health


def test_log_health_rate_handles_midnight_rollover(tmp_path: Path) -> None:
    log = tmp_path / "orchestrator.log"
    log.write_text(
        "\n".join(
            [
                '23:59:50 werkzeug INFO: 127.0.0.1 - - [10/May/2026 23:59:50] "POST /result HTTP/1.1" 200 -',
                '00:00:10 werkzeug INFO: 127.0.0.1 - - [11/May/2026 00:00:10] "POST /result HTTP/1.1" 200 -',
            ]
        )
    )

    health = log_health(log)

    assert health["elapsed_minutes"] == pytest.approx(20.0 / 60.0)
    assert health["rate_per_minute"] == pytest.approx(6.0)


def test_log_health_bins_are_chronological_across_midnight(tmp_path: Path) -> None:
    log = tmp_path / "orchestrator.log"
    log.write_text(
        "\n".join(
            [
                '23:45:00 werkzeug INFO: 127.0.0.1 - - [10/May/2026 23:45:00] "POST /result HTTP/1.1" 200 -',
                '00:01:00 werkzeug INFO: 127.0.0.1 - - [11/May/2026 00:01:00] "POST /result HTTP/1.1" 200 -',
            ]
        )
    )

    health = log_health(log)

    assert sorted(health["bins"]) == [1425, 1440]
