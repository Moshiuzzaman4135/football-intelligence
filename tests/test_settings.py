from football_intelligence.settings import Settings


def test_settings_reads_validated_environment(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DETECTOR", "ultralytics")
    monkeypatch.setenv("FOOTBALL_MAX_FRAME_ERRORS", "3")
    monkeypatch.setenv("FOOTBALL_DATA_ROOT", "/tmp/football-settings-test")

    settings = Settings()

    assert settings.detector == "ultralytics"
    assert settings.max_frame_errors == 3
    assert str(settings.data_root) == "/tmp/football-settings-test"
