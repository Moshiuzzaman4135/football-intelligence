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
    overlay_mode: Literal["clean", "tactical", "debug"] = "clean"
    playing_area_polygon: list[list[float]] = Field(default_factory=list)
    playing_area_person_tolerance: float = Field(default=0.03, ge=0, le=0.5)
    playing_area_ball_margin: float = Field(default=0.10, ge=0, le=0.5)
    person_min_confidence: float = Field(default=0.25, ge=0, le=1)
    ball_min_confidence: float = Field(default=0.25, ge=0, le=1)
    active_track_ceiling: int = Field(default=30, ge=1, le=200)
    track_confirm_min_hits: int = Field(default=2, ge=1, le=20)
    trail_max_age_ms: int = Field(default=1500, ge=1)
    trail_max_points: int = Field(default=20, ge=1, le=500)
    trail_max_jump_ratio: float = Field(default=0.35, ge=0, le=1)
    banner_duration_ms: int = Field(default=1500, ge=0)
    kick_speed_px_s: float = Field(default=250, ge=0)
    kick_proximity_px: float = Field(default=60, ge=0)
    kick_min_contact_frames: int = Field(default=1, ge=1)
    kick_min_ball_continuity: int = Field(default=2, ge=1)
    kick_cooldown_ms: int = Field(default=1000, ge=0)
    kick_max_confidence: float = Field(default=0.70, ge=0, le=1)
    kick_max_jump_ratio: float = Field(default=0.30, ge=0, le=1)
    database_url: str = ""
    upload_cleanup_interval_seconds: float = Field(default=300, ge=0.01)
    object_store_backend: Literal["filesystem", "s3"] = "filesystem"
    s3_endpoint_url: str = ""
    s3_public_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "football-media"
    s3_region: str = "us-east-1"
    tessdata_dir: Path = Path("/usr/share/tesseract-ocr/5/tessdata_fast")
    scoreboard_region_x: float = Field(default=0, ge=0, le=1)
    scoreboard_region_y: float = Field(default=0, ge=0, le=1)
    scoreboard_region_width: float = Field(default=1, gt=0, le=1)
    scoreboard_region_height: float = Field(default=0.2, gt=0, le=1)

    @property
    def scoreboard_region(self) -> tuple[float, float, float, float]:
        return (
            self.scoreboard_region_x,
            self.scoreboard_region_y,
            self.scoreboard_region_width,
            self.scoreboard_region_height,
        )

    @model_validator(mode="after")
    def validate_object_store(self) -> "Settings":
        if self.scoreboard_region_x + self.scoreboard_region_width > 1:
            raise ValueError("scoreboard region exceeds frame width")
        if self.scoreboard_region_y + self.scoreboard_region_height > 1:
            raise ValueError("scoreboard region exceeds frame height")
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
        if self.object_store_backend == "s3" and not self.database_url:
            raise ValueError("S3 object storage requires a durable database URL")
        return self
