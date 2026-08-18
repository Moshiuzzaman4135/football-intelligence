# Test video assets

Downloaded video fixtures are intentionally Git-ignored. Generated deterministic fixtures are created during tests or with `python -m football_intelligence.demo_fixture`.

## Real-footage fixture

- Local ignored path: `tests/assets/football-demo.mp4`
- Source page: https://www.pexels.com/video/training-field-854173/
- Direct file used: `https://videos.pexels.com/video-files/854173/854173-hd_1920_1080_25fps.mp4`
- Source page terms: Free to use (CC0)
- SHA-256: `00b79b097cc0e9a68fcb40f29aa9bfd18ebc2bfa81e9cd299d61ded86db9e49a`
- Media: H.264 MP4, 1920x1080, 25 FPS, 17.44 seconds, 7,703,029 bytes

Re-download without committing the media:

```bash
curl --fail --location \
  'https://videos.pexels.com/video-files/854173/854173-hd_1920_1080_25fps.mp4' \
  --output tests/assets/football-demo.mp4
sha256sum tests/assets/football-demo.mp4
```
