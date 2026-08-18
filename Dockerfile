FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg curl \
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
