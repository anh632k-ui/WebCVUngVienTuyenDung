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


def taxonomy_skill_aliases(skill: TaxonomySkill) -> tuple[str, ...]:
    """Return stable, reviewed aliases for one existing taxonomy skill."""
    return tuple(dict.fromkeys((skill.name, *_SAFE_ALIASES.get(skill.normalized_name, ()))))


def taxonomy_skill_pattern(skill: TaxonomySkill) -> re.Pattern[str]:
    """Compile the punctuation-safe matcher shared by Resume and Job parsing."""
    aliases = sorted(taxonomy_skill_aliases(skill), key=len, reverse=True)
    alternatives = "|".join(re.escape(alias) for alias in aliases)
    return re.compile(rf"(?<![\w+#.])(?:{alternatives})(?![\w+#.])", re.IGNORECASE)


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
        if taxonomy_skill_pattern(skill).search(text):
            matches.append(
                ParsedSkill(
                    skill_id=skill.id,
                    name=skill.name,
                    normalized_name=skill.normalized_name,
                )
            )
            seen_ids.add(skill.id)
    return tuple(matches)
