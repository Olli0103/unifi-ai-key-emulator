"""Owned, deterministic cartoon/PCM stress inputs, never real camera footage.

These inputs exercise a declared synthetic person-presence task. Simulated
lighting/weather is not evidence about real weather, IR cameras or field quality.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import struct
import wave

from PIL import Image, ImageDraw, ImageFilter


VISION = (
    ("person", "day"), ("person", "night"), ("person", "glare"),
    ("person", "rain"), ("person", "motion_blur"), ("person", "occlusion"),
    ("person", "multi_object"), ("person", "day_edge"),
    ("empty", "day"), ("empty", "night"), ("empty", "glare"),
    ("empty", "rain"), ("empty", "motion_blur"), ("empty", "occlusion"),
    ("empty", "false_motion"), ("package", "multi_object"),
    ("cat", "day"), ("cat", "night"), ("dog", "day"), ("dog", "night"),
    ("vehicle", "day"), ("vehicle", "glare"),
    ("package", "day"), ("package", "night"),
)
CONDITIONS = frozenset({"day", "night", "glare", "rain", "pets", "motion_blur",
                        "occlusion", "false_motion", "multi_object"})


def _person(draw, x, scale=1):
    def box(a, b, c, d):
        return tuple(round(v) for v in (x + a * scale, b * scale, x + c * scale, d * scale))
    draw.ellipse(box(-24, 85, 24, 133), fill="#d89c73", outline="#49372b", width=3)
    draw.rectangle(box(-31, 145, 31, 246), fill="#ba4540", outline="#49372b", width=3)
    draw.line([box(-30, 151, -58, 224)[:2], box(-30, 151, -58, 224)[2:]],
              fill="#d89c73", width=round(15 * scale))
    draw.line([box(30, 151, 58, 224)[:2], box(30, 151, 58, 224)[2:]],
              fill="#d89c73", width=round(15 * scale))
    for offset in (-19, 19):
        draw.line([(x + offset * scale, 242 * scale),
                   (x + offset * scale, 322 * scale)], fill="#324569", width=round(24 * scale))
        draw.ellipse(box(offset - 17, 314, offset + 17, 331), fill="#252525")


def frame(subject, condition):
    picture = Image.new("RGB", (512, 384), "#a4c8dd")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 220, 512, 384), fill="#86a073")
    draw.rectangle((20, 45, 150, 210), fill="#e6dccc", outline="#655d54", width=5)
    draw.rectangle((37, 65, 133, 135), fill="#a4c8dd", outline="#655d54", width=4)
    draw.line((0, 334, 512, 334), fill="#4f6546", width=3)
    if subject == "person":
        for x in ((185, 345) if condition == "multi_object" else
                  (94,) if condition == "day_edge" else (284,)):
            _person(draw, x)
    elif subject in ("cat", "dog"):
        draw.ellipse((225, 220, 350, 304), fill="#ac7b48", outline="#4c3422", width=4)
        draw.ellipse((190, 170, 272, 248), fill="#ac7b48", outline="#4c3422", width=4)
        if subject == "cat":
            draw.polygon(((194, 190), (199, 144), (224, 175)), fill="#ac7b48", outline="#4c3422")
            draw.polygon(((240, 176), (266, 146), (268, 200)), fill="#ac7b48", outline="#4c3422")
            draw.arc((328, 170, 398, 276), 235, 95, fill="#4c3422", width=12)
        else:
            draw.ellipse((179, 181, 211, 244), fill="#68462e")
            draw.ellipse((250, 176, 279, 242), fill="#68462e")
            draw.ellipse((207, 213, 252, 242), fill="#d6ad7c", outline="#4c3422", width=2)
        for x in (238, 320):
            draw.rectangle((x, 275, x + 18, 329), fill="#ac7b48", outline="#4c3422", width=3)
        for x in (214, 247):
            draw.ellipse((x, 196, x + 6, 202), fill="#201b17")
    elif subject == "vehicle":
        draw.rounded_rectangle((180, 224, 428, 297), radius=16, fill="#437eac", outline="#263946", width=4)
        draw.polygon(((225, 224), (253, 170), (354, 170), (389, 224)),
                     fill="#437eac", outline="#263946")
        draw.polygon(((246, 217), (264, 184), (303, 184), (303, 217)), fill="#c6e5ef")
        draw.polygon(((315, 184), (346, 184), (369, 217), (315, 217)), fill="#c6e5ef")
        for x in (231, 377):
            draw.ellipse((x - 26, 276, x + 26, 328), fill="#252a30", outline="#121519", width=3)
            draw.ellipse((x - 10, 292, x + 10, 312), fill="#a7afb4")
    elif subject == "package":
        for x in ((204, 351) if condition == "multi_object" else (285,)):
            draw.rectangle((x - 52, 230, x + 52, 331), fill="#be925d", outline="#5c422b", width=4)
            draw.rectangle((x - 8, 232, x + 8, 330), fill="#e5ca9e")
            draw.line((x - 50, 280, x + 50, 280), fill="#e5ca9e", width=13)
    elif subject != "empty":
        raise ValueError("Unknown synthetic subject")
    if condition == "occlusion":
        draw.rectangle((0, 269, 512, 292), fill="#5c6a56")
    if condition == "false_motion":
        for x in range(190, 425, 34):
            draw.arc((x, 80, x + 55, 304), 70, 260, fill="#ded9c8", width=16)
    if condition == "rain":
        rng = random.Random(71903)
        for _ in range(100):
            x, y = rng.randrange(512), rng.randrange(384)
            draw.line((x, y, x - 5, y + 17), fill="#bacbd3", width=1)
    if condition == "night":
        table = [round(v * 0.43) for v in range(256)]
        picture = picture.point(table * 3)
    elif condition == "glare":
        overlay = Image.new("RGB", picture.size, "white")
        picture = Image.blend(picture, overlay, 0.28)
    elif condition == "motion_blur":
        picture = picture.filter(ImageFilter.GaussianBlur(2.3))
    return picture


def generate(root):
    root = Path(root)
    root.mkdir(mode=0o700)
    images = root / "images"
    images.mkdir(mode=0o700)
    inputs, gold = [], []
    for number, (subject, condition) in enumerate(VISION, 1):
        identity = f"synthetic-{number:02}"
        path = images / f"{identity}.png"
        frame(subject, condition).save(path, format="PNG", compress_level=9)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        tags = ["day" if condition == "day_edge" else condition]
        if subject in ("cat", "dog"):
            tags.append("pets")
        record = {"case_id": identity, "file_name": path.name, "input_sha256": digest,
                  "camera_class": "synthetic-outdoor", "conditions": sorted(tags)}
        inputs.append(record)
        gold.append({**record, "expected": "person" if subject == "person" else "none",
                     "subject": subject})
    audio = root / "audio"
    audio.mkdir(mode=0o700)
    audio_rows = []
    for kind in ("silence", "noise"):
        path = audio / f"synthetic-{kind}.wav"
        rng = random.Random(71903)
        samples = [0 if kind == "silence" else rng.randrange(-900, 901) for _ in range(32000)]
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            out.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        audio_rows.append({"id": f"synthetic-{kind}", "file_name": path.name,
                           "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "expected": "", "conditions": ["audio", kind],
                           "scope": "negative PCM input only; no speech recognition quality claim"})
    for filename, value in (("inputs.json", {"cases": inputs}),
                            ("gold.json", {"cases": gold, "audio": audio_rows})):
        (root / filename).write_text(json.dumps(value, indent=2) + "\n")
    # IDs appear in the contact sheet only, never in a scored image.
    sheet = Image.new("RGB", (512 * 4, (384 + 24) * 6), "white")
    draw = ImageDraw.Draw(sheet)
    for number, row in enumerate(inputs):
        x, y = number % 4 * 512, number // 4 * 408
        with Image.open(images / row["file_name"]) as picture:
            sheet.paste(picture, (x, y + 24))
        draw.text((x + 8, y + 4), row["case_id"], fill="black")
    sheet.save(root / "contact-sheet.png")
    return {"vision_cases": len(inputs), "audio_negative_cases": len(audio_rows),
            "conditions": sorted({c for row in inputs for c in row["conditions"]}),
            "declared_synthetic_only": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(json.dumps(generate(parser.parse_args().output)))
