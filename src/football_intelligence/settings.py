"""Validated environment settings shared by API, CLI, and worker entry points."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FOOTBALL_", env_file=".env", extra="ignore"
    )

    data_root: Path = Path("data")
    detector: Literal["color", "ultralytics"] = "color"
    model_name: str = "yolo11n.pt"
    device: str = "auto"
    max_frame_errors: int = Field(default=10, ge=0, le=1000)
    object_store_backend: Literal["filesystem", "s3"] = "filesystem"
    s3_endpoint_url: str = ""
    s3_public_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "football-media"
    s3_region: str = "us-east-1"

    @model_validator(mode="after")
    def validate_object_store(self) -> "Settings":
        if self.object_store_backend == "s3" and not all(
            (
                self.s3_endpoint_url,
                self.s3_access_key,
                self.s3_secret_key.get_secret_value(),
                self.s3_bucket,
            )
        ):
            raise ValueError(
                "S3 object storage requires endpoint, access key, secret key, and bucket"
            )
        return self
