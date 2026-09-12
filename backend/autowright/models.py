"""§19 request models: every mutating endpoint's body is validated here by
pydantic before any handler logic runs — a malformed body (missing or mistyped
fields, wrong shapes) answers 422. Types, shapes, and required fields live in
these models; semantic validation (the cron dialect, the §4.3 trigger rules,
the §8 step validators) and the store-state cross-field checks (paramValues
against param definitions, agent existence) stay in the handlers and in
triggers.py / drafting.py. Pydantic shapes requests only — response bodies
remain plain dicts (§2)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

# A recurring pattern below: `field: StrictX = None` (no `| None` in the
# annotation). Pydantic never validates defaults, so this reads "the key may be
# absent, but when sent it must be a strict X — an explicit null is rejected".
# `StrictX | None = None` is used only where null is a meaningful value.


class StepIn(BaseModel):
    """One draft step. §4.1 spelling boundary: the API spelling of the step
    flags is camelCase (noTimeout, infiniteRetries); disk and the internal
    shape are snake_case only. This model is the one place the client spelling
    is accepted — `plain()` emits snake_case, and nothing past this boundary
    reads the camel keys. Everything else (file, name, code, timeout, …) rides
    through as-is; the §8 step validators judge it server-side where required."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    no_timeout: bool | None = Field(default=None, alias="noTimeout")
    infinite_retries: bool | None = Field(default=None, alias="infiniteRetries")

    def plain(self) -> dict:
        d = dict(self.model_extra or {})
        if self.no_timeout:
            d["no_timeout"] = True
        if self.infinite_retries:
            d["infinite_retries"] = True
        return d


class DraftIn(BaseModel):
    """A §4.4 draft payload. Only `steps` is typed (each entry must be an
    object — the camelCase flag boundary above); the rest (name, description,
    spec, params, packages, triggers, stepAgents, allowedSecrets, …)
    passes through untouched for the handler's semantic checks. `plain()`
    preserves key presence — a handler's `"triggers" in d` still works."""

    model_config = ConfigDict(extra="allow")

    steps: list[StepIn] = None

    def plain(self) -> dict:
        d = dict(self.model_extra or {})
        if "steps" in self.model_fields_set:
            d["steps"] = [s.plain() for s in (self.steps or [])]
        return d


class SnapshotSettingsPatch(BaseModel):
    """§6.3 automatic-snapshot toggles — partial object, sent keys merged."""

    preVersion: StrictBool = None
    preClear: StrictBool = None
    preRestore: StrictBool = None


class AutomationPatch(BaseModel):
    """PATCH /automations/{id} — user-owned fields only (§19)."""

    name: StrictStr | None = None            # blank/missing is ignored by the store
    description: StrictStr | None = None     # blank clears (§4.1)
    triggers: list[dict[str, Any]] = None    # §4.3 rules checked in the handler
    paramValues: dict[StrictStr, Any] = None  # names+kinds checked against defs in the handler
    agentId: StrictStr | None = None          # existence checked in the handler
    stepAgents: list[StrictStr] = None        # existence checked in the handler
    allowedSecrets: list[StrictStr] = None
    snapshotSettings: SnapshotSettingsPatch = None
    maxParallel: StrictInt = Field(default=None, ge=1)   # §6 — strict int, floor 1
    maxQueued: StrictInt = Field(default=None, ge=0)     # §6 — strict int, floor 0


class ConcurrencyIn(BaseModel):
    """§8 chat-staged concurrency — partial object, same floors as the PATCH."""

    maxParallel: StrictInt = Field(default=None, ge=1)   # §6 — strict int, floor 1
    maxQueued: StrictInt = Field(default=None, ge=0)     # §6 — strict int, floor 0


class AutomationCreate(BaseModel):
    """POST /automations (§19)."""

    draft: DraftIn
    name: StrictStr | None = None
    agentId: StrictStr | None = None
    stepAgents: list[StrictStr] = None
    allowedSecrets: list[StrictStr] = None
    paramValues: dict[StrictStr, Any] = None  # §4.2 staged values — lenient name+kind match
    concurrency: ConcurrencyIn = None         # §8 staged concurrency — applied like the PATCH


class VersionSave(BaseModel):
    """POST /automations/{id}/versions (§19)."""

    draft: DraftIn
    name: StrictStr | None = None
    agentId: StrictStr | None = None
    stepAgents: list[StrictStr] = None
    allowedSecrets: list[StrictStr] = None
    paramValues: dict[StrictStr, Any] = None  # §4.2 staged values — lenient name+kind match
    concurrency: ConcurrencyIn = None         # §8 staged concurrency — applied like the PATCH


class VersionRestore(BaseModel):
    """POST /automations/{id}/restore (§19)."""

    version: StrictInt


class DraftPut(BaseModel):
    """PUT /draft/{owner} — the §4.4 container snapshot for either owner.
    `agentId` rides beside the payload for the pending slot (the identity no
    automation record exists to hold); ignored for an automation owner."""

    draft: DraftIn
    agentId: StrictStr | None = None


class ChatPut(BaseModel):
    """PUT /chat/{owner} — the §11 thread, rewritten whole (§4.4 thread
    lifetime); an empty list unlinks the file (§11 Clear chat). Entry shapes
    are checked semantically by the store (kind-less entries are dropped)."""

    chat: list[dict[str, Any]]


class TriggersPreview(BaseModel):
    """POST /triggers/preview (§19) — a list of §4.3-shaped trigger dicts.
    Only a body that isn't a list of objects is a 422; a semantically invalid
    entry is a per-entry `valid: false` result."""

    triggers: list[dict[str, Any]]


class ExecuteBody(BaseModel):
    """POST /automations/{id}/execute (§19)."""

    version: StrictStr | None = None
    trigger: Literal["manual", "menubar"] = "manual"
    # §6/§19: opt into the firing queue when every slot is taken — the §9.2
    # capacity popup's Queue action. False keeps the refuse-with-409 behavior.
    queue: StrictBool = False


class AppStarted(BaseModel):
    """POST /app-started (§19) — launchId is required and nonempty: the
    idempotency dedupe is the whole point of the endpoint."""

    launchId: StrictStr = Field(min_length=1)


class TestStart(BaseModel):
    """POST /tests (§19). triggerMock's §4.3 field rules stay in the handler."""

    draft: DraftIn
    automationId: StrictStr | None = None
    enabledAgents: list[StrictStr] = None
    allowedSecrets: list[StrictStr] = None
    paramValues: dict[StrictStr, Any] = None
    triggerMock: dict[str, Any] | None = None
    # §11 stale-outcome rule: the renderer's opaque fingerprint of the tested
    # steps, stored verbatim in test.yaml and echoed on the draft payload.
    stepsFingerprint: StrictStr | None = None


class PackageRef(BaseModel):
    """One §6.2 package declaration — `import` is a Python keyword, hence the
    alias; `plain()` emits the wire spelling the packages module reads."""

    pip: StrictStr
    import_: StrictStr = Field(alias="import")

    def plain(self) -> dict:
        return {"pip": self.pip, "import": self.import_}


class PackagesBody(BaseModel):
    """POST /packages/check|install|outdated|update (§19)."""

    packages: list[PackageRef] = Field(default_factory=list)


class SnapshotCreate(BaseModel):
    """POST /automations/{id}/memory/snapshots (§6.3)."""

    name: StrictStr | None = None


class SnapshotRename(BaseModel):
    """PATCH /automations/{id}/memory/snapshots/{snapshot_id} — null/"" clears."""

    name: StrictStr | None = None


class DraftJobStart(BaseModel):
    """POST /drafts (§19) — a §8 drafting job."""

    mode: Literal["chat", "sync"]
    automationId: StrictStr | None = None
    text: StrictStr | None = None
    spec: Any = None                      # sync sends spec markdown; edit sends blocks
    current: DraftIn | None = None        # the in-editor draft (camel steps normalized)
    chat: list[dict[str, Any]] | None = None
    executionId: StrictStr | None = None
    agentId: StrictStr | None = None
    enabledAgents: list[StrictStr] = None
    allowedSecrets: list[StrictStr] = None


class SkipStep(BaseModel):
    """POST /executions/{id}/skip-step (§7)."""

    index: StrictInt


class ImportUrl(BaseModel):
    """POST /automations/import/url (§5.2)."""

    url: StrictStr


class ImportConfirm(BaseModel):
    """POST /automations/import/confirm (§5.2)."""

    token: StrictStr


class MarketplaceAdd(BaseModel):
    """POST /marketplace/sources (§22.4) - exactly one of the two, non-empty;
    the exactly-one rule and the https/absolute-path rules are the handler's."""

    url: StrictStr | None = None
    path: StrictStr | None = None


class MarketplaceCatalogCreate(BaseModel):
    """POST /marketplace/catalogs (§22.7): the folder to create the catalog in."""

    folder: StrictStr


class MarketplaceCatalogEntry(BaseModel):
    """One §22.7 editor row: exactly one of `path` (kept as written),
    `automationId` (exported on save), or `archiveFile` (copied on save) - the
    exactly-one rule is the store's, so it names the entry."""

    title: StrictStr = ""
    description: StrictStr = ""
    path: StrictStr | None = None
    image: StrictStr | None = None
    automationId: StrictStr | None = None
    archiveFile: StrictStr | None = None


class MarketplaceCatalogSave(BaseModel):
    """PUT /marketplace/sources/{id}/catalog (§22.7)."""

    name: StrictStr = ""
    description: StrictStr = ""
    url: StrictStr = ""
    entries: list[MarketplaceCatalogEntry] = Field(default_factory=list)


class AgentAdd(BaseModel):
    """POST /agents (§4.7) — harness membership and the mode/model matrix stay
    semantic (the handler's), the shapes are checked here."""

    harness: StrictStr
    mode: Literal["default", "ollama", "custom"] = "default"
    model: StrictStr | None = None
    name: StrictStr | None = None
    description: StrictStr | None = None


class AgentPatch(BaseModel):
    """PATCH /agents/{id} — same shape rules as POST; `model: null` is a real
    value (the §4.7 matrix rejects it semantically where a model is needed)."""

    harness: StrictStr = None
    mode: Literal["default", "ollama", "custom"] = None
    model: StrictStr | None = None
    name: StrictStr | None = None
    description: StrictStr | None = None
    default: StrictBool = None


class CheckHarness(BaseModel):
    """POST /agents/check-harness (§19) — mode constrained like AgentAdd's,
    so an unknown mode answers 422 instead of silently reading as default."""

    harness: StrictStr
    mode: Literal["default", "ollama", "custom"] = "default"
    model: StrictStr | None = None


class ProviderId(BaseModel):
    """POST /agents/install · /agents/login — provider id body."""

    id: StrictStr


class OllamaPull(BaseModel):
    """POST /ollama/pull (§19)."""

    model: StrictStr


class SecretCreate(BaseModel):
    """POST /secrets (§4.8) — create: a blank value creates a placeholder.
    The name is validated (and checked unique) in the handler."""

    name: StrictStr
    value: StrictStr = ""
    description: StrictStr | None = None


class SecretPut(BaseModel):
    """PUT /secrets/{id} (§4.8) — edit: the name is immutable and not a field;
    a blank value keeps the stored state; description edits are
    presence-sensitive (exclude_unset)."""

    value: StrictStr = ""
    description: StrictStr | None = None


class SettingsPatch(BaseModel):
    """PATCH /settings (§4.9/§19) — strictly-typed: booleans must be booleans,
    days a real int (bool/float/string rejected), notifications the §4.9 enum."""

    login: StrictBool = None
    menuBarIcon: StrictBool = None
    keepAwake: StrictBool = None
    automaticUpdateCheck: StrictBool = None
    keepForever: StrictBool = None
    developerMode: StrictBool = None
    cliEnabled: StrictBool = None
    notifications: Literal["attention", "all"] = None
    days: StrictInt = None


class DataPath(BaseModel):
    """POST /settings/data-path (§19)."""

    path: StrictStr
