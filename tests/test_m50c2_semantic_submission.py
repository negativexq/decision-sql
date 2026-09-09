# ruff: noqa: E501

from __future__ import annotations

from app.semantics.semantic_submission import (
    CLAIM_CONTRACT_VERSION,
    CatalogRegistry,
    CatalogView,
    ClaimAssertion,
    ClaimCheckStatus,
    ClaimFamily,
    SemanticClaim,
    SemanticSubmissionClaims,
    check_claims,
)


def _registry(*, complete: bool = True) -> CatalogRegistry:
    schema = [
        {
            "object_id": "attribute:orders:id",
            "attribute_id": "attribute:orders:id",
            "entity_id": "entity:orders",
            "physical_column_or_path": "id",
        },
        {
            "object_id": "attribute:customers:id",
            "attribute_id": "attribute:customers:id",
            "entity_id": "entity:customers",
            "physical_column_or_path": "id",
        },
        {"object_id": "entity:orders", "entity_id": "entity:orders", "physical_table": "orders"},
        {
            "object_id": "entity:customers",
            "entity_id": "entity:customers",
            "physical_table": "customers",
        },
    ]
    return CatalogRegistry(
        views=tuple(
            sorted(
                (
                    CatalogView(
                        family=ClaimFamily.SCHEMA_OBJECT,
                        id_field="object_id",
                        records=tuple(sorted(schema, key=lambda item: item["object_id"])),
                        authoritative_complete=complete,
                    ),
                    CatalogView(
                        family=ClaimFamily.RELATIONSHIP,
                        id_field="relationship_id",
                        records=(
                            {
                                "relationship_id": "relationship:orders_customers",
                                "left_attribute": "attribute:orders:id",
                                "right_attribute": "attribute:customers:id",
                                "authorized": True,
                            },
                            {
                                "relationship_id": "relationship:orders_private",
                                "left_attribute": "attribute:orders:id",
                                "right_attribute": "attribute:customers:id",
                                "authorized": False,
                            },
                        ),
                        authoritative_complete=complete,
                    ),
                    CatalogView(
                        family=ClaimFamily.SEMANTIC_DEFINITION,
                        id_field="rule_id",
                        records=({"rule_id": "rule:completed"},),
                        authoritative_complete=complete,
                    ),
                    CatalogView(
                        family=ClaimFamily.TEMPORAL_DEFINITION,
                        id_field="temporal_rule_id",
                        records=({"temporal_rule_id": "time:clock"},),
                        authoritative_complete=complete,
                    ),
                    CatalogView(
                        family=ClaimFamily.STATUS_DEFINITION,
                        id_field="rule_id",
                        records=({"rule_id": "rule:active"},),
                        authoritative_complete=complete,
                    ),
                    CatalogView(
                        family=ClaimFamily.POLICY,
                        id_field="policy_id",
                        records=({"policy_id": "policy:readonly"},),
                        authoritative_complete=complete,
                    ),
                ),
                key=lambda view: view.family.value,
            )
        )
    )


def test_catalog_claims_verify_and_contradict_without_oracle() -> None:
    registry = _registry()
    submission = SemanticSubmissionClaims(
        decision="BLOCKED_AUTHORITY",
        claims=(
            SemanticClaim(
                family=ClaimFamily.RELATIONSHIP,
                object_id="relationship:orders_customers",
                assertion=ClaimAssertion.UNAUTHORIZED,
            ),
            SemanticClaim(
                family=ClaimFamily.SEMANTIC_DEFINITION,
                object_id="rule:completed",
                assertion=ClaimAssertion.MISSING,
            ),
        ),
    )
    audit = check_claims(submission, registry)
    assert [item.status for item in audit.checks] == [
        ClaimCheckStatus.CONTRADICTED,
        ClaimCheckStatus.CONTRADICTED,
    ]
    assert CLAIM_CONTRACT_VERSION == "semantic-submission-claims-1"


def test_unknown_absence_and_population_are_not_promoted_to_truth() -> None:
    registry = _registry(complete=False)
    submission = SemanticSubmissionClaims(
        decision="NEEDS_CLARIFICATION",
        claims=(
            SemanticClaim(
                family=ClaimFamily.SEMANTIC_DEFINITION,
                object_id="rule:missing",
                assertion=ClaimAssertion.MISSING,
            ),
            SemanticClaim(
                family=ClaimFamily.POPULATION_SEMANTICS,
                object_id="population:orders",
                assertion=ClaimAssertion.DEFINED,
            ),
        ),
    )
    audit = check_claims(submission, registry)
    assert [item.status for item in audit.checks] == [
        ClaimCheckStatus.UNRESOLVED,
        ClaimCheckStatus.UNRESOLVED,
    ]


def test_relationship_sql_claim_is_checked_structurally() -> None:
    registry = _registry()
    submission = SemanticSubmissionClaims(
        decision="ANSWER",
        sql="SELECT o.id FROM orders o JOIN customers c ON o.id = c.id",
        claims=(
            SemanticClaim(
                family=ClaimFamily.RELATIONSHIP,
                object_id="relationship:orders_customers",
                assertion=ClaimAssertion.USED,
            ),
        ),
    )
    audit = check_claims(submission, registry)
    assert audit.checks[0].status is ClaimCheckStatus.VERIFIED


def test_contract_rejects_duplicate_claims() -> None:
    claim = SemanticClaim(
        family=ClaimFamily.TEMPORAL_DEFINITION,
        object_id="time:clock",
        assertion=ClaimAssertion.DEFINED,
    )
    try:
        SemanticSubmissionClaims(decision="ANSWER", claims=(claim, claim))
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate claims must be rejected")
