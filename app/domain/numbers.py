"""Tournament numbers are a random labelling, not a ranking."""

from __future__ import annotations

import random


def assign_tournament_numbers(participant_ids: list[int], seed: int) -> dict[int, int]:
    if len(participant_ids) != len(set(participant_ids)):
        raise ValueError("Ishtirokchi identifikatorlari takrorlanmasligi kerak.")
    order = list(participant_ids)
    random.Random(seed).shuffle(order)
    return {participant_id: number for number, participant_id in enumerate(order, start=1)}
