from pathlib import Path

import pytest

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
    monkeypatch.setenv("FOOTBALL_DATABASE_URL", "postgresql+psycopg://app@db/football")

    settings = Settings()

    assert settings.object_store_backend == "s3"
    assert settings.s3_endpoint_url == "http://minio:9000"
    assert settings.s3_public_endpoint_url == "http://127.0.0.1:9010"
    assert settings.s3_access_key == "operator"
    assert settings.s3_secret_key.get_secret_value() == "development-secret"
    assert settings.s3_bucket == "match-media"
    assert settings.database_url == "postgresql+psycopg://app@db/football"


def test_s3_runtime_requires_durable_database(monkeypatch):
    monkeypatch.setenv("FOOTBALL_OBJECT_STORE_BACKEND", "s3")
    monkeypatch.setenv("FOOTBALL_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("FOOTBALL_S3_ACCESS_KEY", "operator")
    monkeypatch.setenv("FOOTBALL_S3_SECRET_KEY", "development-secret")
    monkeypatch.delenv("FOOTBALL_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="database URL"):
        Settings(_env_file=None)


def test_compose_requires_distinct_runtime_minio_credentials():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert '${FOOTBALL_MINIO_ROOT_USER:?' in compose
    assert '${FOOTBALL_MINIO_ROOT_PASSWORD:?' in compose
    assert '${FOOTBALL_S3_ACCESS_KEY:?' in compose
    assert '${FOOTBALL_S3_SECRET_KEY:?' in compose
    assert "minio-init:" in compose
    assert ":-football-local" not in compose
