from football_intelligence.settings import Settings


def test_settings_reads_validated_environment(monkeypatch):
    monkeypatch.setenv("FOOTBALL_DETECTOR", "ultralytics")
    monkeypatch.setenv("FOOTBALL_MAX_FRAME_ERRORS", "3")
    monkeypatch.setenv("FOOTBALL_DATA_ROOT", "/tmp/football-settings-test")

    settings = Settings()

    assert settings.detector == "ultralytics"
    assert settings.max_frame_errors == 3
    assert str(settings.data_root) == "/tmp/football-settings-test"


def test_settings_configures_s3_compatible_object_storage(monkeypatch):
    monkeypatch.setenv("FOOTBALL_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("FOOTBALL_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("FOOTBALL_S3_PUBLIC_ENDPOINT_URL", "http://127.0.0.1:9010")
    monkeypatch.setenv("FOOTBALL_S3_ACCESS_KEY", "operator")
    monkeypatch.setenv("FOOTBALL_S3_SECRET_KEY", "development-secret")
    monkeypatch.setenv("FOOTBALL_S3_BUCKET", "match-media")

    settings = Settings()

    assert settings.object_store_backend == "s3"
    assert settings.s3_endpoint_url == "http://minio:9000"
    assert settings.s3_public_endpoint_url == "http://127.0.0.1:9010"
    assert settings.s3_access_key == "operator"
    assert settings.s3_secret_key.get_secret_value() == "development-secret"
    assert settings.s3_bucket == "match-media"
