"""Session text migration using the common guarded generation lifecycle."""
from pathlib import Path

from .encoding_identity import TEXT_ENCODING
from .index_rebuild import Rebuild, RebuildError, RebuildOutcomeUncertain, _atomic_bytes, _atomic_json, _require_approval
from .index_text_encoder import text_endpoint


class SessionRebuild(Rebuild):
    profile_kind = 'e5-session-v1'
    dimensions = 384
    model_names = ('multilingual-e5-small', 'intfloat/multilingual-e5-small')
    backends = ('http', 'sentence-transformers')
    target_encoding = TEXT_ENCODING
    allow_unversioned_source = True

    def __init__(self, store, state_dir, *, target_revision, target_source, **kwargs):
        if Path(state_dir).name != 'session-search':
            raise RebuildError('Session migrations require the separate session-search state directory')
        super().__init__(store, state_dir, target_revision=target_revision,
                         target_source=text_endpoint(target_source), **kwargs)
        self.root_profile = self.state_dir.parent/'search-profile.json'
        self.previous_root = self.directory/'previous-root-profile.json'

    def _target_identity(self, identity):
        # The reviewed target adapter is the version-reporting local E5 HTTP protocol.
        return dict(identity, backend='http', prefixes={'query': 'query: ', 'document': 'passage: '})

    def _target_source_supported(self):
        try:
            return text_endpoint(self.target_source) == self.target_source
        except ValueError:
            return False

    def _backup_matches(self, backup, rows):
        counts = backup.get('counts') if isinstance(backup, dict) else None
        return (isinstance(backup, dict) and backup.get('verified') is True and isinstance(counts, dict)
                and type(counts.get('session_embeddings')) is int and counts.get('session_embeddings') == rows and isinstance(counts.get('tables'), dict)
                and type(counts['tables'].get('smartDetectSessionsSearch')) is int
                and counts['tables']['smartDetectSessionsSearch'] >= rows)

    def _root_handoff(self):
        current, _, raw = self._record(self.root_profile, include_bytes=True)
        previous, _, previous_raw = self._record(self.previous_root, include_bytes=True)
        if previous is not None:
            if current not in (previous, self.target_profile):
                raise RebuildError('The E5 root/session profile handoff no longer matches this generation')
            return previous_raw
        if current is not None and current.get('profile') == self.profile_kind:
            if current != self._read(self.live_profile):
                raise RebuildError('The existing root and session encoder identities differ')
            _atomic_bytes(self.previous_root, raw)
            return raw
        # Hybrid Basic+Deep: the CLIP root profile is never replaced by E5.
        return None

    def _cutover(self, *, backup, key_quiesced, write_profile, source=None, readback_plan=None):
        _require_approval(readback=True, source=source, readback_plan=readback_plan)
        if self.state not in ('verified','awaiting_native_readback','cut_over'):
            raise RebuildError('The session generation is not ready for cutover')
        if not key_quiesced():
            raise RebuildError('Stop the Key query and indexing runtime before session cutover')
        if not self._backup_matches(backup, self.store.live_counts()['rows']):
            raise RebuildError('A verified backup matching the live session index is required')
        original_root = self._root_handoff()
        def profiles(path, value):
            write_profile(path, value)
            if original_root is not None:
                _atomic_json(self.root_profile, value)
        try:
            result = super()._cutover(backup=backup, key_quiesced=key_quiesced,
                write_profile=profiles, source=source, readback_plan=readback_plan)
            # Recovery after the child/vector swap but before the root profile.
            if original_root is not None and self._read(self.root_profile) != self.target_profile:
                _atomic_json(self.root_profile, self.target_profile)
            return result
        except Exception:
            if original_root is not None:
                try:
                    _atomic_bytes(self.root_profile, original_root)
                except Exception:
                    raise RebuildOutcomeUncertain('The root/session profile recovery needs inspection') from None
            raise

    async def _rollback(self, *, key_quiesced, previous_embedder, source):
        previous, _, raw = self._record(self.previous_root, include_bytes=True)
        if previous is not None and self._read(self.root_profile) not in (previous, self.target_profile):
            raise RebuildError('A later root profile change blocks automatic session-profile rollback')
        result = await super()._rollback(key_quiesced=key_quiesced,
                                         previous_embedder=previous_embedder, source=source)
        if previous is not None:
            try:
                _atomic_bytes(self.root_profile, raw)
            except Exception:
                raise RebuildOutcomeUncertain('Session vectors restored; root profile recovery needs inspection') from None
        return result
