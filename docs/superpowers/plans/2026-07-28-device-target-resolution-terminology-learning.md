# Device Target Resolution and Terminology Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace natural-language HA target passthrough with audited deterministic candidates, Codex candidate selection, deterministic verification, and execution-success-driven personal terminology learning.

**Architecture:** Build a `resolution` package that owns HA catalog snapshots, candidate generation, Codex selection contracts, and verification. Feed only `VerifiedTarget` into `DeviceExecutor`, then send successful executions to a separate `terms` package that persists provisional mappings, processes corrections, and promotes them after 600 seconds. Keep HA catalog reads, Codex calls, MCP calls, term state changes, and system promotion triggers on the shared append-only audit chain.

**Tech Stack:** Python 3.11+, Pydantic 2, FastAPI, httpx, websockets 16, MCP 1.x, SQLite WAL, pytest, pytest-asyncio, jsonschema.

## Global Constraints

- Every user message and system trigger has a unique `message_id`; `request_id` always equals `message_id`.
- All user input/output, Codex prompt/parameters/stdout/stderr/result/error, HA catalog request/response, MCP request/response, verification, and terminology changes use the shared `AuditRecorder`.
- Authorization, Token, API Key, Cookie, passwords, and client secrets are redacted before persistence.
- An external side effect is blocked when its request audit cannot be written.
- Codex may return only opaque candidate IDs, never a free-form `entity_id`.
- Target resolution does not calculate risk levels; it exposes a `RiskPolicyPort` boundary only.
- Personal terminology is learned without prompting only after full execution success.
- Provisional terminology promotes after exactly 600 seconds without correction.
- Household terminology requires an explicit “全家都这么叫” request and a separate confirmation.
- No production code is written before its corresponding test has been observed failing for the expected reason.

---

## File Structure

### New production files

- `src/home_assist_agent/resolution/__init__.py`: package exports.
- `src/home_assist_agent/resolution/models.py`: actor, catalog, intent, candidate, resolution, verification, and clarification contracts.
- `src/home_assist_agent/resolution/normalize.py`: Unicode and token normalization.
- `src/home_assist_agent/resolution/candidates.py`: deterministic candidate generation and ranking.
- `src/home_assist_agent/resolution/verifier.py`: refreshed-catalog validation and one-retry stale handling.
- `src/home_assist_agent/ha/catalog.py`: audited HA REST/WebSocket catalog adapter.
- `src/home_assist_agent/codex/schemas/target_resolution.json`: candidate-only Codex output schema.
- `src/home_assist_agent/terms/__init__.py`: package exports.
- `src/home_assist_agent/terms/models.py`: terminology, feedback, and promotion contracts.
- `src/home_assist_agent/terms/store.py`: SQLite terminology and clarification storage.
- `src/home_assist_agent/terms/service.py`: provisional learning, correction, and household promotion behavior.
- `src/home_assist_agent/terms/worker.py`: idempotent 600-second promotion worker.

### Modified production files

- `src/home_assist_agent/settings.py`: HA base URL, actor identity, term DB, confidence, limit, timeout, and feature flag.
- `src/home_assist_agent/commands/models.py`: route target expression, `needs_input`, resolution details, warnings, and plural tool calls.
- `src/home_assist_agent/codex/gateway.py`: `resolve_target` method and target-free indirect plan prompt.
- `src/home_assist_agent/codex/schemas/route_decision.json`: target expression for every IoT route.
- `src/home_assist_agent/codex/schemas/device_plan.json`: non-target parameters only.
- `src/home_assist_agent/commands/service.py`: unified resolution pipeline, clarification, and learning handoff.
- `src/home_assist_agent/devices/executor.py`: verified target injection and deterministic set fan-out.
- `src/home_assist_agent/channels/message.py`: trusted `ActorContext`, feedback routing, and audit linkage.
- `src/home_assist_agent/api/models.py`: compatible request and response surface.
- `src/home_assist_agent/bootstrap.py`: wire catalog, resolution, terms, and worker services.
- `src/home_assist_agent/main.py`: worker application lifecycle.
- `pyproject.toml`: explicit `websockets>=16,<17` dependency.
- `.env.example`: new non-secret configuration keys.
- `README.md`: local configuration and behavior.

### New test files

- `tests/test_resolution_candidates.py`
- `tests/test_ha_catalog.py`
- `tests/test_term_store.py`
- `tests/test_target_resolution_gateway.py`
- `tests/test_resolution_verifier.py`
- `tests/test_verified_device_executor.py`
- `tests/test_target_resolution_service.py`
- `tests/test_term_learning_service.py`
- `tests/test_term_promotion_worker.py`
- `tests/test_target_resolution_audit.py`

---

### Task 1: Resolution Contracts and Deterministic Candidate Builder

**Files:**
- Create: `src/home_assist_agent/resolution/__init__.py`
- Create: `src/home_assist_agent/resolution/models.py`
- Create: `src/home_assist_agent/resolution/normalize.py`
- Create: `src/home_assist_agent/resolution/candidates.py`
- Test: `tests/test_resolution_candidates.py`

**Interfaces:**
- Produces: `ActorContext`, `DeviceActionIntent`, `HaEntitySnapshot`, `CatalogSnapshot`, `TargetCandidate`, `TargetResolutionDecision`, `VerifiedTarget`, `ClarificationChoice`, and the pass-through `RiskPolicyPort` protocol.
- Produces: `normalize_term(value: str) -> str`.
- Produces: `CandidateBuilder.build(intent, actor, catalog, terms, context_terms=()) -> list[TargetCandidate]`.

- [ ] **Step 1: Write failing normalization and candidate tests**

```python
def test_personal_provisional_precedes_ha_alias_for_same_expression() -> None:
    candidates = CandidateBuilder(limit=20).build(
        intent=DeviceActionIntent(action="turn_on", target_expression=" 床头灯 "),
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        catalog=catalog_with(
            entity("light.left", friendly_name="左侧台灯", aliases=["床头灯"]),
            entity("light.right", friendly_name="右侧台灯", aliases=["床头灯"]),
        ),
        terms=[
            visible_term(
                term="床头灯",
                entity_ids=["light.right"],
                status="provisional",
            )
        ],
    )

    assert candidates[0].target_entity_ids == ("light.right",)
    assert candidates[0].sources[0] == "personal_provisional"
    assert len(candidates) == 2
```

Add separate tests for NFKC normalization, action capability filtering, duplicate entity-set merging, personal-approved/shared/HA precedence, limit 20, set size 20, and unavailable entities.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_resolution_candidates.py -q`

Expected: collection fails because `home_assist_agent.resolution` does not exist.

- [ ] **Step 3: Implement minimal contracts and candidate rules**

```python
class CandidateBuilder:
    def __init__(self, limit: int = 20, target_limit: int = 20) -> None: ...

    def build(
        self,
        *,
        intent: DeviceActionIntent,
        actor: ActorContext,
        catalog: CatalogSnapshot,
        terms: Sequence[VisibleTermMapping],
        context_terms: Sequence[str] = (),
    ) -> list[TargetCandidate]: ...
```

Use immutable Pydantic models, sorted tuples for target IDs, deterministic source weights, and attempt-local opaque IDs (`cand_01`, `cand_02`, ...) assigned only after stable candidate ordering. Entity IDs remain internal candidate data and are never accepted from Codex output.

- [ ] **Step 4: Run candidate tests and full regression suite**

Run: `.venv/bin/pytest tests/test_resolution_candidates.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: existing tests remain green.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/resolution tests/test_resolution_candidates.py
git commit -m "feat: add deterministic target candidates"
```

---

### Task 2: Audited Home Assistant Entity Catalog

**Files:**
- Create: `src/home_assist_agent/ha/catalog.py`
- Modify: `src/home_assist_agent/settings.py`
- Modify: `pyproject.toml`
- Test: `tests/test_ha_catalog.py`

**Interfaces:**
- Consumes: `ActorContext`, `CatalogSnapshot`, `HaEntitySnapshot`.
- Produces: `HomeAssistantCatalogClient.snapshot(actor, message_id, correlation_id=None, causation_id=None) -> CatalogSnapshot`.
- Produces protocol: `HomeAssistantCatalogProvider`.

- [ ] **Step 1: Write failing adapter merge and audit tests**

```python
@pytest.mark.asyncio
async def test_catalog_merges_states_and_registries_without_auditing_token() -> None:
    audit = InMemoryAuditRecorder()
    client = HomeAssistantCatalogClient(
        base_url="http://ha.local:8123",
        token="top-secret",
        http_transport=fake_states_transport(),
        websocket_factory=fake_registry_socket(),
        audit=audit,
    )

    snapshot = await client.snapshot(
        ActorContext(home_id="home-1", person_id="person-1"),
        "message-1",
    )

    assert snapshot.entities[0].entity_id == "light.bedroom_left"
    assert snapshot.entities[0].aliases == ("床头灯",)
    assert snapshot.entities[0].area_name == "卧室"
    assert "top-secret" not in json.dumps(
        [event.payload for event in audit.events],
        ensure_ascii=False,
    )
```

Add tests for 401, timeout, malformed registry response, disabled entities, state-only changes preserving `catalog_version`, identity changes changing it, and complete request/response audit ordering.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_ha_catalog.py -q`

Expected: FAIL because `HomeAssistantCatalogClient` is missing.

- [ ] **Step 3: Implement the minimal REST/WebSocket client**

Use `httpx.AsyncClient` for `/api/states` and `websockets.asyncio.client.connect` for:

```text
config/entity_registry/list
config/device_registry/list
config/area_registry/list
```

Record `external.request` before each outbound read and `external.response` with the full business response or mapped error. Never include request Authorization headers in the audit payload.

- [ ] **Step 4: Run tests and regression suite**

Run: `.venv/bin/pytest tests/test_ha_catalog.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/home_assist_agent/settings.py src/home_assist_agent/ha/catalog.py tests/test_ha_catalog.py
git commit -m "feat: add audited Home Assistant entity catalog"
```

---

### Task 3: Personal Terminology and Clarification Store

**Files:**
- Create: `src/home_assist_agent/terms/__init__.py`
- Create: `src/home_assist_agent/terms/models.py`
- Create: `src/home_assist_agent/terms/store.py`
- Test: `tests/test_term_store.py`

**Interfaces:**
- Produces: `TermMapping`, `TermStatus`, `TermScope`, `ResolutionAttempt`, `HomePromotionRequest`.
- Produces: `SQLiteTermStore.visible_terms(actor, now)`.
- Produces: `create_provisional`, `approve`, `reject`, `supersede`, `save_resolution_attempt`, `load_resolution_attempt`, and promotion request methods.

- [ ] **Step 1: Write failing append-revision and visibility tests**

```python
@pytest.mark.asyncio
async def test_personal_term_visibility_prefers_current_person_and_keeps_history(
    tmp_path: Path,
) -> None:
    store = SQLiteTermStore(tmp_path / "terms.db", audit=InMemoryAuditRecorder())
    first = await store.create_provisional(
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        display_term="床头灯",
        entity_ids=("light.left",),
        source_message_id="message-1",
        source_candidate_id="cand-1",
        catalog_version="v1",
        now=NOW,
    )
    await store.approve(first.mapping_id, "message-promote", now=NOW_PLUS_10)

    visible = await store.visible_terms(
        ActorContext(home_id="home-1", person_id="person-1"),
        NOW_PLUS_10,
    )

    assert visible[0].status == TermStatus.APPROVED
    assert await store.revision_count(first.mapping_id) == 2
```

Add tests for cross-person isolation, household visibility, personal precedence, 0600 file permissions, expired clarification attempts, no deletion of revisions, same-target reuse, and conflicting-approved mappings not being overwritten.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_term_store.py -q`

Expected: FAIL because the terms package is missing.

- [ ] **Step 3: Implement SQLite WAL store with audit-first mutations**

Every mutation records `term.write.request`, writes a new immutable revision in a transaction, and records the specific success or failure event. The current view is derived from the latest revision; no history row is updated or deleted.

- [ ] **Step 4: Run store tests and regression suite**

Run: `.venv/bin/pytest tests/test_term_store.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/terms tests/test_term_store.py
git commit -m "feat: add personal terminology store"
```

---

### Task 4: Candidate-Only Codex Target Resolution

**Files:**
- Create: `src/home_assist_agent/codex/schemas/target_resolution.json`
- Modify: `src/home_assist_agent/codex/gateway.py`
- Modify: `src/home_assist_agent/commands/models.py`
- Modify: `src/home_assist_agent/codex/schemas/route_decision.json`
- Modify: `src/home_assist_agent/codex/schemas/device_plan.json`
- Test: `tests/test_target_resolution_gateway.py`
- Modify: `tests/test_codex_gateway.py`

**Interfaces:**
- Consumes: `DeviceActionIntent`, `TargetCandidate`.
- Produces: `CodexGateway.resolve_target(...) -> TargetResolutionDecision`.
- Produces route contract: every IoT route has `target_expression`.

- [ ] **Step 1: Write failing Schema and prompt tests**

```python
@pytest.mark.asyncio
async def test_target_resolution_prompt_allows_only_candidate_ids() -> None:
    runner = FakeRunner(
        output={
            "status": "selected",
            "selected_candidate_id": "cand_01",
            "confidence": 0.93,
            "alternative_candidate_ids": [],
            "reason": "与个人术语一致",
        }
    )
    gateway = CodexGateway(runner=runner, audit=InMemoryAuditRecorder())

    result = await gateway.resolve_target(
        utterance="打开床头灯",
        action_intent=DeviceActionIntent(
            action="turn_on",
            target_expression="床头灯",
        ),
        candidates=[candidate("cand_01", "light.bedroom_left")],
        message_id="message-1",
    )

    assert result.selected_candidate_id == "cand_01"
    assert '"candidate_id":"cand_01"' in runner.stdin
    assert '"entity_id"' not in TARGET_RESOLUTION_SCHEMA["properties"]
```

Add tests for invalid candidate ID, illegal `entity_id` output, ambiguous/no-match consistency, audit of stdout/stderr/error, and target fields rejected from indirect device plans.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_target_resolution_gateway.py -q`

Expected: FAIL because `resolve_target` and the schema do not exist.

- [ ] **Step 3: Implement the schema, method, and route contract**

Use `purpose="target_resolution"` and fixed `reasoning="medium"`. Candidate IDs are opaque; the Pydantic validator rejects any selected or alternative ID absent from the supplied candidate set.

- [ ] **Step 4: Run Codex tests and regression suite**

Run: `.venv/bin/pytest tests/test_target_resolution_gateway.py tests/test_codex_gateway.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/codex src/home_assist_agent/commands/models.py tests/test_target_resolution_gateway.py tests/test_codex_gateway.py
git commit -m "feat: constrain Codex target resolution to candidates"
```

---

### Task 5: Deterministic Resolution Verifier

**Files:**
- Create: `src/home_assist_agent/resolution/verifier.py`
- Test: `tests/test_resolution_verifier.py`

**Interfaces:**
- Consumes: `TargetResolutionDecision`, candidate snapshot, `ActorContext`, action intent, and `HomeAssistantCatalogProvider`.
- Produces: `ResolutionVerifier.verify(...) -> VerifiedTarget`.
- Raises: `ResolutionError(code, message, retryable)`.

- [ ] **Step 1: Write failing validation tests**

```python
@pytest.mark.asyncio
async def test_candidate_outside_current_home_never_verifies() -> None:
    verifier = ResolutionVerifier(
        catalog=FakeCatalog(snapshot_for("other-home")),
        audit=InMemoryAuditRecorder(),
        confidence_threshold=0.80,
    )

    with pytest.raises(ResolutionError) as error:
        await verifier.verify(
            decision=selected("cand_01", confidence=0.96),
            candidates=[candidate("cand_01", "light.left", home_id="home-1")],
            actor=ActorContext(home_id="home-1", person_id="person-1"),
            intent=turn_on("床头灯"),
            message_id="message-1",
        )

    assert error.value.code == "target_outside_home"
```

Add tests for low confidence, unknown candidate ID, deleted entity, disabled/unavailable entity, unsupported brightness, set size, refreshed catalog version, exactly one stale retry request, and verification audit events.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_resolution_verifier.py -q`

Expected: FAIL because the verifier is missing.

- [ ] **Step 3: Implement minimal fail-closed verification**

The verifier ignores `reason`, looks up entity IDs only from the selected candidate, refreshes the catalog, and emits `target.verification_succeeded` or `target.verification_failed`.

- [ ] **Step 4: Run verifier tests and regression suite**

Run: `.venv/bin/pytest tests/test_resolution_verifier.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/resolution/verifier.py tests/test_resolution_verifier.py
git commit -m "feat: verify resolved Home Assistant targets"
```

---

### Task 6: Verified Target Device Execution

**Files:**
- Modify: `src/home_assist_agent/devices/executor.py`
- Modify: `src/home_assist_agent/commands/models.py`
- Test: `tests/test_verified_device_executor.py`
- Modify: `tests/test_command_service.py`

**Interfaces:**
- Consumes: `VerifiedTarget`, `DeviceActionIntent`.
- Produces: `DeviceExecutionBatch(completed, failed, skipped, tool_calls)`.
- Produces: `DeviceExecutor.execute_verified(...)`.

- [ ] **Step 1: Write failing exact-ID and fail-fast tests**

```python
@pytest.mark.asyncio
async def test_verified_execution_uses_entity_id_not_original_term() -> None:
    mcp = RecordingMcp()
    executor = DeviceExecutor(mcp)

    result = await executor.execute_verified(
        intent=turn_on("床头灯"),
        target=verified("light.bedroom_left"),
        message_id="message-1",
    )

    assert result.tool_calls[0].arguments == {
        "name": "light.bedroom_left",
    }
    assert "床头灯" not in json.dumps(
        result.tool_calls[0].arguments,
        ensure_ascii=False,
    )
```

Add tests for deterministic entity ordering, brightness injection, removal of planner target fields, fail-fast completed/failed/skipped output, no learning eligibility on partial success, and zero MCP calls when request audit fails.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_verified_device_executor.py -q`

Expected: FAIL because `execute_verified` is missing.

- [ ] **Step 3: Implement minimal verified execution and compatibility response**

Keep existing `tool_call` for a one-entity batch and populate plural `tool_calls` for every batch. Do not delete the old method until the orchestrator has switched, but make it unreachable when `TARGET_RESOLUTION_ENABLED=true`.

- [ ] **Step 4: Run executor tests and regression suite**

Run: `.venv/bin/pytest tests/test_verified_device_executor.py tests/test_command_service.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/devices/executor.py src/home_assist_agent/commands/models.py tests/test_verified_device_executor.py tests/test_command_service.py
git commit -m "feat: execute only verified entity targets"
```

---

### Task 7: Unified Resolution Orchestration and Clarification

**Files:**
- Modify: `src/home_assist_agent/commands/service.py`
- Modify: `src/home_assist_agent/channels/message.py`
- Modify: `src/home_assist_agent/api/models.py`
- Modify: `src/home_assist_agent/api/app.py`
- Modify: `src/home_assist_agent/events/service.py`
- Test: `tests/test_target_resolution_service.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_audit_trace.py`
- Modify: `tests/test_event_service.py`

**Interfaces:**
- Consumes: catalog, term store, candidate builder, Codex resolver, verifier, and verified executor.
- Produces: `needs_input` responses and `ResolutionAttempt`.
- Produces one shared direct/indirect target-resolution path.

- [ ] **Step 1: Write failing end-to-end service tests**

```python
@pytest.mark.asyncio
async def test_open_bedside_light_resolves_before_execution() -> None:
    service = resolution_service(
        catalog=catalog_with(entity("light.bedside", aliases=["床头灯"])),
        codex_decision=selected("cand_01", confidence=0.95),
    )

    response = await service.execute(
        "打开床头灯",
        message_id="message-1",
        actor=ActorContext(home_id="home-1", person_id="person-1"),
    )

    assert response.status == CommandStatus.SUCCESS
    assert response.tool_call.arguments["name"] == "light.bedside"
```

Add tests for duplicate names returning three-or-fewer choices, zero side effects on ambiguity/no-match/low-confidence, clarification follow-up with new message ID and causation link, indirect planner target stripping, and direct target passthrough being unreachable.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_target_resolution_service.py -q`

Expected: FAIL because the orchestrator has no resolution dependencies.

- [ ] **Step 3: Implement the minimal unified orchestration**

Pipeline:

```text
route -> catalog -> visible terms -> candidates -> resolve_target
      -> verify -> optional non-target plan -> execute_verified
```

Return `needs_input` with persisted `ResolutionAttempt` before any MCP side effect. Inject `ActorContext` from `MessageChannel`, not from untrusted request JSON.
For system-derived events, inject the same trusted configured actor through
`EventService`; keep actor identity out of event payloads and public API models.

- [ ] **Step 4: Run service/API/audit tests and regression suite**

Run: `.venv/bin/pytest tests/test_target_resolution_service.py tests/test_api.py tests/test_audit_trace.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/commands src/home_assist_agent/channels/message.py src/home_assist_agent/api src/home_assist_agent/events/service.py tests/test_target_resolution_service.py tests/test_api.py tests/test_audit_trace.py tests/test_event_service.py
git commit -m "feat: orchestrate deterministic target resolution"
```

---

### Task 8: Provisional Learning and Explicit Corrections

**Files:**
- Create: `src/home_assist_agent/terms/service.py`
- Modify: `src/home_assist_agent/commands/service.py`
- Modify: `src/home_assist_agent/channels/message.py`
- Test: `tests/test_term_learning_service.py`

**Interfaces:**
- Consumes: successful full batch, original expression, verified target, actor, and message ID.
- Produces: `TermLearningService.record_success`, `handle_feedback`, `request_home_promotion`, `confirm_home_promotion`.

- [ ] **Step 1: Write failing learning and correction tests**

```python
@pytest.mark.asyncio
async def test_successful_execution_creates_personal_provisional_without_prompt() -> None:
    store = memory_store()
    service = TermLearningService(store=store, audit=InMemoryAuditRecorder())

    outcome = await service.record_success(
        actor=ActorContext(home_id="home-1", person_id="person-1"),
        expression="床头灯",
        target=verified("light.bedside"),
        execution=successful_batch("light.bedside"),
        source_message_id="message-1",
        now=NOW,
    )

    assert outcome.mapping.status == TermStatus.PROVISIONAL
    assert outcome.mapping.promote_at == NOW + timedelta(seconds=600)
    assert outcome.prompt_user is False
```

Add tests for partial execution not learning, no action stored, same approved mapping not downgraded, conflicting approved mapping not overwritten, “不是这个” rejection, explicit replacement approved without a device action, and term-store failure returning a warning without re-executing.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_term_learning_service.py -q`

Expected: FAIL because `TermLearningService` is missing.

- [ ] **Step 3: Implement minimal learning and feedback behavior**

Use deterministic correction phrase detection only for active provisional mappings. A correction without a replacement rejects; a correction with a replacement calls the same candidate/Codex/verifier stack but skips device execution.

- [ ] **Step 4: Run learning tests and regression suite**

Run: `.venv/bin/pytest tests/test_term_learning_service.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/terms/service.py src/home_assist_agent/commands/service.py src/home_assist_agent/channels/message.py tests/test_term_learning_service.py
git commit -m "feat: learn and correct personal terminology"
```

---

### Task 9: Promotion Worker and Household Sharing Confirmation

**Files:**
- Create: `src/home_assist_agent/terms/worker.py`
- Modify: `src/home_assist_agent/terms/service.py`
- Modify: `src/home_assist_agent/main.py`
- Test: `tests/test_term_promotion_worker.py`

**Interfaces:**
- Produces: `TermPromotionWorker.run_once(now) -> PromotionSummary`.
- Produces: application lifecycle `start()`/`stop()` integration.

- [ ] **Step 1: Write failing idempotent promotion tests**

```python
@pytest.mark.asyncio
async def test_due_provisional_is_approved_with_unique_system_message_id() -> None:
    audit = InMemoryAuditRecorder()
    store = due_mapping_store(mapping_id="map-1")
    worker = TermPromotionWorker(store=store, audit=audit)

    await worker.run_once(NOW_PLUS_10)
    await worker.run_once(NOW_PLUS_10)

    assert await store.status("map-1") == TermStatus.APPROVED
    promotion_requests = [
        event for event in audit.events if event.event_type == "system.request"
    ]
    assert len(promotion_requests) == 1
    assert promotion_requests[0].message_id.startswith("term-promote-map-1-")
```

Add tests for startup catch-up, rejected candidates skipped, audit failure preventing promotion mutation, explicit household request requiring confirmation, timeout cancellation, conflict display, and personal terminology precedence.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_term_promotion_worker.py -q`

Expected: FAIL because the worker is missing.

- [ ] **Step 3: Implement promotion and lifecycle**

Run a 30-second loop during application lifespan. `run_once` is the testable unit. Use stable message IDs per mapping revision and unique correlation/causation links. Household promotion writes no shared mapping before a separate confirmation message.

- [ ] **Step 4: Run worker tests and regression suite**

Run: `.venv/bin/pytest tests/test_term_promotion_worker.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/home_assist_agent/terms/worker.py src/home_assist_agent/terms/service.py src/home_assist_agent/main.py tests/test_term_promotion_worker.py
git commit -m "feat: promote terminology and confirm household sharing"
```

---

### Task 10: Runtime Wiring, Feature Flag Migration, and Full Audit Acceptance

**Files:**
- Modify: `src/home_assist_agent/bootstrap.py`
- Modify: `src/home_assist_agent/settings.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_target_resolution_audit.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_audit_trace.py`
- Modify: `tests/test_audit_recorder.py`

**Interfaces:**
- Produces: fully wired application with `TARGET_RESOLUTION_ENABLED`.
- Produces: feature-off compatibility and feature-on no-bypass behavior.

- [ ] **Step 1: Write failing runtime and full-chain tests**

```python
@pytest.mark.asyncio
async def test_success_chain_is_complete_and_target_is_never_raw() -> None:
    app, audit, mcp = wired_test_app(target_resolution_enabled=True)

    response = await post_command(app, "打开床头灯", message_id="message-1")

    assert response["request_id"] == response["message_id"] == "message-1"
    assert mcp.calls[0].arguments["name"] == "light.bedside"
    event_types = [event.event_type for event in await audit.list_events("message-1")]
    assert_required_ordered_subsequence(event_types, [
        "user.request",
        "codex.request",
        "codex.response",
        "external.request",
        "external.response",
        "target.candidates_generated",
        "codex.request",
        "codex.response",
        "external.request",
        "external.response",
        "target.verification_succeeded",
        "external.request",
        "external.response",
        "term.write.request",
        "term.provisional_created",
        "user.response",
    ])
```

Add full-chain tests for catalog failure, ambiguity, invalid candidate, audit failure before MCP, MCP failure, partial execution, term write failure, token redaction, Codex stdout/stderr failure, correction linkage, and system promotion linkage.
The ordered-subsequence helper permits additional append-only diagnostic events,
but fails if any required boundary event is absent or out of order.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_target_resolution_audit.py tests/test_runtime.py -q`

Expected: FAIL because runtime wiring and configuration are missing.

- [ ] **Step 3: Wire services and document configuration**

Instantiate one shared `SQLiteAuditRecorder`, then catalog, term store, candidate builder, Codex gateway, verifier, executor, term service, worker, orchestrator, and message channel. Ensure all receive the shared recorder and trusted actor configuration.

- [ ] **Step 4: Run focused and full verification**

Run:

```bash
.venv/bin/pytest \
  tests/test_target_resolution_audit.py \
  tests/test_runtime.py \
  tests/test_audit_trace.py \
  tests/test_audit_recorder.py \
  -q
```

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: all tests PASS with no warnings.

Run: `.venv/bin/python -m home_assist_agent --help`

Expected: exit code 0.

- [ ] **Step 5: Remove the feature-on bypass and inspect secrets**

Search:

```bash
rg -n '\\{"name": command\\.target\\}|arguments\\["name"\\].*target_expression' src tests
rg -n 'Authorization|HA_TOKEN|Bearer ' data docs output
```

Expected: no feature-on natural target passthrough and no persisted credentials.

- [ ] **Step 6: Commit**

```bash
git add \
  .env.example \
  README.md \
  pyproject.toml \
  src/home_assist_agent \
  tests
git commit -m "feat: enable audited target resolution and terminology learning"
```

---

## Plan Self-Review

- Every design-spec section maps to Tasks 1–10.
- HA identity comes from audited state and registry reads; `GetLiveContext` is not treated as an identity source.
- Candidate-only Codex selection, refreshed verification, exact-ID execution, and no raw target bypass have explicit failing tests.
- Personal provisional learning, 600-second promotion, correction, and household confirmation have separate tests.
- Every new external adapter and system worker has success, failure, redaction, and message-chain tests.
- Risk levels remain outside the implementation; only the existing compatibility guard and a port boundary remain.
- The plan contains no unresolved placeholders.
