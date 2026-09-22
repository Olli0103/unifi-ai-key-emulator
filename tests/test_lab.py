from aikey.lab import run_lab


async def test_assembled_service_against_independent_loopback_controller():
    result = await run_lab()
    assert result["passed"] is True
    assert result["counts"]["callbacks"] == 1
    assert result["counts"]["connections"] == 2
    assert result["real_controller_contacted"] is False
    assert "persisted_job_deduplication" in result["checks"]
