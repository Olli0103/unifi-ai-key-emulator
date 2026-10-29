# Runtime dependency refresh, 29 October 2026

The dependency audit on commit `6acb3ca` failed on the existing runtime lock. It reported 47 advisory entries across aiohttp 3.14.3 and Pillow 12.3.0. Some entries are aliases for the same vulnerability; this is not a count of distinct defects in this project.

## Changes

- Pin aiohttp to 3.14.5 in the package metadata and runtime lock. Version 3.14.4 contains the security fixes identified by the audit. Version 3.14.5 also fixes a WebSocket control-frame regression.
- Require Pillow 13 and pin 13.0.0 in the runtime lock. Keeping the old `<13` constraint would exclude the fixes. The development extra uses the same range.

The official Pillow 13 migration notes were checked against the image operations in this repository. The project already requires Python 3.12, above Pillow's new Python 3.11 minimum. It does not use the removed `Image._show`, `get_child_images`, profile metadata or `ExifTags.IFD.Makernote` APIs, or the removed dtype-changing `Image.fromarray` mode argument. Existing image-processing tests cover the actual JPEG, crop, resize and transformation paths with synthetic inputs.

No audit exceptions, ignored advisories or weakened checks were added. This source change does not update any deployed container. Native Protect acceptance, optional model paths and media tests skipped for missing prerequisites remain separate checks.

## Local validation

Python 3.12 on macOS ARM64, with the existing Homebrew OpenSSL selected for the test process:

- Full suite: 1,937 passed, 109 skipped, 56 subtests passed, five warnings. The warnings include the existing asyncio subprocess cleanup warning; they were not suppressed.
- Strict runtime dependency audit: no known vulnerabilities in the 16 resolved packages.
- Dependency consistency, Ruff and source/wheel package build: passed.

The source, tests and dependency manifests were fingerprinted before the full run and verified unchanged afterward. Linux CI and deployed runtime acceptance are separate from these local results.

## Sources

Read on 29 October 2026:

- [aiohttp changelog](https://docs.aiohttp.org/en/stable/changes.html), versions 3.14.4 and 3.14.5.
- [Pillow 13.0.0 release and migration notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html).
- [Failed dependency audit](https://github.com/Olli0103/unifi-ai-key-emulator/actions/runs/40851171316/job/123370715242).
