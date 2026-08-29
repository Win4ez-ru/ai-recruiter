from __future__ import annotations

import pytest
from pydantic import ValidationError

from app import main
from app.config import Settings


def test_run_sanitizes_model_level_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_token = "123456:SECRET_MUST_NOT_LEAK"
    with pytest.raises(ValidationError) as captured:
        Settings(
            telegram_bot_token=secret_token,
            telegram_user_id=42,
            telegram_direct_enabled=False,
            telegram_proxy_urls="",
            ai_provider="ollama",
            ollama_model="test-model",
        )

    def invalid_settings() -> Settings:
        raise captured.value

    monkeypatch.setattr(main, "get_settings", invalid_settings)

    with pytest.raises(SystemExit) as exited:
        main.run()

    stderr = capsys.readouterr().err
    assert exited.value.code == 2
    assert "общие настройки" in stderr
    assert secret_token not in stderr
    assert "input_value" not in stderr
