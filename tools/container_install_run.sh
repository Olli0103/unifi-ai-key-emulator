#!/usr/bin/env bash
# Shared native clean-install path. No publication or credentials.
set -euo pipefail
case "${1:-}" in
  context)
set -euo pipefail
umask 077
tool="${GITHUB_WORKSPACE}/tools/container_install_check.py"
python "${tool}" context --repository "${SOURCE_REPOSITORY}" \
  --revision "${SOURCE_REVISION}" --nonce "${INSTALL_NONCE}"
test "$(git rev-parse HEAD)" = "${SOURCE_REVISION}"
test "$(uname -s)" = Linux
case "${INSTALL_ARCH}:$(uname -m)" in
  amd64:x86_64|arm64:aarch64|arm64:arm64) ;;
  *) exit 1 ;;
esac
server_arch="$(docker version --format '{{.Server.Arch}}')"
test "${server_arch}" = "${INSTALL_ARCH}"
python tools/community_release_check.py --audit-only
mkdir "${RUNNER_TEMP}/container-results"
python "${tool}" manifest --source "${GITHUB_WORKSPACE}" \
  --revision "${SOURCE_REVISION}" --output "${RUNNER_TEMP}/container-expected.json"
# This file contains public source digests only. The non-root guest
# must be able to read the explicit read-only file mount.
chmod 644 "${RUNNER_TEMP}/container-expected.json"
python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['RUNNER_TEMP'])
value = json.loads((root/'container-expected.json').read_text())
first = sorted(value['modules'])[0]
value['modules'][first] = '0'*64
target = root/'container-wrong-source.json'
target.write_text(json.dumps(value))
target.chmod(0o644)
PY
    ;;
  scanner)
set -euo pipefail
umask 077
python tools/image_sbom_check.py install --architecture "${INSTALL_ARCH}" \
  --directory "${RUNNER_TEMP}/image-inventory-tool"
    ;;
  build)
set -euo pipefail
umask 077
tool="${GITHUB_WORKSPACE}/tools/container_install_check.py"
if ! context="$(python "${tool}" context --repository "${SOURCE_REPOSITORY}" \
    --revision "${SOURCE_REVISION}" --nonce "${INSTALL_NONCE}")"; then
  exit 1
fi
results="${RUNNER_TEMP}/container-results"
for role in aikey aiport; do
  image="local-aikey-install:${INSTALL_NONCE}-${role}"
  volume="aikey-ci-${INSTALL_NONCE}-${role}"
  recipe=Dockerfile
  if [ "${role}" = aiport ]; then recipe=Dockerfile.aiport-candidate; fi
  # Docker's maintained immutable Git context excludes ignored local
  # state. No archive extractor, build secret, QEMU or image push.
  docker buildx build --load --platform "linux/${INSTALL_ARCH}" \
    --file "${recipe}" --tag "${image}" \
    --label "org.opencontainers.image.revision=${SOURCE_REVISION}" \
    --label "org.opencontainers.image.source=https://github.com/${SOURCE_REPOSITORY}" \
    --label "org.local-aikey.install-run=${INSTALL_NONCE}" \
    --label "org.local-aikey.install-profile=${role}" "${context}"
  observed="$(docker image inspect --format '{{.Os}}/{{.Architecture}}|{{.Config.User}}|{{ index .Config.Labels "org.local-aikey.install-run" }}|{{ index .Config.Labels "org.opencontainers.image.revision" }}' "${image}")"
  test "${observed}" = "linux/${INSTALL_ARCH}|10001:10001|${INSTALL_NONCE}|${SOURCE_REVISION}"
  # Scan the inspected immutable local image ID before mounting any
  # synthetic state. No registry fallback, upload or image execution
  # by the scanner. Native raw metadata stays temporary on the runner.
  python tools/image_sbom_check.py collect --architecture "${INSTALL_ARCH}" \
    --repository "${SOURCE_REPOSITORY}" --revision "${SOURCE_REVISION}" \
    --nonce "${INSTALL_NONCE}" --profile "${role}" --source "${GITHUB_WORKSPACE}" \
    --tool "${RUNNER_TEMP}/image-inventory-tool/syft" --results "${results}"
  docker run --pull=never --rm --name "aikey-ci-${INSTALL_NONCE}-${role}-ffmpeg" \
    --label "org.local-aikey.install-run=${INSTALL_NONCE}" \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --entrypoint /usr/bin/ffmpeg "${image}" -hide_banner -version
  printf '%s\n' 'synthetic-install-fixture-only' | \
    docker run --pull=never --rm --interactive --name "aikey-ci-${INSTALL_NONCE}-${role}-openssl" \
      --label "org.local-aikey.install-run=${INSTALL_NONCE}" \
      --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
      --entrypoint /usr/bin/openssl "${image}" passwd -6 -stdin > /dev/null
  docker volume create --label "org.local-aikey.install-run=${INSTALL_NONCE}" "${volume}"
  docker run --pull=never --rm --name "aikey-ci-${INSTALL_NONCE}-${role}-init" \
    --label "org.local-aikey.install-run=${INSTALL_NONCE}" \
    --network none --read-only --user 0:0 --cap-drop ALL --cap-add CHOWN \
    --security-opt no-new-privileges --volume "${volume}:/state" \
    --entrypoint /usr/local/bin/python "${image}" -c \
    'import os; os.chmod("/state", 0o700); os.chown("/state", 10001, 10001)'
  for case_name in create wrong-source wrong-arch reopen; do
    phase=reopen
    expected="${RUNNER_TEMP}/container-expected.json"
    architecture="${INSTALL_ARCH}"
    if [ "${case_name}" = create ]; then phase=create; fi
    if [ "${case_name}" = wrong-source ]; then
      expected="${RUNNER_TEMP}/container-wrong-source.json"
    fi
    if [ "${case_name}" = wrong-arch ]; then
      architecture=arm64
      if [ "${INSTALL_ARCH}" = arm64 ]; then architecture=amd64; fi
    fi
    response="${results}/${role}-${case_name}.body"
    status=0
    docker run --pull=never --rm --name "aikey-ci-${INSTALL_NONCE}-${role}-${case_name}" \
      --label "org.local-aikey.install-run=${INSTALL_NONCE}" \
      --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 --workdir /tmp \
      --volume "${volume}:/state" \
      --mount "type=bind,source=${tool},target=/fixture/probe.py,readonly" \
      --mount "type=bind,source=${expected},target=/fixture/expected.json,readonly" \
      --entrypoint /usr/local/bin/python "${image}" /fixture/probe.py probe \
      --expected /fixture/expected.json --architecture "${architecture}" \
      --profile "${role}" --phase "${phase}" > "${response}" || status=$?
    python "${tool}" record --response "${response}" --exit-code "${status}" \
      --profile "${role}" --case "${case_name}" --architecture "${INSTALL_ARCH}" \
      --revision "${SOURCE_REVISION}" --output "${results}/${role}-${case_name}.json"
  done
done
    ;;
  cleanup)
set -euo pipefail
umask 077
tool="${GITHUB_WORKSPACE}/tools/container_install_check.py"
# Validate scope again before any cleanup, even if preflight failed.
python "${tool}" context --repository "${SOURCE_REPOSITORY}" \
  --revision "${SOURCE_REVISION}" --nonce "${INSTALL_NONCE}" > /dev/null
ids="$(docker ps --all --quiet --no-trunc --filter "label=org.local-aikey.install-run=${INSTALL_NONCE}")"
for id in ${ids}; do
  [[ "${id}" =~ ^[0-9a-f]{64}$ ]]
  docker rm --force "${id}"
done
volumes="$(docker volume ls --quiet --filter "label=org.local-aikey.install-run=${INSTALL_NONCE}")"
for volume in ${volumes}; do
  case "${volume}" in
    "aikey-ci-${INSTALL_NONCE}-aikey"|"aikey-ci-${INSTALL_NONCE}-aiport") ;;
    *) exit 1 ;;
  esac
  docker volume rm "${volume}"
done
for role in aikey aiport; do
  image="local-aikey-install:${INSTALL_NONCE}-${role}"
  if docker image inspect "${image}" > /dev/null 2>&1; then
    owner="$(docker image inspect --format '{{ index .Config.Labels "org.local-aikey.install-run" }}' "${image}")"
    test "${owner}" = "${INSTALL_NONCE}"
    docker image rm "${image}"
  fi
done
# Each failed observation stops here; empty output from a failed
# command must never be mistaken for an empty resource set.
remaining="$(docker ps --all --quiet --filter "label=org.local-aikey.install-run=${INSTALL_NONCE}")"
test -z "${remaining}"
remaining="$(docker volume ls --quiet --filter "label=org.local-aikey.install-run=${INSTALL_NONCE}")"
test -z "${remaining}"
remaining="$(docker image ls --filter "label=org.local-aikey.install-run=${INSTALL_NONCE}" --format '{{.Repository}}:{{.Tag}}')"
test -z "${remaining}"
python - <<'PY'
import json, os
from pathlib import Path
target = Path(os.environ['RUNNER_TEMP'])/'container-results/cleanup.json'
value = {'schema':'aikey-container-install-cleanup/1',
         'revision':os.environ['SOURCE_REVISION'], 'architecture':os.environ['INSTALL_ARCH'],
         'containers':0, 'volumes':0, 'image_tags':0}
with target.open('x') as output:
    json.dump(value, output)
PY
python "${tool}" summary --results "${RUNNER_TEMP}/container-results" \
  --revision "${SOURCE_REVISION}" --architecture "${INSTALL_ARCH}" \
  --output "${RUNNER_TEMP}/container-results/summary.json"
python tools/image_sbom_check.py finalize --results "${RUNNER_TEMP}/container-results" \
  --revision "${SOURCE_REVISION}" --architecture "${INSTALL_ARCH}" --nonce "${INSTALL_NONCE}" \
  --output "${RUNNER_TEMP}/container-results/image-inventory.json"
    ;;
  *) exit 2 ;;
esac
