"""Random tournaments must keep every hard pairing invariant."""

from __future__ import annotations

import random

from app.domain.pairing import PairingPlayer, generate_pairing
from app.domain.validation import max_swiss_rounds


def _invariant(
    players: list[int],
    pairs: tuple[tuple[int, int], ...],
    bye: int | None,
    history: set[tuple[int, int]],
    bye_counts: dict[int, int],
) -> None:
    seen: set[int] = set()
    for left, right in pairs:
        assert left != right
        edge = (left, right) if left < right else (right, left)
        assert edge not in history
        assert left not in seen and right not in seen
        seen.add(left)
        seen.add(right)
    if bye is None:
        assert len(players) % 2 == 0
    else:
        assert bye not in seen
        seen.add(bye)
        fewest = min(bye_counts[player] for player in players)
        assert bye_counts[bye] == fewest
    assert seen == set(players)


def test_randomized_swiss_fields() -> None:
    rng = random.Random(20261021)
    sizes = list(range(2, 16))
    for size in sizes:
        trials = 3 if size >= 10 else 1
        for _trial in range(trials):
            scores = {player: 0 for player in range(size)}
            bye_counts = {player: 0 for player in range(size)}
            history: set[tuple[int, int]] = set()
            limit = max_swiss_rounds(size)
            for round_number in range(1, limit + 1):
                players = [
                    PairingPlayer(player, scores[player], bye_counts[player])
                    for player in range(size)
                ]
                outcome = generate_pairing(players, history, seed=rng.randint(1, 1_000_000_000))
                if outcome is None:
                    break
                _invariant(
                    list(range(size)),
                    outcome.pairs,
                    outcome.bye,
                    history,
                    bye_counts,
                )
                for left, right in outcome.pairs:
                    history.add((left, right) if left < right else (right, left))
                    winner = rng.choice(["left", "right", "draw"])
                    if winner == "left":
                        scores[left] += 2
                    elif winner == "right":
                        scores[right] += 2
                    else:
                        scores[left] += 1
                        scores[right] += 1
                if outcome.bye is not None:
                    scores[outcome.bye] += 2
                    bye_counts[outcome.bye] += 1
                if size % 2 == 1 and round_number <= size:
                    assert max(bye_counts.values()) <= 1 or min(bye_counts.values()) >= 1
            if size % 2 == 1:
                assert sum(bye_counts.values()) == 0 or max(bye_counts.values()) >= 1
