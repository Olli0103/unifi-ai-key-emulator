"""Canonical-format model identities are metadata, never routes or loaded files."""

import copy
import json

import pytest

from aikey import benchmark, benchmark_multi, model_catalog
from test_benchmark import bound_corpus, corpus
from test_benchmark_multi import document


@pytest.mark.parametrize(
    "model",
    [
        "fictional-org/fictional-vision-v1",
        "fictional-org/vision-v1:latest",
        "accounts/fictional-team/models/fictional-vision",
        "fictional-model+variant_v1",
        "x" * 200,
    ],
)
def test_catalog_accepted_model_spellings_score_and_compare_without_normalization(model):
    assert model_catalog._MODEL_ID.fullmatch(model)
    for factory, scorer, comparator in [
        (bound_corpus, benchmark.score, benchmark.compare),
        (document, benchmark_multi.score, benchmark_multi.compare),
    ]:
        original = factory()
        original["run"]["model"] = model
        before = copy.deepcopy(original)
        result = scorer(original)
        assert original == before and result["run"]["model"] == model
        comparator(result, copy.deepcopy(result))
        other = factory()
        assert scorer(other)["corpus_sha256"] == result["corpus_sha256"]
    legacy = corpus()
    legacy["run"]["model"] = model
    assert benchmark.score(legacy)["run"]["model"] == model


@pytest.mark.parametrize(
    "model",
    [
        None,
        "",
        True,
        123,
        [],
        "x" * 201,
        "org/model\n",
        "org/model\x00",
        " org/model",
        "org/model ",
        "org/model with spaces",
        "org/模型",
        "/absolute-model",
    ],
)
def test_invalid_models_fail_with_fixed_diagnostics_in_both_scorers_and_comparators(model):
    for factory, scorer, comparator in [
        (bound_corpus, benchmark.score, benchmark.compare),
        (document, benchmark_multi.score, benchmark_multi.compare),
    ]:
        d = factory()
        report = scorer(d)
        d["run"]["model"] = model
        with pytest.raises(benchmark.BenchmarkError, match="run.model"):
            scorer(d)
        damaged = copy.deepcopy(report)
        damaged["run"]["model"] = model
        for left, right in [(report, damaged), (damaged, report)]:
            with pytest.raises(benchmark.BenchmarkError, match="run.model"):
                comparator(left, right)


@pytest.mark.parametrize("field", ["provider", "profile", "code_revision"])
def test_other_run_identifiers_do_not_inherit_model_path_vocabulary(field):
    for factory, scorer in [(bound_corpus, benchmark.score), (document, benchmark_multi.score)]:
        for value in ["namespace/value", "x" * 65, "PRIVATE-CANARY\nvalue"]:
            d = factory()
            d["run"][field] = value
            with pytest.raises(benchmark.BenchmarkError):
                scorer(d)
            clean = factory()
            report = scorer(clean)
            report["run"][field] = value
            comparator = benchmark.compare if scorer is benchmark.score else benchmark_multi.compare
            with pytest.raises(benchmark.BenchmarkError):
                comparator(report, report)


def test_case_and_camera_class_ids_are_still_tags_not_model_identifiers():
    for field in ["id", "camera_class"]:
        for factory, scorer in [(bound_corpus, benchmark.score), (document, benchmark_multi.score)]:
            d = factory()
            d["cases"][0][field] = "namespace/value"
            with pytest.raises(benchmark.BenchmarkError):
                scorer(d)


@pytest.mark.parametrize("kind", ["detection", "multimodal"])
def test_cli_round_trip_preserves_model_id_and_rejects_private_invalid_values(
    tmp_path, capsys, kind
):
    d = bound_corpus() if kind == "detection" else document()
    d["run"]["model"] = "fictional-org/vision-v1:tag"
    path = tmp_path / "fictional.json"
    path.write_text(json.dumps(d))
    score_command = "score" if kind == "detection" else "multi-score"
    compare_command = "compare" if kind == "detection" else "multi-compare"
    assert benchmark.main([score_command, str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["run"]["model"] == d["run"]["model"]
    path.write_text(json.dumps(result))
    assert benchmark.main([compare_command, str(path), str(path)]) == 0
    capsys.readouterr()
    d["run"]["model"] = "PRIVATE-CANARY\nmodel"
    path.write_text(json.dumps(d))
    assert benchmark.main([score_command, str(path)]) == 1
    output = capsys.readouterr()
    assert not output.out and "PRIVATE-CANARY" not in output.err and "Traceback" not in output.err


@pytest.mark.parametrize(
    "model", ["_legacy-local", "-legacy-local", ".legacy-local", ":legacy-tag", "_" * 64, ":" * 64]
)
def test_previously_valid_flat_model_tags_remain_accepted_verbatim(model):
    for factory, scorer, comparator in [
        (bound_corpus, benchmark.score, benchmark.compare),
        (document, benchmark_multi.score, benchmark_multi.compare),
    ]:
        d = factory()
        d["run"]["model"] = model
        result = scorer(d)
        assert result["run"]["model"] == model
        comparator(result, copy.deepcopy(result))
