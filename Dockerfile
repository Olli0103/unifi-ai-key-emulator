FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 aikey \
    && useradd --uid 10001 --gid 10001 --create-home aikey
WORKDIR /app
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.txt /app/
COPY requirements-runtime.lock /app/
COPY requirements-build.lock /app/
COPY src /app/src
RUN pip install --no-deps -r requirements-build.lock -r requirements-runtime.lock \
    && pip install --no-deps --no-build-isolation . \
    && pip check
USER 10001:10001
ENTRYPOINT ["local-aikey"]
CMD ["check", "--config", "/state/config.json"]
