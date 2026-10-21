"""Prize places are ordered by playoff results, not by Buchholz.

Swiss points are never changed here. A tie that overlaps 1st, 2nd, or 3rd
place is resolved by:
- a head-to-head game for exactly two participants, repeated after a draw;
- a round-robin (2/1/0) for three or more, followed by another mini-tournament
  if a prize-relevant tie remains.

Participants marked withdrawn or excluded are omitted. If fewer than three
eligible finishers remain, only the available places are awarded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import PRIZE_SLOTS
from app.domain.exceptions import UnresolvedPrizes


@dataclass(frozen=True)
class TiebreakGame:
    player1_id: int
    player2_id: int
    outcome: str  # P1_WIN, P2_WIN, DRAW, TECHNICAL_P1, TECHNICAL_P2


def prize_slot_count(eligible_count: int) -> int:
    return max(0, min(PRIZE_SLOTS, eligible_count))


def build_clusters(entries: list[tuple[int, int]]) -> list[list[int]]:
    """Group eligible `(participant_id, swiss_points)` into score clusters."""

    ordered = sorted(entries, key=lambda item: (-item[1], item[0]))
    clusters: list[list[int]] = []
    current_points: int | None = None
    for participant_id, points in ordered:
        if current_points != points:
            clusters.append([participant_id])
            current_points = points
        else:
            clusters[-1].append(participant_id)
    return clusters


def first_unresolved_prize_cluster(
    clusters: list[list[int]],
    slots: int,
) -> int | None:
    """Index of the first tied cluster that still affects a prize place."""

    place = 1
    for index, cluster in enumerate(clusters):
        if place > slots:
            return None
        if len(cluster) > 1:
            return index
        place += 1
    return None


def prizes_resolved(clusters: list[list[int]], slots: int) -> bool:
    return first_unresolved_prize_cluster(clusters, slots) is None


def remove_contender(clusters: list[list[int]], participant_id: int) -> list[list[int]]:
    updated: list[list[int]] = []
    for cluster in clusters:
        remaining = [item for item in cluster if item != participant_id]
        if remaining:
            updated.append(remaining)
    return updated


def replace_cluster(
    clusters: list[list[int]],
    index: int,
    subgroups: list[list[int]],
) -> list[list[int]]:
    return [*clusters[:index], *subgroups, *clusters[index + 1 :]]


def rank_mini_tournament(
    player_ids: list[int],
    games: list[TiebreakGame],
) -> list[list[int]]:
    """Rank a completed mini-tournament. Draws leave a shared subgroup."""

    points = {player_id: 0 for player_id in player_ids}
    playing = set(player_ids)
    for game in games:
        if game.player1_id not in playing or game.player2_id not in playing:
            continue
        if game.outcome == "P1_WIN":
            points[game.player1_id] += 2
        elif game.outcome == "P2_WIN":
            points[game.player2_id] += 2
        elif game.outcome == "DRAW":
            points[game.player1_id] += 1
            points[game.player2_id] += 1
        elif game.outcome in {"TECHNICAL_P1", "TECHNICAL_P2"}:
            continue
        else:
            raise ValueError(f"Noma’lum natija: {game.outcome}")
    return build_clusters([(player_id, points[player_id]) for player_id in player_ids])


def head_to_head_split(player1_id: int, player2_id: int, outcome: str) -> list[list[int]] | None:
    """Return ordered subgroups, or None when the game must be replayed."""

    if outcome == "DRAW":
        return None
    if outcome in {"P1_WIN", "TECHNICAL_P1"}:
        return [[player1_id], [player2_id]]
    if outcome in {"P2_WIN", "TECHNICAL_P2"}:
        return [[player2_id], [player1_id]]
    raise ValueError(f"Noma’lum natija: {outcome}")


def assigned_prize_places(
    clusters: list[list[int]],
    slots: int,
) -> list[tuple[int, int]]:
    """Return `(place, participant_id)` once every prize cluster is a singleton."""

    if not prizes_resolved(clusters, slots):
        raise UnresolvedPrizes("Mukofot o‘rinlari hali aniqlanmagan.")
    assigned: list[tuple[int, int]] = []
    place = 1
    for cluster in clusters:
        for participant_id in cluster:
            if place <= slots:
                assigned.append((place, participant_id))
            place += 1
    return assigned


def unawarded_prize_places(eligible_count: int) -> list[int]:
    slots = prize_slot_count(eligible_count)
    return [place for place in range(1, PRIZE_SLOTS + 1) if place > slots]
