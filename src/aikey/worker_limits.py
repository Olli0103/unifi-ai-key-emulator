"""Finite, saved-only administrator controls; not runtime admission authority."""

# The UI deliberately offers a bounded subset of the existing worker options.
# It can introduce/tighten an inference gate, never remove it. Unknown worker
# options, privacy scopes and cost journals are not part of this contract.
LIMITS = {
    "max_queue": ("Queued jobs", 8, 1, 1024),
    "max_concurrency": ("Worker slots", 1, 1, 32),
    "inference_concurrency": ("Concurrent gated vision requests", None, 1, 32),
    "timeout_s": ("General job deadline (seconds)", 120, 1, 900),
    "caption_timeout_s": ("Caption deadline (seconds)", 30, 1, 600),
    "speech_timeout_s": ("Speech request deadline (seconds)", 300, 1, 900),
}


def saved_limits(worker):
    if not isinstance(worker, dict):
        raise ValueError("Worker settings unavailable")
    values = {name: worker.get(name, spec[1]) for name, spec in LIMITS.items()}
    for name, value in values.items():
        if name == "inference_concurrency" and value is None:
            continue
        if type(value) is not int or not LIMITS[name][2] <= value <= LIMITS[name][3]:
            raise ValueError("Worker settings outside editor bounds")
    if (
        values["inference_concurrency"] is not None
        and values["inference_concurrency"] > values["max_concurrency"]
    ):
        raise ValueError("Model request limit exceeds worker slots")
    return values


def proposed_limits(values):
    if not isinstance(values, dict) or set(values) != set(LIMITS):
        raise ValueError("Choose all six processing limits")
    result = saved_limits(values)
    if result["inference_concurrency"] is None:
        raise ValueError("An explicit finite model request limit is required")
    return result
