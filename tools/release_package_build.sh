#!/usr/bin/env bash
# Called only after the stage environment approved this exact dispatch.
set -euo pipefail
umask 077
python tools/release_prepare.py --tag "${RELEASE_TAG}" --publish > "${RUNNER_TEMP}/package-preparation.json"
python -m pip install --disable-pip-version-check build==1.6.1 setuptools==84.0.0 cyclonedx-bom==7.4.0
epoch="$(git log -1 --format=%ct)"
for number in 1 2; do
  directory="${RUNNER_TEMP}/release-build-${number}"
  git clone --quiet --no-local "${GITHUB_WORKSPACE}" "${directory}"
  (
    cd "${directory}"
    SOURCE_DATE_EPOCH="${epoch}" PYTHONHASHSEED=0 python -m build --no-isolation --outdir dist
  )
  python tools/release_check.py verify "${directory}/dist" --epoch "${epoch}" \
    --forbid "${HOME}" --forbid "${directory}" --forbid "${GITHUB_WORKSPACE}" \
    --manifest-out "${RUNNER_TEMP}/package-build-${number}.json"
done
python tools/release_check.py compare "${RUNNER_TEMP}/package-build-1.json" "${RUNNER_TEMP}/package-build-2.json"
python tools/community_release_check.py --dist "${RUNNER_TEMP}/release-build-1/dist"
mkdir "${RUNNER_TEMP}/release-packages"
cp "${RUNNER_TEMP}/release-build-1/dist/"* "${RUNNER_TEMP}/release-packages/"
python -m venv "${RUNNER_TEMP}/release-runtime"
"${RUNNER_TEMP}/release-runtime/bin/python" -m pip install --disable-pip-version-check --no-deps -r requirements-runtime.lock
"${RUNNER_TEMP}/release-runtime/bin/python" -m pip install --disable-pip-version-check --no-deps "${RUNNER_TEMP}/release-packages/"*.whl
"${RUNNER_TEMP}/release-runtime/bin/python" -m pip check
python -m cyclonedx_py environment "${RUNNER_TEMP}/release-runtime" --pyproject pyproject.toml \
  --output-reproducible --of JSON -o "${RUNNER_TEMP}/release-packages/package.cdx.json"
python - <<'PY'
import importlib.util, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'tools'))
import release_candidate as candidate
plan=json.loads((Path(os.environ['RUNNER_TEMP'])/'package-preparation.json').read_text())
folder=Path(os.environ['RUNNER_TEMP'])/'release-packages'
candidate.publication.verify_packages(Path.cwd(),plan,folder)
candidate.package_bom(Path.cwd(),plan,folder/'package.cdx.json')
PY
