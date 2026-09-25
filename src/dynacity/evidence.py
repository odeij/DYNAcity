"""Evidence registry and frozen, content-hashed evidence bundles.

`TransitionAdjustment.effect_source` is free text by contract, which is enough
to force *a* source but not to make one checkable. This module gives scenarios
something to point at:

    registry JSON (curated by people) → freeze_bundle(as_of) → EvidenceBundle
                                                     ↓
                     scenario levers cite `evidence:<source_id>@<bundle_id>`

A bundle is frozen: its id is derived from the canonical JSON of its sources,
so the same sources always produce the same id and any edit to any source
produces a different one. A forecast that cites `@evb-…` therefore identifies
the exact text of the evidence it was conditioned on, even after the registry
has moved on.

The module records provenance; it does not estimate effects. A lever citing a
law still carries a caller-asserted effect size — the citation only makes it
auditable whether that assertion had a basis, and whether the basis was in
force at the snapshot date.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVIDENCE_PREFIX = "evidence:"


class EvidenceSource(BaseModel):
    """One citable document. Written and checked by people, never by a model.

    `verified` is the curator's statement that the entry (title, dates,
    excerpt) was checked against the primary document. Unverified entries stay
    usable but cap the confidence of any lever that cites them.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=3)
    kind: Literal["law", "decree", "report", "study", "dataset", "notice", "other"]
    publisher: str | None = None
    url: str | None = None
    published: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    status: Literal["in_force", "superseded", "draft", "withdrawn"] = "in_force"
    supersedes: list[str] = Field(default_factory=list)
    excerpt: str | None = None
    # Other names planners use for it ("Law 194", "loi 194", "القانون 194"); a
    # lever may cite the source only if the request mentions its title or one
    # of these.
    aliases: list[str] = Field(default_factory=list)
    verified: bool = False
    notes: str | None = None

    def names(self) -> list[str]:
        return [self.title, *self.aliases]


class EvidenceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_id: str
    sources: list[EvidenceSource]

    @model_validator(mode="after")
    def consistent_ids(self) -> "EvidenceRegistry":
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence source_id values must be unique")
        known = set(ids)
        dangling = sorted({ref for s in self.sources for ref in s.supersedes} - known)
        if dangling:
            raise ValueError(f"supersedes references unknown sources: {dangling}")
        return self


class EvidenceBundle(BaseModel):
    """An immutable, hash-identified view of a registry as of one date."""

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    registry_id: str
    as_of: date
    frozen_at: str
    content_sha256: str
    sources: list[EvidenceSource]

    def get(self, source_id: str) -> EvidenceSource | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def superseded_by(self, source_id: str) -> list[str]:
        return sorted(s.source_id for s in self.sources if source_id in s.supersedes)

    def reference(self, source_id: str) -> str:
        return f"{EVIDENCE_PREFIX}{source_id}@{self.bundle_id}"


def _content_hash(registry_id: str, as_of: date, sources: list[EvidenceSource]) -> str:
    # Canonical JSON: sorted sources, sorted keys, no whitespace variance.
    # `frozen_at` is deliberately excluded so re-freezing identical content
    # reproduces the same id.
    payload = {
        "registry_id": registry_id,
        "as_of": as_of.isoformat(),
        "sources": [
            s.model_dump(mode="json") for s in sorted(sources, key=lambda s: s.source_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def freeze_bundle(registry: EvidenceRegistry, as_of: date) -> EvidenceBundle:
    digest = _content_hash(registry.registry_id, as_of, registry.sources)
    return EvidenceBundle(
        bundle_id=f"evb-{digest[:12]}",
        registry_id=registry.registry_id,
        as_of=as_of,
        frozen_at=datetime.now(UTC).isoformat(),
        content_sha256=digest,
        sources=sorted(registry.sources, key=lambda s: s.source_id),
    )


def verify_bundle(bundle: EvidenceBundle) -> bool:
    """True when the bundle's content still matches its recorded hash."""

    digest = _content_hash(bundle.registry_id, bundle.as_of, bundle.sources)
    return digest == bundle.content_sha256 and bundle.bundle_id == f"evb-{digest[:12]}"


def citation_problems(bundle: EvidenceBundle, source_id: str, on: date) -> list[tuple[str, str]]:
    """Reasons a source cannot back a lever on date `on`, as (code, message) pairs.

    Codes ending in `.unverified` are advisory; every other code disqualifies
    the citation. Messages name the replacement source when one exists, so they
    work as repair instructions.
    """

    source = bundle.get(source_id)
    if source is None:
        return [("evidence.unknown_source",
                 f"source {source_id!r} is not in bundle {bundle.bundle_id}; "
                 f"known: {[s.source_id for s in bundle.sources]}")]
    problems: list[tuple[str, str]] = []
    replacements = bundle.superseded_by(source_id)
    if source.status in {"superseded", "withdrawn"} or replacements:
        hint = f"; cite {replacements} instead" if replacements else ""
        problems.append(("evidence.superseded",
                         f"source {source_id!r} is {source.status if source.status != 'in_force' else 'superseded'}{hint}"))
    elif source.status == "draft":
        problems.append(("evidence.draft", f"source {source_id!r} is a draft, not in force"))
    if source.effective_from and on < source.effective_from:
        problems.append(("evidence.not_yet_effective",
                         f"source {source_id!r} takes effect {source.effective_from}, after {on}"))
    if source.effective_until and on > source.effective_until:
        problems.append(("evidence.expired",
                         f"source {source_id!r} expired {source.effective_until}, before {on}"))
    if not source.verified:
        problems.append(("evidence.unverified",
                         f"source {source_id!r} has not been checked against the primary document; "
                         "citing levers are capped at low confidence"))
    return problems
