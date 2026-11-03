"""Prospectively frozen staged-index quality, before any live vector swap."""
from __future__ import annotations

from .index_readback import NativeReadbackPlan, ReadbackError, check_ranked


def validate_plan(run, plan):
    """Admission before persisting the prospectively frozen targets or model work."""
    from .index_session_readback import SessionReadbackPlan
    if (type(plan) not in (NativeReadbackPlan, SessionReadbackPlan) or plan.space != run.profile_kind
            or not plan.matches(
            source_archive_sha256=run.journal.get('source_archive_sha256'),
            from_revision=run.journal['from_revision'], target_revision=run.target_revision)):
        raise ReadbackError('The quality plan does not match the frozen generation and source')
    if ((plan.value.get('from_profile_fingerprint') is not None
            and plan.value['from_profile_fingerprint'] != run.journal.get('from_fingerprint'))
            or (plan.value.get('target_profile_fingerprint') is not None
            and plan.value['target_profile_fingerprint'] != run.target_profile['fingerprint'])):
        raise ReadbackError('The staged quality plan belongs to another full encoder identity')
    if run.state != 'verified':
        raise ReadbackError('Full staged coverage must be verified before quality evaluation')
    if plan.value.get('empty_index') is True and run.store.live_counts()['rows'] != 0:
        raise ReadbackError('An empty-index plan cannot qualify existing indexed history')
    run._check_source_identity()


async def qualify_staged(run, embedder, plan: NativeReadbackPlan):
    """Called under the generation writer lock; no live query/config changes."""
    from .index_session_readback import check_sessions
    validate_plan(run, plan)
    identity = run.store.staged_identity(run.tag)
    outcomes = []
    for query in plan.value['queries']:
        await run._check_embedder(embedder, run.target_revision)
        vector = await embedder.embed_text(query['text'])
        from .index_rebuild import _check_vector
        vector = _check_vector(vector, run.dimensions)
        await run._check_embedder(embedder, run.target_revision)
        if plan.space == 'e5-session-v1':
            rows = run.store.rank_sessions(run.tag, vector, k=query['k'],
                min_cosine=query['min_cosine'], camera_ids=query['camera_ids'],
                start_ms=query['start_ms'], end_ms=query['end_ms'])
            outcome = check_sessions(query, rows)
        else:
            rows = run.store.rank_staged(run.tag, vector, k=query['k'],
                min_cosine=query['min_cosine'], camera_id=query['camera_id'],
                start_ms=query['start_ms'], end_ms=query['end_ms'])
            outcome = check_ranked(query, rows)
        if not outcome['passed']:
            raise ReadbackError('The frozen staged-index quality target was not met')
        outcomes.append(outcome)
    if identity != run.store.staged_identity(run.tag):
        raise ReadbackError('The staged index changed during quality evaluation')
    return {'schema': 'aikey-staged-index-quality/1', 'passed': True,
            'plan_sha256': plan.sha256, 'source_archive_sha256': run.journal['source_archive_sha256'],
            'target_revision': run.target_revision, 'staged_identity': identity, 'queries': outcomes,
            'empty_index': plan.value.get('empty_index', False)}
