"""The SQL function allowlist, shared by the runtime policy and the model context.

Kept free of other app imports so the governed context can show the model
exactly the list the policy enforces.
"""

from __future__ import annotations

SAFE_FUNCTION_FAMILIES = {
    "SAFE_AGGREGATE": frozenset(
        {
            "ARRAY_AGG",
            "AVG",
            "CORR",
            "COUNT",
            "JSON_AGG",
            "JSONB_AGG",
            "JSON_OBJECT_AGG",
            "JSONB_OBJECT_AGG",
            "LOGICAL_AND",
            "LOGICAL_OR",
            "MAX",
            "MIN",
            "MODE",
            "PERCENTILE_CONT",
            "PERCENTILE_DISC",
            "REGR_SLOPE",
            "STDDEV",
            "STRING_AGG",
            "SUM",
        }
    ),
    "SAFE_DATETIME": frozenset(
        {"AGE", "DATE_TRUNC", "EXTRACT", "MAKE_DATE", "STR_TO_DATE", "TO_CHAR", "TIMESTAMP_TRUNC"}
    ),
    "SAFE_NULL_HANDLING": frozenset({"COALESCE", "NULLIF"}),
    "SAFE_NUMERIC": frozenset(
        {
            "ABS",
            "CEIL",
            "EXP",
            "FLOOR",
            "GREATEST",
            "LEAST",
            "LN",
            "LOG",
            "POWER",
            "ROUND",
            "SIGN",
            "SQRT",
            "TRUNC",
        }
    ),
    "SAFE_STRING": frozenset(
        {
            "ARRAY_TO_STRING",
            # Pure text functions: no side effects, no external access, cost
            # linear in the input.  Regular-expression functions stay out.
            "BTRIM",
            "CONCAT",
            "CONCAT_WS",
            "INITCAP",
            "LEFT",
            "LENGTH",
            "LOWER",
            "PAD",
            "REPLACE",
            "REVERSE",
            "RIGHT",
            "SPLIT_PART",
            "STARTS_WITH",
            "STR_POSITION",
            "SUBSTRING",
            "TRANSLATE",
            "UPPER",
            "JSONB_EXTRACT_PATH_TEXT",
            "JSONB_EXTRACT_PATH",
            # SQLGlot represents PostgreSQL's read-only jsonb #>> operator
            # as JSONB_EXTRACT_SCALAR.  It extracts text and has no side
            # effects or external access.
            "JSONB_EXTRACT_SCALAR",
            "JSON_EXTRACT",
            "JSON_EXTRACT_SCALAR",
            "ROW_TO_JSON",
            "STRING_TO_ARRAY",
            "TRIM",
        }
    ),
    "SAFE_ARRAY": frozenset({"ARRAY", "ARRAY_REMOVE", "CARDINALITY", "UNNEST"}),
    "SAFE_JSON": frozenset({"JSON_BUILD_OBJECT", "JSONB_BUILD_OBJECT"}),
    "SAFE_WINDOW": frozenset(
        {
            "DENSE_RANK",
            "FIRST_VALUE",
            "LAG",
            "LAST_VALUE",
            "LEAD",
            "NTH_VALUE",
            "NTILE",
            "PERCENT_RANK",
            "RANK",
            "ROW_NUMBER",
        }
    ),
    "SAFE_SUBQUERY": frozenset({"EXISTS"}),
    "SAFE_CAST": frozenset({"CAST"}),
    # These expressions are read-only; their execution context is recorded
    # as provenance rather than treated as a safety violation.
    "SAFE_STABLE_TEMPORAL": frozenset(
        {
            "CURRENT_DATE",
            "CURRENT_TIME",
            "CURRENT_TIMESTAMP",
            "LOCALTIME",
            "LOCALTIMESTAMP",
            "NOW",
            "STATEMENT_TIMESTAMP",
            "TRANSACTION_TIMESTAMP",
        }
    ),
}

SAFE_FUNCTIONS = set().union(*SAFE_FUNCTION_FAMILIES.values())

# The policy checks SQLGlot's normalized names; the model writes PostgreSQL.
# These map a normalized name to the spellings that normalize to it.  An empty
# tuple marks a name SQLGlot only produces from an operator or another spelling.
_POSTGRES_SPELLINGS: dict[str, tuple[str, ...]] = {
    "EXTRACT": ("EXTRACT", "DATE_PART"),
    "JSON_EXTRACT": (),
    "JSON_EXTRACT_SCALAR": (),
    "JSONB_EXTRACT_SCALAR": (),
    "LENGTH": ("LENGTH", "CHAR_LENGTH"),
    "LOGICAL_AND": ("BOOL_AND",),
    "LOGICAL_OR": ("BOOL_OR",),
    "PAD": ("LPAD", "RPAD"),
    "STR_POSITION": ("POSITION", "STRPOS"),
    "STR_TO_DATE": ("TO_DATE",),
    "SUBSTRING": ("SUBSTRING", "SUBSTR"),
    "TIMESTAMP_TRUNC": (),
    "TRIM": ("TRIM", "LTRIM", "RTRIM"),
}


def model_visible_functions() -> tuple[str, ...]:
    """PostgreSQL function names the policy admits, as the model should write them."""
    return tuple(
        sorted(
            {
                spelling
                for name in SAFE_FUNCTIONS
                for spelling in _POSTGRES_SPELLINGS.get(name, (name,))
            }
        )
    )
