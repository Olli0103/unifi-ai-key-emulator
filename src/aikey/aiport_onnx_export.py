"""Export an already pinned local RF-DETR Nano checkpoint for ONNX trials."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from .aiport_detection import DetectionError, RFDetrNanoDetector
from .aiport_onnx_detection import validate_onnx_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    if (not output_dir.is_absolute() or output_dir.is_symlink()
            or not output_dir.is_dir() or any(output_dir.iterdir())
            or output_dir.stat().st_mode & 0o077):
        parser.error("output-dir must be an empty private directory")
    old_umask = os.umask(0o077)
    try:
        model = RFDetrNanoDetector.from_checkpoint(
            args.checkpoint, args.checkpoint_sha256)
        artifact = Path(model.model.export(output_dir=str(output_dir), verbose=False))
        if (artifact.parent != output_dir or artifact.suffix != ".onnx"
                or artifact.is_symlink() or not artifact.is_file()):
            raise DetectionError("invalid_onnx_export")
        artifact.chmod(0o600)
        hasher = hashlib.sha256()
        with artifact.open("rb") as source:
            while block := source.read(1024 * 1024):
                hasher.update(block)
        digest = hasher.hexdigest()
        validate_onnx_model(str(artifact), digest)
    finally:
        os.umask(old_umask)
    print(f"{artifact} {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
