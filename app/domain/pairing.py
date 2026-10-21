"""Exact Swiss pairing.

Checkers colors are chosen by the players at the board and are not a pairing
constraint.

Hard constraints, in order:
1. Every active participant appears in exactly one pair or, when the active
   count is odd, in the single pairing-allocated bye.
2. Two participants who already faced each other are not paired again.
3. The bye goes to a participant with the fewest previous byes. A repeat bye
   is forbidden while any active participant has fewer byes.

Lexicographic objective among pairings that satisfy the hard constraints:
1. Minimize the bye recipient's current Swiss score (odd counts only).
2. Minimize the sum of absolute Swiss-score differences across pairs.
   Equal-score pairs therefore cost zero and are preferred.

If several pairings share that objective, one is chosen with `random.Random(seed)`.
The seed, the objective, and every generated variant are stored by the caller.
The implementation uses Edmonds' blossom algorithm (`networkx.max_weight_matching`)
with maximum cardinality. It is an exact matching, not a greedy Swiss heuristic.

When no pairing meets the hard constraints the function returns None. Callers
must stop and ask the administrator to approve early termination. Repeated
opponents are never inserted as a fallback.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations

import networkx as nx

from app.domain.exceptions import InfeasiblePairing

# Diff points change the weight by this much. Per-edge noise stays below it
# even when every edge in a 32-pair matching takes the maximum noise.
_DIFF_SCALE = 10_000
_NOISE_MOD = 100


@dataclass(frozen=True)
class PairingPlayer:
    participant_id: int
    score: int
    bye_count: int


@dataclass(frozen=True)
class PairingObjective:
    bye_score: int | None
    diff_sum: int

    def as_dict(self) -> dict[str, int | None]:
        return {"bye_score": self.bye_score, "diff_sum": self.diff_sum}


@dataclass(frozen=True)
class PairingOutcome:
    pairs: tuple[tuple[int, int], ...]
    bye: int | None
    objective: PairingObjective
    seed: int

    def canonical(self) -> tuple[int | None, tuple[tuple[int, int], ...]]:
        return self.bye, self.pairs


def canonical_pairs(
    pairs: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    bye: int | None,
) -> tuple[int | None, tuple[tuple[int, int], ...]]:
    normalized = tuple(sorted(tuple(sorted(pair)) for pair in pairs))
    return bye, normalized


def generate_pairing(
    players: list[PairingPlayer],
    history: set[tuple[int, int]],
    seed: int,
) -> PairingOutcome | None:
    """Return one optimal pairing, or None when every candidate is illegal.

    `history` contains ordered pairs `(min_id, max_id)` of previous opponents.
    """

    if len(players) < 2:
        return None
    ids = [player.participant_id for player in players]
    if len(ids) != len(set(ids)):
        raise InfeasiblePairing("Ishtirokchi identifikatorlari takrorlanmasligi kerak.")

    rng = random.Random(seed)
    by_id = {player.participant_id: player for player in players}
    played = set(history)

    if len(players) % 2 == 0:
        bye_choices: list[int | None] = [None]
    else:
        fewest = min(player.bye_count for player in players)
        bye_choices = sorted(
            player.participant_id for player in players if player.bye_count == fewest
        )

    valid: list[tuple[PairingObjective, tuple[tuple[int, int], ...], int | None]] = []
    for bye in bye_choices:
        rest = [participant_id for participant_id in sorted(by_id) if participant_id != bye]
        matched = _minimum_difference_matching(rest, by_id, played, rng)
        if matched is None:
            continue
        pairs, diff_sum = matched
        bye_score = None if bye is None else by_id[bye].score
        objective = PairingObjective(bye_score=bye_score, diff_sum=diff_sum)
        valid.append((objective, pairs, bye))

    if not valid:
        return None

    def sort_key(
        item: tuple[PairingObjective, tuple[tuple[int, int], ...], int | None],
    ) -> tuple[int, int]:
        objective = item[0]
        # Even counts have no bye score; they sort only by difference.
        bye_key = -1 if objective.bye_score is None else objective.bye_score
        return bye_key, objective.diff_sum

    best_key = min(sort_key(item) for item in valid)
    shortlist = [item for item in valid if sort_key(item) == best_key]
    objective, pairs, bye = rng.choice(shortlist)
    return PairingOutcome(pairs=pairs, bye=bye, objective=objective, seed=seed)


def _minimum_difference_matching(
    participant_ids: list[int],
    by_id: dict[int, PairingPlayer],
    played: set[tuple[int, int]],
    rng: random.Random,
) -> tuple[tuple[tuple[int, int], ...], int] | None:
    if len(participant_ids) % 2 != 0:
        return None
    graph = nx.Graph()
    graph.add_nodes_from(participant_ids)
    for left, right in combinations(participant_ids, 2):
        edge = (left, right) if left < right else (right, left)
        if edge in played:
            continue
        diff = abs(by_id[left].score - by_id[right].score)
        weight = (100_000 - diff) * _DIFF_SCALE + rng.randrange(_NOISE_MOD)
        graph.add_edge(left, right, weight=weight, diff=diff)

    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
    # NetworkX 3.4+ returns a set of undirected pairs. Older releases returned
    # a dict that listed every edge in both directions.
    raw_edges = matching.items() if isinstance(matching, dict) else matching
    seen: set[int] = set()
    pairs: list[tuple[int, int]] = []
    diff_sum = 0
    for left, right in raw_edges:
        if left in seen or right in seen:
            continue
        seen.add(left)
        seen.add(right)
        pair = (left, right) if left < right else (right, left)
        pairs.append(pair)
        diff_sum += abs(by_id[left].score - by_id[right].score)
    if len(seen) != len(participant_ids):
        return None
    return tuple(sorted(pairs)), diff_sum


def find_distinct_optimal(
    players: list[PairingPlayer],
    history: set[tuple[int, int]],
    *,
    base_seed: int,
    existing: set[tuple[int | None, tuple[tuple[int, int], ...]]],
    objective: PairingObjective,
    attempts: int = 40,
) -> PairingOutcome | None:
    """Search nearby seeds for another pairing with the same objective."""

    for offset in range(1, attempts + 1):
        outcome = generate_pairing(players, history, base_seed + offset)
        if outcome is None:
            continue
        if outcome.objective != objective:
            continue
        if outcome.canonical() in existing:
            continue
        return outcome
    return None
