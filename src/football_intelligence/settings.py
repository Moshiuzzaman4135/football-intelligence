"""Validated environment settings shared by API, CLI, and worker entry points."""

from pathlib import Path
from typing import Literal

from pydantic import Field
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
