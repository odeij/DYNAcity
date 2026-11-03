import json
from datetime import date
from pathlib import Path

import pytest

from dynacity.evidence import (
    EvidenceRegistry,
    EvidenceSource,
    citation_problems,
    freeze_bundle,
    verify_bundle,
)
from dynacity.scenario_compiler import check_draft, compile_scenario, resolve_draft

from test_scenario_compiler import FakeClient, codes, draft, snapshot

AS_OF = date(2024, 4, 30)


def registry() -> EvidenceRegistry:
    return EvidenceRegistry(
        registry_id="test",
        sources=[
            EvidenceSource(source_id="grant-2021", title="Heritage grant scheme 2021", kind="report",
                           status="superseded", verified=True),
            EvidenceSource(source_id="grant-2023", title="Heritage grant scheme 2023", kind="report",
                           supersedes=["grant-2021"], effective_from=date(2023, 1, 1), verified=True),
            EvidenceSource(source_id="future-decree", title="Decree effective 2026", kind="decree",
                           effective_from=date(2026, 1, 1), verified=True),
            EvidenceSource(source_id="unchecked-study", title="Unchecked study", kind="study"),
        ],
    )


def test_bundle_id_is_content_derived_and_tamper_evident():
    first = freeze_bundle(registry(), AS_OF)
    second = freeze_bundle(registry(), AS_OF)
    assert first.bundle_id == second.bundle_id and first.bundle_id.startswith("evb-")
    assert verify_bundle(first)
    tampered = first.model_copy(deep=True)
    tampered.sources[0].title = "edited after freezing"
    assert not verify_bundle(tampered)
    assert freeze_bundle(registry(), date(2025, 1, 1)).bundle_id != first.bundle_id


def test_registry_rejects_duplicate_and_dangling_ids():
    with pytest.raises(ValueError):
        EvidenceRegistry(registry_id="x", sources=[
            EvidenceSource(source_id="a", title="Aaa", kind="law"),
            EvidenceSource(source_id="a", title="Aaa", kind="law"),
        ])
    with pytest.raises(ValueError):
        EvidenceRegistry(registry_id="x", sources=[
            EvidenceSource(source_id="a", title="Aaa", kind="law", supersedes=["missing"]),
        ])


def test_citation_problems_cover_supersession_dates_and_verification():
    bundle = freeze_bundle(registry(), AS_OF)
    superseded = dict(citation_problems(bundle, "grant-2021", AS_OF))
    assert "grant-2023" in superseded["evidence.superseded"]
    assert citation_problems(bundle, "grant-2023", AS_OF) == []
    assert "evidence.not_yet_effective" in dict(citation_problems(bundle, "future-decree", AS_OF))
    assert [c for c, _ in citation_problems(bundle, "unchecked-study", AS_OF)] == ["evidence.unverified"]
    assert "evidence.unknown_source" in dict(citation_problems(bundle, "nope", AS_OF))


def test_compiler_resolves_citation_to_versioned_reference():
    bundle = freeze_bundle(registry(), AS_OF)
    cited = draft(evidence_source_id="grant-2023", effect_source="Heritage grant scheme 2023",
                  confidence_grade="medium")
    assert check_draft(cited, snapshot(), "Apply the Heritage grant scheme 2023 in Mar Mikhael.", bundle).ok
    spec = resolve_draft(cited, snapshot(), bundle)
    assert spec.evidence_bundle_id == bundle.bundle_id
    assert spec.interventions[0].effect_source == f"evidence:grant-2023@{bundle.bundle_id}"
    assert spec.interventions[0].confidence_grade == "medium"


def test_superseded_citation_is_hard_and_unverified_caps_confidence():
    bundle = freeze_bundle(registry(), AS_OF)
    stale = draft(evidence_source_id="grant-2021", effect_source="Heritage grant scheme 2021")
    asked = "Use the Heritage grant scheme 2021 and the Unchecked study."
    assert "evidence.superseded" in {i.code for i in check_draft(stale, snapshot(), asked, bundle).hard()}
    weak = draft(evidence_source_id="unchecked-study", effect_source="Unchecked study",
                 confidence_grade="high")
    report = check_draft(weak, snapshot(), asked, bundle)
    assert report.ok and "evidence.unverified" in codes(report)
    assert resolve_draft(weak, snapshot(), bundle).interventions[0].confidence_grade == "low"


def test_citation_without_bundle_is_rejected():
    report = check_draft(draft(evidence_source_id="grant-2023"), snapshot(), "x")
    assert "evidence.no_bundle" in codes(report)


def test_bundle_sources_reach_prompt_and_repair_names_replacement():
    bundle = freeze_bundle(registry(), AS_OF)
    stale = draft(evidence_source_id="grant-2021", effect_source="Heritage grant scheme 2021")
    fixed = draft(evidence_source_id="grant-2023", effect_source="Heritage grant scheme 2023")
    client = FakeClient(stale, fixed)
    text = "Apply the Heritage grant scheme 2021 (now the Heritage grant scheme 2023)."
    result = compile_scenario(text, snapshot(), client=client, evidence=bundle)
    assert result.ok
    assert bundle.bundle_id in client.calls[0]["messages"][0]["content"]
    assert "cite ['grant-2023'] instead" in client.calls[1]["messages"][0]["content"]


def test_example_registry_freezes():
    path = Path(__file__).parents[1] / "examples" / "evidence_registry.example.json"
    bundle = freeze_bundle(EvidenceRegistry.model_validate(json.loads(path.read_text())), AS_OF)
    assert verify_bundle(bundle)


def test_registered_source_can_only_be_cited_when_the_planner_names_it():
    # Live finding: a model attached a registered law to an unrelated grant.
    bundle = freeze_bundle(registry(), AS_OF)
    cited = draft(evidence_source_id="grant-2023", effect_source="Heritage grant scheme 2023")
    unasked = check_draft(cited, snapshot(), "Give renovation grants to stalled buildings in Mar Mikhael.", bundle)
    assert "evidence.not_requested" in {i.code for i in unasked.hard()}
    aliased = registry()
    aliased.sources[1].aliases = ["the 2023 scheme"]
    bundle = freeze_bundle(aliased, AS_OF)
    assert check_draft(cited, snapshot(), "Apply the 2023 scheme in Mar Mikhael.", bundle).ok
