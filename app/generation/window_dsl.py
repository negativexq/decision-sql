"""Small deterministic provider-facing Window DSL parser."""

from app.generation.window_provider_dto import (
    ProviderAggregate,
    ProviderDirection,
    ProviderPattern,
    ProviderRankingFunction,
    ProviderTiePolicy,
    ProviderWindowIRDTO,
)

ALLOWED_KEYS = {
    "pattern",
    "source",
    "outputs",
    "partition",
    "order",
    "tie_breakers",
    "target",
    "offset",
    "n",
    "tie_policy",
    "aggregate",
    "ranking_function",
    "frame_mode",
    "frame_start_kind",
    "frame_start_value",
    "frame_end_kind",
    "frame_end_value",
    "scale",
    "alias",
}


def parse_window_dsl(text: str) -> ProviderWindowIRDTO:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "WINDOW":
        raise ValueError("DSL must start with WINDOW")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.count("=") != 1:
            raise ValueError("DSL fields must be key=value")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unknown DSL key: {key}")
        if key in values:
            raise ValueError(f"duplicate DSL key: {key}")
        if not value or any(marker in value for marker in (";", "--", "/*", "*/")):
            raise ValueError("invalid DSL value")
        values[key] = value
    required = {"pattern", "source", "outputs"} - values.keys()
    if required:
        raise ValueError("missing DSL keys: " + ", ".join(sorted(required)))
    pattern = ProviderPattern(values["pattern"])
    order = _orders(values.get("order", ""))
    tie_breakers = _orders(values.get("tie_breakers", ""))
    return ProviderWindowIRDTO(
        pattern=pattern,
        source_relation=values["source"],
        physical_outputs=_list(values["outputs"]),
        partition_by=_list(values.get("partition", "")),
        order_by=order,
        tie_breakers=tie_breakers,
        target=values.get("target"),
        offset=_int(values.get("offset")),
        n=_int(values.get("n")),
        tie_policy=ProviderTiePolicy(values["tie_policy"]) if "tie_policy" in values else None,
        aggregate=ProviderAggregate(values["aggregate"]) if "aggregate" in values else None,
        ranking_function=(
            ProviderRankingFunction(values["ranking_function"])
            if "ranking_function" in values
            else None
        ),
        frame_mode=values.get("frame_mode"),
        frame_start_kind=values.get("frame_start_kind"),
        frame_start_value=_int(values.get("frame_start_value")),
        frame_end_kind=values.get("frame_end_kind"),
        frame_end_value=_int(values.get("frame_end_value")),
        scale=_float(values.get("scale")),
        alias=values.get("alias"),
    )


def _list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _orders(value: str) -> tuple[dict[str, str], ...]:
    if not value:
        return ()
    result: list[dict[str, str]] = []
    for item in value.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError("order must be column:DIRECTION")
        result.append(
            {"column": parts[0].strip(), "direction": ProviderDirection(parts[1].strip()).value}
        )
    return tuple(result)


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdigit():
        raise ValueError("integer DSL fields must be unsigned decimal integers")
    return int(value)


def _float(value: str | None) -> float | None:
    return None if value is None else float(value)
