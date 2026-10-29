"""Detection JSON cannot erase ambiguous fields or clamp nonfinite coordinates."""

import json

import pytest

from aikey.aiport_api_detection import ApiDetectionError, fallback_to_fractions, parse_detections


def detection_text(box, *, duplicate=None):
    item = ('{"kind":"person","label":"person","score":0.9,"box":'
            + json.dumps(box) + '}')
    if duplicate == "kind":
        item = item.replace('"kind":"person"', '"kind":"animal","kind":"person"')
    elif duplicate == "score":
        item = item.replace('"score":0.9', '"score":0.1,"score":0.9')
    elif duplicate == "box":
        item = item.replace('"box":', '"box":[0,0,0,0],"box":')
    result = '{"detections":[' + item + ']}'
    if duplicate == "detections":
        result = result.replace('{"detections":', '{"detections":[],"detections":', 1)
    return result


@pytest.mark.parametrize("mode", ["primary", "fallback"])
@pytest.mark.parametrize("field", ["detections", "kind", "score", "box"])
def test_duplicate_detection_fields_are_refused_before_last_value_can_win(mode, field):
    text = detection_text([100, 100, 500, 900] if mode == "fallback" else [.1, .1, .5, .9],
                          duplicate=field)
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        if mode == "fallback":
            text = fallback_to_fractions(text)
        parse_detections(text, threshold=.5)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_fallback_coordinates_are_not_clamped_into_valid_boxes(constant):
    box = f"[{constant},100,500,900]" if constant.startswith("-") else f"[100,100,{constant},900]"
    text = ('{"detections":[{"kind":"person","label":"person","score":0.9,"box":'
            + box + '}]}')
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        parse_detections(fallback_to_fractions(text), threshold=.5)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_primary_scores_keep_the_existing_refusal(constant):
    text = detection_text([.1, .1, .5, .9]).replace('"score":0.9', '"score":' + constant)
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        parse_detections(text, threshold=.5)


@pytest.mark.parametrize("mode", ["primary", "fallback"])
def test_escaped_duplicate_label_is_also_ambiguous(mode):
    text = detection_text([100, 100, 500, 900] if mode == "fallback" else [.1, .1, .5, .9])
    text = text.replace('"label":"person"', '"label":"dog","la\\u0062el":"person"')
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        if mode == "fallback":
            text = fallback_to_fractions(text)
        parse_detections(text, threshold=.5)


@pytest.mark.parametrize("mode", ["primary", "fallback"])
def test_unique_finite_invalid_item_keeps_valid_sibling(mode):
    text = detection_text([100, 100, 500, 900] if mode == "fallback" else [.1, .1, .5, .9])
    text = text.replace('[{"kind"', '[{"not_a_detection":true},{"kind"', 1)
    if mode == "fallback":
        text = fallback_to_fractions(text)
    rejected = {}
    result = parse_detections(text, threshold=.5, rejected=rejected)
    assert len(result) == 1 and result[0].box == (.1, .1, .5, .9)
    assert rejected == {"shape": 1}


@pytest.mark.parametrize("mode", ["primary", "fallback"])
def test_integer_larger_than_float_range_fails_without_overflow(mode):
    text = detection_text([100, 100, 500, 900] if mode == "fallback" else [.1, .1, .5, .9])
    text = text.replace('"score":0.9', '"score":' + "9" * 400)
    with pytest.raises(ApiDetectionError, match="invalid_api_detection_response"):
        if mode == "fallback":
            text = fallback_to_fractions(text)
        parse_detections(text, threshold=.5)


@pytest.mark.parametrize("mode", ["primary", "fallback"])
def test_unique_finite_primary_and_fallback_coordinates_still_match(mode):
    text = detection_text([100, 100, 500, 900] if mode == "fallback" else [.1, .1, .5, .9])
    if mode == "fallback":
        text = fallback_to_fractions(text)
    result = parse_detections(text, threshold=.5)
    assert len(result) == 1 and result[0].kind == "person"
    assert result[0].box == (.1, .1, .5, .9) and result[0].score == .9
