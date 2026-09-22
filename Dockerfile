FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 aikey \
    && useradd --uid 10001 --gid 10001 --create-home aikey
WORKDIR /app
COPY pyproject.toml /app/
COPY requirements-runtime.lock /app/
COPY src /app/src
RUN pip install -r requirements-runtime.lock && pip install --no-deps .
USER 10001:10001
ENTRYPOINT ["local-aikey"]
CMD ["check", "--config", "/state/config.json"]
