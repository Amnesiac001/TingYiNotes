from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .models import Segment


@dataclass(frozen=True)
class ParagraphRules:
    preferred_sentences: int = 3
    max_sentences: int = 5
    max_duration_ms: int = 25_000
    max_english_chars: int = 420
    natural_gap_ms: int = 1_100
    force_gap_ms: int = 2_000


DEFAULT_PARAGRAPH_RULES = ParagraphRules()

_TRANSITION = re.compile(
    r"^(?:now\s+(?:let(?:'s| us)|we(?:'ll| will))|next\b|moving on\b|"
    r"let(?:'s| us)\s+(?:move|turn)|another\s+(?:point|topic|question)|"
    r"in (?:summary|conclusion)|to summarize\b|finally\b)",
    re.IGNORECASE,
)


def should_start_new_paragraph(
    current: Sequence[Segment],
    incoming: Segment,
    rules: ParagraphRules = DEFAULT_PARAGRAPH_RULES,
) -> bool:
    """Return whether an incoming stable sentence should begin a new display paragraph."""
    if not current:
        return False
    first = current[0]
    previous = current[-1]
    gap_ms = max(0, incoming.start_ms - previous.end_ms)
    proposed_duration = max(0, incoming.end_ms - first.start_ms)
    proposed_chars = sum(len(item.original_text.strip()) for item in current) + len(
        incoming.original_text.strip()
    )
    if len(current) >= rules.max_sentences:
        return True
    if gap_ms >= rules.force_gap_ms:
        return True
    if len(current) >= rules.preferred_sentences and gap_ms >= rules.natural_gap_ms:
        return True
    if len(current) >= 2 and proposed_duration > rules.max_duration_ms:
        return True
    if len(current) >= 2 and proposed_chars > rules.max_english_chars:
        return True
    if len(current) >= 2 and _TRANSITION.match(incoming.original_text.strip()):
        return True
    return False


def group_segments(
    segments: Sequence[Segment],
    rules: ParagraphRules = DEFAULT_PARAGRAPH_RULES,
) -> list[list[Segment]]:
    paragraphs: list[list[Segment]] = []
    for segment in segments:
        if not paragraphs or should_start_new_paragraph(paragraphs[-1], segment, rules):
            paragraphs.append([segment])
        else:
            paragraphs[-1].append(segment)
    return paragraphs
