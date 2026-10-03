"""Synthetic night-IR scenes for the held-package follow-up (no real frames)."""

from io import BytesIO
import random

from PIL import Image, ImageDraw

from aikey.aiport_held_followup import HeldPackageFollowup


BOX = (0.40, 0.60, 0.55, 0.85)          # normalized held box
PIXELS = (256, 216, 352, 306)            # the same box on a 640x360 frame


def _scene(seed, *, shift=0, patch=None, brightness=0, cat=False):
    """Grey hallway with an object in BOX and per-frame sensor noise."""
    rng = random.Random(seed)
    image = Image.new("L", (640, 360), 90 + brightness)
    draw = ImageDraw.Draw(image)
    for y in range(0, 360, 8):                    # floor texture
        draw.line((0, y, 640, y), fill=80 + brightness + (y % 24))
    x1, y1, x2, y2 = PIXELS
    if cat:   # curled body, head and ears: IR-bright fur, no box edges
        draw.ellipse((x1 + shift, y1 + 20, x2 + shift, y2), fill=160 + brightness)
        draw.ellipse((x2 - 38 + shift, y1, x2 - 2 + shift, y1 + 34), fill=165 + brightness)
        draw.polygon([(x2 - 36 + shift, y1 + 6), (x2 - 30 + shift, y1 - 8),
                      (x2 - 24 + shift, y1 + 4)], fill=165 + brightness)
    else:
        draw.rectangle((x1 + shift, y1, x2 + shift, y2), fill=150 + brightness)
        draw.line((x1 + shift, (y1 + y2) // 2, x2 + shift, (y1 + y2) // 2),
                  fill=120 + brightness, width=3)
    if patch is not None:                           # e.g. a breathing flank or an ear
        px, py, level = patch
        draw.ellipse((px, py, px + 22, py + 16), fill=level + brightness)
    noisy = image.point(lambda v: v)
    pixels = noisy.load()
    for _ in range(4000):
        x, y = rng.randrange(640), rng.randrange(360)
        pixels[x, y] = max(0, min(255, pixels[x, y] + rng.randint(-6, 6)))
    out = BytesIO()
    noisy.convert("RGB").save(out, format="JPEG", quality=80)
    return out.getvalue()


def _run(frames_after_start, *, start_frame=None, seconds=0.5):
    followup = HeldPackageFollowup()
    assert followup.start("CAM", 7, BOX, start_frame or _scene(0), now=0.0)
    decision = "keep"
    for index, frame in enumerate(frames_after_start, start=1):
        decision = followup.observe("CAM", frame, now=index * seconds).get(7, decision)
        if decision != "keep":
            break
    return decision, followup


def test_a_parcel_that_stays_inert_is_confirmed_after_the_dwell():
    decision, followup = _run([_scene(seed) for seed in range(1, 50)])
    assert decision == "confirmed"
    assert followup.decisions["confirmed"] == 1
    assert followup.tracking("CAM") == frozenset()


def test_a_visibly_moving_resting_animal_is_animal_like():
    # Large repeated change inside the box (ear flick, tail). A sleeping cat
    # does not do this: see the inert-cat control below.
    frames = [_scene(seed, patch=(290, 235 + (seed % 3) * 4, 60 if seed % 2 else 200))
              for seed in range(1, 50)]
    decision, _followup = _run(frames)
    assert decision == "animated"


def test_an_object_that_walks_away_is_moved_not_confirmed():
    frames = [_scene(seed) for seed in range(1, 6)] + [_scene(seed, shift=180)
                                                       for seed in range(6, 50)]
    decision, _followup = _run(frames)
    assert decision == "moved"


def test_lights_or_ir_level_changing_the_whole_scene_is_not_movement():
    frames = [_scene(seed, brightness=35) for seed in range(1, 50)]
    decision, _followup = _run(frames)
    assert decision == "confirmed"


def test_no_frames_for_longer_than_the_window_expires():
    followup = HeldPackageFollowup()
    followup.start("CAM", 7, BOX, _scene(0), now=0.0)
    assert followup.observe("CAM", _scene(1), now=31.0) == {7: "expired"}


def test_nothing_is_decided_before_the_dwell_or_minimum_frames():
    decision, _followup = _run([_scene(seed) for seed in range(1, 20)])
    assert decision == "keep"


def test_follow_up_is_bounded_per_camera_and_ignores_unreadable_frames():
    followup = HeldPackageFollowup()
    assert followup.start("CAM", 1, BOX, _scene(0), now=0.0)
    assert followup.start("CAM", 2, BOX, _scene(0), now=0.0)
    assert not followup.start("CAM", 3, BOX, _scene(0), now=0.0)
    assert not followup.start("OTHER", 1, BOX, b"not a jpeg", now=0.0)
    assert followup.observe("CAM", b"not a jpeg", now=1.0) == {1: "keep", 2: "keep"}


def test_a_sleeping_cat_without_measurable_movement_is_indistinguishable_from_a_parcel():
    # Esszimmer 26 Sep 05:00: the real resting object gave no measurable
    # in-box motion and the shadow verdict was "confirmed"; a Protect Animal
    # event followed at 05:10. This local signal cannot veto such a cat, so
    # the follow-up must stay in shadow for startup-held night-IR candidates.
    decision, _followup = _run([_scene(seed, cat=True) for seed in range(1, 50)],
                               start_frame=_scene(0, cat=True))
    assert decision == "confirmed"
