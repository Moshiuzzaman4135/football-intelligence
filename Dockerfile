FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata_fast

ARG APP_UID=1000
ARG APP_GID=1000
ARG TESSDATA_FAST_COMMIT=87416418657359cb625c412a48b6e1d6d41c29bd
ARG TESSDATA_FAST_ENG_SHA256=7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg curl tesseract-ocr \
    && mkdir -p "${TESSDATA_PREFIX}" /usr/share/doc/tessdata-fast \
    && curl --fail --silent --show-error --location \
      "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_FAST_COMMIT}/eng.traineddata" \
      --output "${TESSDATA_PREFIX}/eng.traineddata" \
    && printf '%s  %s\n' "${TESSDATA_FAST_ENG_SHA256}" "${TESSDATA_PREFIX}/eng.traineddata" \
      | sha256sum --check \
    && curl --fail --silent --show-error --location \
      "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_FAST_COMMIT}/LICENSE" \
      --output /usr/share/doc/tessdata-fast/LICENSE \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --create-home app

WORKDIR /app

COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -e '.[dev]'

COPY --chown=app:app . .

USER app

EXPOSE 8000 8501
CMD ["uvicorn", "football_intelligence.api:app", "--host", "0.0.0.0", "--port", "8000"]
