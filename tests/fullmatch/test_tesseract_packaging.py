from pathlib import Path

from football_intelligence.settings import Settings


def test_dockerfile_pins_tessdata_fast_english_model_and_verifies_checksum():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "tesseract-ocr" in dockerfile
    assert "87416418657359cb625c412a48b6e1d6d41c29bd" in dockerfile
    assert "tessdata_fast/${TESSDATA_FAST_COMMIT}" in dockerfile
    assert "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2" in dockerfile
    assert "sha256sum --check" in dockerfile
    assert "TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata_fast" in dockerfile


def test_scoreboard_region_and_tessdata_location_are_environment_configurable():
    settings = Settings(
        tessdata_dir="/models/tessdata_fast",
        scoreboard_region_x=0.1,
        scoreboard_region_y=0.02,
        scoreboard_region_width=0.8,
        scoreboard_region_height=0.15,
    )

    assert settings.tessdata_dir == Path("/models/tessdata_fast")
    assert settings.scoreboard_region == (0.1, 0.02, 0.8, 0.15)
