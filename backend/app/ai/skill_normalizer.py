from __future__ import annotations

import re
from collections.abc import Sequence

from app.ai.schemas import ParsedSkill, TaxonomySkill

_SAFE_ALIASES: dict[str, tuple[str, ...]] = {
    "csharp": ("C#",),
    "cpp": ("C++",),
    "nextjs": ("Next.js", "NextJS"),
    "nodejs": ("Node.js", "NodeJS"),
    "aspnet-core": ("ASP.NET Core",),
    "cicd": ("CI/CD",),
    "nlp": ("NLP",),
}


def _boundary_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w+#.]){re.escape(value)}(?![\w+#.])", re.IGNORECASE)


def normalize_skills(
    text: str,
    taxonomy: Sequence[TaxonomySkill],
) -> tuple[ParsedSkill, ...]:
    """Return existing taxonomy skills in stable taxonomy-id order without mutation."""

    matches: list[ParsedSkill] = []
    seen_ids: set[int] = set()
    for skill in sorted(taxonomy, key=lambda item: item.id):
        if skill.id in seen_ids:
            continue
        aliases = (skill.name, *_SAFE_ALIASES.get(skill.normalized_name, ()))
        if any(_boundary_pattern(alias).search(text) for alias in aliases):
            matches.append(
                ParsedSkill(
                    skill_id=skill.id,
                    name=skill.name,
                    normalized_name=skill.normalized_name,
                )
            )
            seen_ids.add(skill.id)
    return tuple(matches)
