"""E5 session store: isolated 384-value stages/snapshots, no visual-table writes."""
import hashlib
import json

from .index_rebuild import PgStore, RebuildError, _check_vector


class SessionPgStore(PgStore):
    SCHEMA = 'aikey_session_rebuild'

    def prepare(self, tag, revision):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS {self.SCHEMA}')
            conn.execute(f'CREATE TABLE IF NOT EXISTS {stage} (object_id varchar PRIMARY KEY, '
                'revision char(64) NOT NULL, status varchar NOT NULL '
                "CHECK (status IN ('embedded','no_source')), embedding vector(384), "
                'updated_at timestamptz NOT NULL DEFAULT now(), '
                "CHECK ((status='embedded')=(embedding IS NOT NULL)))")

    def uncovered(self, tag, limit):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            rows = conn.execute(f'SELECT d."sessionId" FROM public."smartDetectSessionsSearch" d '
                f'LEFT JOIN {stage} s ON s.object_id=d."sessionId" '
                'WHERE d."descEmbedding" IS NOT NULL AND s.object_id IS NULL '
                'ORDER BY d."sessionId" LIMIT %s', (limit,)).fetchall()
        return [r[0] for r in rows]

    def staged_summary(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            embedded, missing, revisions, bad = conn.execute(
                f"SELECT count(*) FILTER(WHERE status='embedded'), count(*) FILTER(WHERE status='no_source'),"
                "coalesce(array_agg(DISTINCT revision),'{}'),count(*) FILTER(WHERE status='embedded' "
                f'AND (vector_dims(embedding)<>384 OR (embedding <#> embedding)>=0)) FROM {stage}').fetchone()
            (uncovered,) = conn.execute('SELECT count(*) FROM public."smartDetectSessionsSearch" d '
                f'LEFT JOIN {stage} s ON s.object_id=d."sessionId" '
                'WHERE d."descEmbedding" IS NOT NULL AND s.object_id IS NULL').fetchone()
        return {'embedded': embedded, 'no_source': missing, 'revisions': sorted(x.strip() for x in revisions),
                'bad_vectors': bad, 'uncovered': uncovered}

    def live_counts(self):
        with self.connect() as conn:
            (rows,) = conn.execute('SELECT count(*) FROM public."smartDetectSessionsSearch" '
                                   'WHERE "descEmbedding" IS NOT NULL').fetchone()
        return {'rows': rows}

    def source_rows(self, *, limit=100_000):
        if type(limit) is not int or not 1 <= limit <= 100_000:
            raise RebuildError('The session source row budget is invalid')
        with self.connect() as conn:
            rows = conn.execute('SELECT "sessionId",description,"cameraIds","firstSeen","lastSeen" '
                'FROM public."smartDetectSessionsSearch" WHERE "descEmbedding" IS NOT NULL '
                'ORDER BY "sessionId" LIMIT %s', (limit+1,)).fetchall()
        if len(rows) > limit:
            raise RebuildError('The complete session projection exceeds its source row budget')
        return [dict(zip(('session_id','description','camera_ids','first_seen','last_seen'), row)) for row in rows]

    def source_association(self, session_id):
        with self.connect() as conn:
            rows = conn.execute('SELECT description,"cameraIds","firstSeen","lastSeen" '
                'FROM public."smartDetectSessionsSearch" WHERE "sessionId"=%s AND "descEmbedding" IS NOT NULL',
                (session_id,)).fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            return None
        text, cameras, first, last = rows[0]
        return hashlib.sha256(text.encode()).hexdigest(), tuple(sorted(cameras)), first, last

    def source_identity(self):
        with self.connect() as conn:
            (digest,) = conn.execute("""
                SELECT encode(sha256(convert_to(coalesce(string_agg(
                  encode(sha256(convert_to(concat_ws(chr(31),"sessionId",description,"cameraIds"::text,
                    "firstSeen"::text,"lastSeen"::text),'UTF8')),'hex'),'' ORDER BY "sessionId"),''),
                  'UTF8')),'hex') FROM public."smartDetectSessionsSearch" WHERE "descEmbedding" IS NOT NULL
                """).fetchone()
        return digest

    def staged_identity(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            (digest,) = conn.execute(
                "SELECT encode(sha256(convert_to(coalesce(string_agg("
                "encode(sha256(convert_to(concat_ws(chr(31),s.object_id,s.revision,s.status,s.embedding::text,"
                'd."sessionId",d.description,d."cameraIds"::text,d."firstSeen"::text,d."lastSeen"::text),'
                "'UTF8')),'hex'),'' ORDER BY coalesce(s.object_id,d.\"sessionId\")),''),'UTF8')),'hex') "
                f'FROM {stage} s FULL OUTER JOIN '
                '(SELECT * FROM public."smartDetectSessionsSearch" WHERE "descEmbedding" IS NOT NULL) d '
                'ON d."sessionId"=s.object_id').fetchone()
        return digest

    def rank_sessions(self, tag, vector, *, k, min_cosine, camera_ids, start_ms, end_ms):
        stage, _ = self._names(tag)
        values = _check_vector(vector, 384)
        if type(k) is not int or not 1 <= k <= 10:
            raise RebuildError('The session rank bound is invalid')
        with self.connect() as conn:
            rows = conn.execute(f'SELECT s.object_id,d."cameraIds",d."firstSeen",d."lastSeen",'
                "encode(sha256(convert_to(d.description,'UTF8')),'hex'),"
                f'1-(s.embedding <=> %s::vector) AS similarity FROM {stage} s '
                'JOIN public."smartDetectSessionsSearch" d ON d."sessionId"=s.object_id '
                "WHERE s.status='embedded' AND (cardinality(%s::varchar[])=0 OR d.\"cameraIds\" && %s::varchar[]) "
                'AND (%s::bigint IS NULL OR (d."firstSeen" < %s AND d."lastSeen" >= %s)) '
                'AND 1-(s.embedding <=> %s::vector) >= %s ORDER BY similarity DESC,s.object_id LIMIT %s',
                (json.dumps(values),camera_ids,camera_ids,start_ms,end_ms,start_ms,
                 json.dumps(values),min_cosine,k)).fetchall()
        return [dict(zip(('session_id','camera_ids','first_seen','last_seen','description_sha256','similarity'), r))
                for r in rows]

    def cutover(self, tag, revision, *, expected_source_identity=None, expected_staged_identity=None):
        stage, previous = self._names(tag)
        with self.connect() as conn, conn.transaction():
            conn.execute('LOCK TABLE public."smartDetectSessionsSearch" IN SHARE ROW EXCLUSIVE MODE')
            conn.execute(f'LOCK TABLE {stage} IN SHARE ROW EXCLUSIVE MODE')
            if ((expected_source_identity is not None and self.source_identity() != expected_source_identity)
                    or (expected_staged_identity is not None
                        and self.staged_identity(tag) != expected_staged_identity)):
                raise RebuildError("The frozen session source or qualified stage changed before the atomic swap")
            (missing,) = conn.execute('SELECT count(*) FROM public."smartDetectSessionsSearch" d '
                f'LEFT JOIN {stage} s ON s.object_id=d."sessionId" AND s.status=\'embedded\' AND s.revision=%s '
                'WHERE d."descEmbedding" IS NOT NULL AND s.object_id IS NULL', (revision,)).fetchone()
            if missing:
                raise RebuildError('Session coverage changed before cutover; nothing was swapped')
            conn.execute(f'DROP TABLE IF EXISTS {previous}')
            conn.execute(f'CREATE TABLE {previous} AS SELECT "sessionId" AS object_id,"descEmbedding" AS embedding,description,"cameraIds","firstSeen","lastSeen" '
                         'FROM public."smartDetectSessionsSearch" WHERE "descEmbedding" IS NOT NULL')
            conn.execute(f'ALTER TABLE {previous} ADD PRIMARY KEY(object_id)')
            count = conn.execute('UPDATE public."smartDetectSessionsSearch" d SET "descEmbedding"=s.embedding '
                f'FROM {stage} s WHERE s.object_id=d."sessionId" AND d."descEmbedding" IS NOT NULL').rowcount
            (total,) = conn.execute('SELECT count(*) FROM public."smartDetectSessionsSearch" '
                                   'WHERE "descEmbedding" IS NOT NULL').fetchone()
            if count != total:
                raise RebuildError('Session swap count changed; the transaction rolled back')
        return count

    def rows_without_snapshot(self, tag):
        _, previous = self._names(tag)
        with self.connect() as conn:
            rows = conn.execute('SELECT d."sessionId" FROM public."smartDetectSessionsSearch" d '
                f'LEFT JOIN {previous} p ON p.object_id=d."sessionId" '
                'WHERE d."descEmbedding" IS NOT NULL AND (p.object_id IS NULL OR '
                '(d.description,d."cameraIds",d."firstSeen",d."lastSeen") IS DISTINCT FROM '
                '(p.description,p."cameraIds",p."firstSeen",p."lastSeen")) ORDER BY d."sessionId"').fetchall()
        return [r[0] for r in rows]

    def restore(self, tag, replacements):
        _, previous = self._names(tag)
        with self.connect() as conn, conn.transaction():
            conn.execute('LOCK TABLE public."smartDetectSessionsSearch" IN SHARE ROW EXCLUSIVE MODE')
            count = conn.execute('UPDATE public."smartDetectSessionsSearch" d SET "descEmbedding"=p.embedding '
                                  f'FROM {previous} p WHERE p.object_id=d."sessionId" AND d."descEmbedding" IS NOT NULL '
                                  'AND (d.description,d."cameraIds",d."firstSeen",d."lastSeen") IS NOT DISTINCT FROM '
                                  '(p.description,p."cameraIds",p."firstSeen",p."lastSeen")').rowcount
            for session_id, vector in replacements.items():
                count += conn.execute('UPDATE public."smartDetectSessionsSearch" SET "descEmbedding"=%s::vector '
                    'WHERE "sessionId"=%s', (json.dumps(_check_vector(vector,384)),session_id)).rowcount
            (total,) = conn.execute('SELECT count(*) FROM public."smartDetectSessionsSearch" '
                                   'WHERE "descEmbedding" IS NOT NULL').fetchone()
            if count != total:
                raise RebuildError('Session restore coverage changed; transaction rolled back')
        return count

    def swapped(self, tag):
        stage, _ = self._names(tag)
        with self.connect() as conn:
            same, total = conn.execute('SELECT count(s.object_id) FILTER(WHERE d."descEmbedding"=s.embedding),'
                'count(*) FROM public."smartDetectSessionsSearch" d '
                f'LEFT JOIN {stage} s ON s.object_id=d."sessionId" WHERE d."descEmbedding" IS NOT NULL').fetchone()
        return same == total
