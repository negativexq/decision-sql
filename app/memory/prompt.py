"""Bounded prompt rendering for verified query examples."""

from app.memory.models import VerifiedQueryExample


def render_verified_examples(examples: tuple[VerifiedQueryExample, ...]) -> str:
    sections = ["VERIFIED EXAMPLES (trusted references; adapt, do not copy blindly):"]
    for index, example in enumerate(examples, start=1):
        sections.extend(
            [
                "",
                f"--- VERIFIED EXAMPLE {index} ---",
                f"Question: {example.question}",
                "SQL:",
                example.sql,
                "--- END VERIFIED EXAMPLE ---",
            ]
        )
    return "\n".join(sections)


def render_memory_schema_context(schema: str, examples: tuple[VerifiedQueryExample, ...]) -> str:
    return f"{schema}\n\n{render_verified_examples(examples)}"
