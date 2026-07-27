import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from home_assist_agent.audit.recorder import AuditRecorderProtocol
from home_assist_agent.errors import DependencyError
from home_assist_agent.resolution.models import ActorContext
from home_assist_agent.resolution.normalize import normalize_term
from home_assist_agent.terms.models import (
    ResolutionAttempt,
    TermMapping,
    TermScope,
    TermStatus,
)


class TermConflictError(Exception):
    def __init__(self, existing: TermMapping) -> None:
        super().__init__("term_conflict")
        self.code = "term_conflict"
        self.existing = existing


class TermStateError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SQLiteTermStore:
    def __init__(
        self,
        database_path: Path | str,
        *,
        audit: AuditRecorderProtocol,
        provisional_seconds: int = 600,
    ) -> None:
        self._database_path = Path(database_path)
        self._audit = audit
        self._provisional_seconds = provisional_seconds

    async def create_provisional(
        self,
        *,
        actor: ActorContext,
        display_term: str,
        entity_ids: tuple[str, ...],
        source_message_id: str,
        source_candidate_id: str,
        catalog_version: str,
        now: datetime,
        evidence: dict[str, Any] | None = None,
    ) -> TermMapping:
        normalized = normalize_term(display_term)
        ordered_ids = self._ordered_entity_ids(entity_ids)
        existing = await asyncio.to_thread(
            self._find_current_term_sync,
            actor.home_id,
            actor.person_id,
            TermScope.PERSON,
            normalized,
        )
        if existing is not None:
            if existing.target_entity_ids != ordered_ids:
                raise TermConflictError(existing)
            return await self._append_revision(
                existing,
                status=existing.status,
                change_message_id=source_message_id,
                event_type="term.mapping_reused",
                now=now,
                source_candidate_id=source_candidate_id,
                catalog_version=catalog_version,
                evidence=evidence or {},
            )
        mapping = TermMapping(
            revision_id=uuid4().hex,
            revision=1,
            mapping_id=uuid4().hex,
            home_id=actor.home_id,
            scope=TermScope.PERSON,
            person_id=actor.person_id,
            display_term=display_term.strip(),
            normalized_term=normalized,
            target_entity_ids=ordered_ids,
            status=TermStatus.PROVISIONAL,
            source_message_id=source_message_id,
            source_candidate_id=source_candidate_id,
            catalog_version=catalog_version,
            evidence=evidence or {},
            created_at=now,
            promote_at=now + timedelta(seconds=self._provisional_seconds),
            updated_at=now,
        )
        return await self._insert_mapping(
            mapping,
            change_message_id=source_message_id,
            operation="create_provisional",
            event_type="term.provisional_created",
        )

    async def create_approved(
        self,
        *,
        actor: ActorContext,
        scope: TermScope,
        display_term: str,
        entity_ids: tuple[str, ...],
        source_message_id: str,
        source_candidate_id: str,
        catalog_version: str,
        now: datetime,
        evidence: dict[str, Any] | None = None,
        supersedes_mapping_id: str | None = None,
    ) -> TermMapping:
        normalized = normalize_term(display_term)
        person_id = actor.person_id if scope == TermScope.PERSON else None
        ordered_ids = self._ordered_entity_ids(entity_ids)
        existing = await asyncio.to_thread(
            self._find_current_term_sync,
            actor.home_id,
            person_id,
            scope,
            normalized,
        )
        if existing is not None:
            if existing.target_entity_ids != ordered_ids:
                raise TermConflictError(existing)
            return await self._append_revision(
                existing,
                status=TermStatus.APPROVED,
                change_message_id=source_message_id,
                event_type="term.mapping_reused",
                now=now,
                source_candidate_id=source_candidate_id,
                catalog_version=catalog_version,
                evidence=evidence or {},
            )
        mapping = TermMapping(
            revision_id=uuid4().hex,
            revision=1,
            mapping_id=uuid4().hex,
            home_id=actor.home_id,
            scope=scope,
            person_id=person_id,
            display_term=display_term.strip(),
            normalized_term=normalized,
            target_entity_ids=ordered_ids,
            status=TermStatus.APPROVED,
            source_message_id=source_message_id,
            source_candidate_id=source_candidate_id,
            catalog_version=catalog_version,
            evidence=evidence or {},
            created_at=now,
            updated_at=now,
            supersedes_mapping_id=supersedes_mapping_id,
        )
        return await self._insert_mapping(
            mapping,
            change_message_id=source_message_id,
            operation="create_approved",
            event_type="term.approved",
        )

    async def approve(
        self,
        mapping_id: str,
        message_id: str,
        *,
        now: datetime,
    ) -> TermMapping:
        existing = await self._require_current(mapping_id)
        if existing.status == TermStatus.APPROVED:
            return existing
        if existing.status != TermStatus.PROVISIONAL:
            raise TermStateError("term_not_provisional")
        return await self._append_revision(
            existing,
            status=TermStatus.APPROVED,
            change_message_id=message_id,
            event_type="term.approved",
            now=now,
        )

    async def reject(
        self,
        mapping_id: str,
        message_id: str,
        *,
        now: datetime,
    ) -> TermMapping:
        existing = await self._require_current(mapping_id)
        if existing.status == TermStatus.REJECTED:
            return existing
        if existing.status not in {
            TermStatus.PROVISIONAL,
            TermStatus.APPROVED,
        }:
            raise TermStateError("term_not_active")
        return await self._append_revision(
            existing,
            status=TermStatus.REJECTED,
            change_message_id=message_id,
            event_type="term.rejected",
            now=now,
        )

    async def supersede(
        self,
        mapping_id: str,
        message_id: str,
        *,
        now: datetime,
    ) -> TermMapping:
        existing = await self._require_current(mapping_id)
        if existing.status == TermStatus.SUPERSEDED:
            return existing
        return await self._append_revision(
            existing,
            status=TermStatus.SUPERSEDED,
            change_message_id=message_id,
            event_type="term.superseded",
            now=now,
        )

    async def visible_terms(
        self,
        actor: ActorContext,
        now: datetime,
    ) -> list[TermMapping]:
        del now
        mappings = await asyncio.to_thread(
            self._visible_terms_sync,
            actor.home_id,
            actor.person_id,
        )
        status_rank = {
            TermStatus.PROVISIONAL: 0,
            TermStatus.APPROVED: 1,
        }
        return sorted(
            mappings,
            key=lambda mapping: (
                mapping.normalized_term,
                0 if mapping.scope == TermScope.PERSON else 1,
                status_rank[mapping.status],
                mapping.created_at,
                mapping.mapping_id,
            ),
        )

    async def save_resolution_attempt(
        self,
        attempt: ResolutionAttempt,
        message_id: str,
    ) -> ResolutionAttempt:
        await self._record_write_request(
            message_id,
            "save_resolution_attempt",
            attempt.model_dump(mode="json"),
        )
        try:
            await asyncio.to_thread(
                self._save_resolution_attempt_sync,
                attempt,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            await self._record_write_failure(
                message_id,
                "save_resolution_attempt",
                error,
            )
            raise DependencyError(
                "term_store_unavailable",
                "术语存储写入失败。",
            ) from error
        await self._audit.record(
            message_id=message_id,
            event_type="resolution.attempt_saved",
            service="term_store",
            payload=attempt.model_dump(mode="json"),
        )
        return attempt

    async def load_resolution_attempt(
        self,
        actor: ActorContext,
        attempt_id: str,
        now: datetime,
    ) -> ResolutionAttempt | None:
        return await asyncio.to_thread(
            self._load_resolution_attempt_sync,
            actor,
            attempt_id,
            now,
        )

    async def resolution_attempt_count(self, attempt_id: str) -> int:
        return await asyncio.to_thread(
            self._resolution_attempt_count_sync,
            attempt_id,
        )

    async def revision_count(self, mapping_id: str) -> int:
        return await asyncio.to_thread(self._revision_count_sync, mapping_id)

    async def _require_current(self, mapping_id: str) -> TermMapping:
        mapping = await asyncio.to_thread(
            self._load_current_mapping_sync,
            mapping_id,
        )
        if mapping is None:
            raise TermStateError("term_not_found")
        return mapping

    async def _append_revision(
        self,
        existing: TermMapping,
        *,
        status: TermStatus,
        change_message_id: str,
        event_type: str,
        now: datetime,
        source_candidate_id: str | None = None,
        catalog_version: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> TermMapping:
        combined_evidence = dict(existing.evidence)
        combined_evidence.update(evidence or {})
        mapping = existing.model_copy(
            update={
                "revision_id": uuid4().hex,
                "revision": existing.revision + 1,
                "status": status,
                "source_candidate_id": (
                    source_candidate_id or existing.source_candidate_id
                ),
                "catalog_version": catalog_version or existing.catalog_version,
                "evidence": combined_evidence,
                "promote_at": (
                    existing.promote_at
                    if status == TermStatus.PROVISIONAL
                    else None
                ),
                "updated_at": now,
            }
        )
        return await self._insert_mapping(
            mapping,
            change_message_id=change_message_id,
            operation=event_type,
            event_type=event_type,
        )

    async def _insert_mapping(
        self,
        mapping: TermMapping,
        *,
        change_message_id: str,
        operation: str,
        event_type: str,
    ) -> TermMapping:
        await self._record_write_request(
            change_message_id,
            operation,
            mapping.model_dump(mode="json"),
        )
        try:
            await asyncio.to_thread(
                self._insert_mapping_sync,
                mapping,
                change_message_id,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            await self._record_write_failure(
                change_message_id,
                operation,
                error,
            )
            raise DependencyError(
                "term_store_unavailable",
                "术语存储写入失败。",
            ) from error
        await self._audit.record(
            message_id=change_message_id,
            event_type=event_type,
            service="term_store",
            payload=mapping.model_dump(mode="json"),
        )
        return mapping

    async def _record_write_request(
        self,
        message_id: str,
        operation: str,
        payload: Any,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="term.write.request",
            service="term_store",
            payload={"operation": operation, "change": payload},
        )

    async def _record_write_failure(
        self,
        message_id: str,
        operation: str,
        error: Exception,
    ) -> None:
        await self._audit.record(
            message_id=message_id,
            event_type="term.write.failed",
            service="term_store",
            payload={"operation": operation, "error": str(error)},
            status="error",
            error_code="term_store_unavailable",
        )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        self._database_path.chmod(0o600)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS term_mapping_revisions (
                revision_id TEXT PRIMARY KEY,
                mapping_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                home_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                person_id TEXT,
                display_term TEXT NOT NULL,
                normalized_term TEXT NOT NULL,
                target_entity_ids_json TEXT NOT NULL,
                status TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                source_candidate_id TEXT NOT NULL,
                catalog_version TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promote_at TEXT,
                updated_at TEXT NOT NULL,
                supersedes_mapping_id TEXT,
                change_message_id TEXT NOT NULL,
                UNIQUE(mapping_id, revision)
            );

            CREATE INDEX IF NOT EXISTS idx_term_current
            ON term_mapping_revisions(
                home_id, scope, person_id, normalized_term, mapping_id, revision
            );

            CREATE TABLE IF NOT EXISTS resolution_attempts (
                attempt_id TEXT PRIMARY KEY,
                home_id TEXT NOT NULL,
                person_id TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS term_revisions_no_update
            BEFORE UPDATE ON term_mapping_revisions
            BEGIN
                SELECT RAISE(ABORT, 'term revisions are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS term_revisions_no_delete
            BEFORE DELETE ON term_mapping_revisions
            BEGIN
                SELECT RAISE(ABORT, 'term revisions are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS resolution_attempts_no_update
            BEFORE UPDATE ON resolution_attempts
            BEGIN
                SELECT RAISE(ABORT, 'resolution attempts are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS resolution_attempts_no_delete
            BEFORE DELETE ON resolution_attempts
            BEGIN
                SELECT RAISE(ABORT, 'resolution attempts are append-only');
            END;
            """
        )

    def _insert_mapping_sync(
        self,
        mapping: TermMapping,
        change_message_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO term_mapping_revisions (
                    revision_id, mapping_id, revision, home_id, scope,
                    person_id, display_term, normalized_term,
                    target_entity_ids_json, status, source_message_id,
                    source_candidate_id, catalog_version, evidence_json,
                    created_at, promote_at, updated_at,
                    supersedes_mapping_id, change_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mapping.revision_id,
                    mapping.mapping_id,
                    mapping.revision,
                    mapping.home_id,
                    mapping.scope.value,
                    mapping.person_id,
                    mapping.display_term,
                    mapping.normalized_term,
                    json.dumps(mapping.target_entity_ids, separators=(",", ":")),
                    mapping.status.value,
                    mapping.source_message_id,
                    mapping.source_candidate_id,
                    mapping.catalog_version,
                    json.dumps(
                        mapping.evidence,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    mapping.created_at.isoformat(),
                    (
                        mapping.promote_at.isoformat()
                        if mapping.promote_at is not None
                        else None
                    ),
                    mapping.updated_at.isoformat(),
                    mapping.supersedes_mapping_id,
                    change_message_id,
                ),
            )

    def _find_current_term_sync(
        self,
        home_id: str,
        person_id: str | None,
        scope: TermScope,
        normalized_term: str,
    ) -> TermMapping | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT current.*
                FROM term_mapping_revisions AS current
                JOIN (
                    SELECT mapping_id, MAX(revision) AS revision
                    FROM term_mapping_revisions
                    GROUP BY mapping_id
                ) AS latest
                  ON latest.mapping_id = current.mapping_id
                 AND latest.revision = current.revision
                WHERE current.home_id = ?
                  AND current.scope = ?
                  AND current.person_id IS ?
                  AND current.normalized_term = ?
                  AND current.status IN ('provisional', 'approved')
                ORDER BY current.created_at DESC
                LIMIT 1
                """,
                (home_id, scope.value, person_id, normalized_term),
            ).fetchone()
        return self._row_to_mapping(row) if row is not None else None

    def _load_current_mapping_sync(
        self,
        mapping_id: str,
    ) -> TermMapping | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM term_mapping_revisions
                WHERE mapping_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (mapping_id,),
            ).fetchone()
        return self._row_to_mapping(row) if row is not None else None

    def _visible_terms_sync(
        self,
        home_id: str,
        person_id: str,
    ) -> list[TermMapping]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT current.*
                FROM term_mapping_revisions AS current
                JOIN (
                    SELECT mapping_id, MAX(revision) AS revision
                    FROM term_mapping_revisions
                    GROUP BY mapping_id
                ) AS latest
                  ON latest.mapping_id = current.mapping_id
                 AND latest.revision = current.revision
                WHERE current.home_id = ?
                  AND current.status IN ('provisional', 'approved')
                  AND (
                    (current.scope = 'person' AND current.person_id = ?)
                    OR
                    (current.scope = 'home' AND current.person_id IS NULL)
                  )
                """,
                (home_id, person_id),
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def _revision_count_sync(self, mapping_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM term_mapping_revisions
                WHERE mapping_id = ?
                """,
                (mapping_id,),
            ).fetchone()
        return int(row[0])

    def _save_resolution_attempt_sync(
        self,
        attempt: ResolutionAttempt,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO resolution_attempts (
                    attempt_id, home_id, person_id, source_message_id,
                    expires_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.home_id,
                    attempt.person_id,
                    attempt.source_message_id,
                    attempt.expires_at.isoformat(),
                    attempt.model_dump_json(),
                ),
            )

    def _load_resolution_attempt_sync(
        self,
        actor: ActorContext,
        attempt_id: str,
        now: datetime,
    ) -> ResolutionAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM resolution_attempts
                WHERE attempt_id = ?
                  AND home_id = ?
                  AND person_id = ?
                  AND expires_at > ?
                """,
                (
                    attempt_id,
                    actor.home_id,
                    actor.person_id,
                    now.isoformat(),
                ),
            ).fetchone()
        if row is None:
            return None
        return ResolutionAttempt.model_validate_json(row["payload_json"])

    def _resolution_attempt_count_sync(self, attempt_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM resolution_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _ordered_entity_ids(entity_ids: tuple[str, ...]) -> tuple[str, ...]:
        ordered = tuple(sorted(set(entity_ids)))
        if not ordered or len(ordered) > 20:
            raise ValueError("term target must contain 1 to 20 entities")
        return ordered

    @staticmethod
    def _row_to_mapping(row: sqlite3.Row) -> TermMapping:
        return TermMapping(
            revision_id=row["revision_id"],
            revision=row["revision"],
            mapping_id=row["mapping_id"],
            home_id=row["home_id"],
            scope=row["scope"],
            person_id=row["person_id"],
            display_term=row["display_term"],
            normalized_term=row["normalized_term"],
            target_entity_ids=tuple(json.loads(row["target_entity_ids_json"])),
            status=row["status"],
            source_message_id=row["source_message_id"],
            source_candidate_id=row["source_candidate_id"],
            catalog_version=row["catalog_version"],
            evidence=json.loads(row["evidence_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            promote_at=(
                datetime.fromisoformat(row["promote_at"])
                if row["promote_at"] is not None
                else None
            ),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            supersedes_mapping_id=row["supersedes_mapping_id"],
        )
