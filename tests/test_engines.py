"""Pure rules: pairing, scoring, prizes, state, and configuration."""

from __future__ import annotations

import random
from itertools import combinations

import pytest
from app.config import (
    Settings,
    asyncpg_engine_options,
    get_settings,
    libpq_database_url,
    normalize_database_url,
    validate_runtime_settings,
)
from app.domain.enums import ResultKind, TournamentStatus
from app.domain.exceptions import InvalidConfig, InvalidTransition
from app.domain.numbers import assign_tournament_numbers
from app.domain.pairing import PairingPlayer, generate_pairing
from app.domain.prizes import (
    TiebreakGame,
    build_clusters,
    first_unresolved_prize_cluster,
    head_to_head_split,
    prize_slot_count,
    rank_mini_tournament,
    remove_contender,
    replace_cluster,
    unawarded_prize_places,
)
from app.domain.scoring import SwissGame, calculate_standings, virtual_opponent_score
from app.domain.state_machine import assert_transition
from app.domain.validation import max_swiss_rounds, validate_limits
from app.presentation.messages import PARTICIPANT_MENU, money, prize_congratulations


def test_uzbek_menu_and_prize_text() -> None:
    assert PARTICIPANT_MENU[2] == "Mening o‘yinlarim"
    assert "200 000 so‘m" in money(200_000)
    text = prize_congratulations(
        player_name="Ali Valiyev",
        tournament_name="Bahor",
        place=1,
        amount=200_000,
    )
    assert "Ali Valiyev" in text
    assert "Bahor" in text
    assert "1-o‘rin" in text
    assert "200 000 so‘m" in text


def test_configuration_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "555:abc")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "77")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost:5432/shashka")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.admin_telegram_id == 77
    assert settings.async_database_url.startswith("postgresql+asyncpg://")
    assert normalize_database_url("postgresql://h/db").startswith("postgresql+asyncpg://")
    hosted = "postgresql://u:p@db.ondigitalocean.com:25060/defaultdb?sslmode=require"
    engine_url, connect_args = asyncpg_engine_options(hosted)
    assert engine_url == "postgresql+asyncpg://u:p@db.ondigitalocean.com:25060/defaultdb"
    assert connect_args == {"ssl": True}
    assert "sslmode=require" in libpq_database_url(hosted)
    validate_runtime_settings(settings)
    get_settings.cache_clear()


def test_round_count_validation() -> None:
    assert max_swiss_rounds(4) == 3
    assert max_swiss_rounds(5) == 5
    assert max_swiss_rounds(2) == 1
    validate_limits(4, 8, 3)
    with pytest.raises(InvalidConfig):
        validate_limits(4, 8, 4)
    with pytest.raises(InvalidConfig):
        validate_limits(1, 4, 1)


def test_state_transitions() -> None:
    assert_transition(TournamentStatus.DRAFT, TournamentStatus.REGISTRATION_OPEN)
    assert_transition(TournamentStatus.REGISTRATION_CLOSED, TournamentStatus.PAIRING_PREVIEW)
    assert_transition(TournamentStatus.ROUND_REVIEW, TournamentStatus.FINAL_REVIEW)
    assert_transition(
        TournamentStatus.ROUND_PAUSED,
        TournamentStatus.ROUND_ACTIVE,
        previous=TournamentStatus.ROUND_ACTIVE,
    )
    assert_transition(TournamentStatus.ROUND_PAUSED, TournamentStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        assert_transition(TournamentStatus.COMPLETED, TournamentStatus.REGISTRATION_OPEN)
    with pytest.raises(InvalidTransition):
        assert_transition(TournamentStatus.CANCELLED, TournamentStatus.FINAL_REVIEW)
    with pytest.raises(InvalidTransition):
        assert_transition(TournamentStatus.FINAL_REVIEW, TournamentStatus.ROUND_ACTIVE)


def test_tournament_numbers_are_a_random_label() -> None:
    first = assign_tournament_numbers([10, 20, 30, 40], seed=7)
    second = assign_tournament_numbers([10, 20, 30, 40], seed=7)
    assert first == second
    assert sorted(first.values()) == [1, 2, 3, 4]
    assert len(set(first.values())) == 4
    assert assign_tournament_numbers([1, 2, 3, 4, 5, 6, 7, 8], seed=1) != (
        assign_tournament_numbers([1, 2, 3, 4, 5, 6, 7, 8], seed=2)
    )


def _players(scores: dict[int, int], byes: dict[int, int] | None = None) -> list[PairingPlayer]:
    bye_counts = byes or {}
    return [
        PairingPlayer(participant_id=pid, score=score, bye_count=bye_counts.get(pid, 0))
        for pid, score in scores.items()
    ]


def test_initial_pairing_is_random_and_complete() -> None:
    players = _players({index: 0 for index in range(1, 9)})
    first = generate_pairing(players, set(), seed=1)
    assert first is not None
    assert first.objective.diff_sum == 0
    covered = {player for pair in first.pairs for player in pair}
    assert covered == set(range(1, 9))
    assert first.bye is None
    alternatives = [generate_pairing(players, set(), seed=seed) for seed in range(2, 25)]
    assert any(item is not None and item.canonical() != first.canonical() for item in alternatives)


def test_odd_first_round_assigns_one_bye() -> None:
    players = _players({index: 0 for index in range(1, 6)})
    outcome = generate_pairing(players, set(), seed=3)
    assert outcome is not None and outcome.bye is not None
    covered = {player for pair in outcome.pairs for player in pair}
    assert outcome.bye not in covered
    assert covered | {outcome.bye} == set(range(1, 6))


def test_no_repeat_and_prefers_equal_scores() -> None:
    players = _players({1: 4, 2: 4, 3: 0, 4: 0})
    history = {(1, 3)}
    outcome = generate_pairing(players, history, seed=5)
    assert outcome is not None
    assert (1, 3) not in outcome.pairs
    assert outcome.objective.diff_sum == 0
    assert set(outcome.pairs) == {(1, 2), (3, 4)}


def test_bye_goes_to_lowest_score_without_a_previous_bye() -> None:
    players = _players({1: 0, 2: 0, 3: 4}, {3: 0})
    outcome = generate_pairing(players, set(), seed=9)
    assert outcome is not None
    assert outcome.bye in {1, 2}
    assert outcome.bye != 3


def test_repeat_bye_is_avoided() -> None:
    players = _players({1: 0, 2: 2, 3: 2}, {1: 1, 2: 0, 3: 0})
    outcome = generate_pairing(players, set(), seed=4)
    assert outcome is not None
    assert outcome.bye in {2, 3}
    assert outcome.bye != 1


def test_infeasible_when_everyone_already_played() -> None:
    players = _players({1: 1, 2: 1})
    assert generate_pairing(players, {(1, 2)}, seed=1) is None


def _brute_cost(players: list[PairingPlayer], history: set[tuple[int, int]]) -> int | None:
    scores = {player.participant_id: player.score for player in players}
    ids = [player.participant_id for player in players]
    bye_counts = {player.participant_id: player.bye_count for player in players}

    def match_cost(rest: list[int]) -> int | None:
        best = None
        if not rest:
            return 0

        def walk(remaining: list[int], cost: int) -> None:
            nonlocal best
            if best is not None and cost >= best:
                return
            if not remaining:
                best = cost if best is None else min(best, cost)
                return
            first = remaining[0]
            for index in range(1, len(remaining)):
                other = remaining[index]
                edge = (first, other) if first < other else (other, first)
                if edge in history:
                    continue
                nxt = remaining[1:index] + remaining[index + 1 :]
                walk(nxt, cost + abs(scores[first] - scores[other]))

        walk(rest, 0)
        return best

    if len(ids) % 2 == 0:
        return match_cost(sorted(ids))
    fewest = min(bye_counts.values())
    eligible = [pid for pid in ids if bye_counts[pid] == fewest]
    best_pair = None
    for bye in eligible:
        rest = [pid for pid in ids if pid != bye]
        cost = match_cost(sorted(rest))
        if cost is None:
            continue
        candidate = (scores[bye], cost)
        if best_pair is None or candidate < best_pair:
            best_pair = candidate
    return None if best_pair is None else best_pair[1]


def test_pairing_matches_exact_enumeration() -> None:
    rng = random.Random(11)
    for _ in range(12):
        count = rng.choice([4, 6])
        scores = {index + 1: rng.randint(0, 6) for index in range(count)}
        history = set()
        ids = list(scores)
        for left, right in combinations(ids, 2):
            if rng.random() < 0.2:
                history.add((left, right) if left < right else (right, left))
        players = _players(scores)
        outcome = generate_pairing(players, history, seed=rng.randint(1, 10_000))
        expected = _brute_cost(players, history)
        if expected is None:
            assert outcome is None
        else:
            assert outcome is not None
            assert outcome.objective.diff_sum == expected


def test_virtual_opponent_and_buchholz() -> None:
    assert virtual_opponent_score(2, horizon=5, round_number=3) == 5
    games = [
        SwissGame(1, 1, 2, 2, ResultKind.REAL),
        SwissGame(2, 1, 1, 0, ResultKind.REAL),
        SwissGame(1, 2, None, 2, ResultKind.BYE),
        SwissGame(3, 1, None, 2, ResultKind.BYE),
        SwissGame(2, 2, 3, 2, ResultKind.REAL),
        SwissGame(3, 2, 2, 0, ResultKind.REAL),
    ]
    # Horizon 2. Player 1: round 1 win vs 2, round 2 bye.
    # Player 2 final points = 0 + 2 = 2. Player 1 points = 4.
    # Bye virtual at r=2: score before = 2, H=2 → 2 + (2-2+1) = 3.
    rows = calculate_standings([1, 2, 3], games, horizon=2)
    by_id = {row.participant_id: row for row in rows}
    assert by_id[1].points == 4
    assert by_id[1].buchholz == 2 + 3
    assert by_id[1].real_wins == 1
    assert by_id[1].sonneborn_berger == pytest.approx(1 * 2 + 1 * 3)


def test_sonneborn_berger_draw_and_technical_not_a_real_win() -> None:
    games = [
        SwissGame(1, 1, 2, 1, ResultKind.REAL),
        SwissGame(2, 1, 1, 1, ResultKind.REAL),
        SwissGame(1, 2, 2, 2, ResultKind.TECHNICAL),
        SwissGame(2, 2, 1, 0, ResultKind.TECHNICAL),
    ]
    rows = calculate_standings([1, 2], games, horizon=2)
    by_id = {row.participant_id: row for row in rows}
    assert by_id[1].real_wins == 0
    assert by_id[1].real_draws == 1
    assert by_id[1].technical_wins == 1
    # Real draw uses opponent final score 1 with weight 0.5.
    # Technical win uses virtual score, not the opponent's score.
    # Player 1 entering round 2 has 1. virtual = 1 + (2-2+1) = 2. SB += 1.0 * 2.
    assert by_id[1].sonneborn_berger == pytest.approx(0.5 * 1 + 2)


def test_shared_rank_when_every_tiebreak_matches() -> None:
    games = [
        SwissGame(1, 1, 2, 1, ResultKind.REAL),
        SwissGame(2, 1, 1, 1, ResultKind.REAL),
        SwissGame(3, 1, None, 0, ResultKind.BYE),
    ]
    rows = calculate_standings([1, 2, 3], games, horizon=1)
    by_id = {row.participant_id: row for row in rows}
    assert by_id[1].rank == by_id[2].rank == 1
    assert by_id[3].rank == 3


def test_prize_resolution_helpers() -> None:
    clusters = build_clusters([(1, 4), (2, 4), (3, 2), (4, 2)])
    assert first_unresolved_prize_cluster(clusters, 3) == 0
    assert head_to_head_split(1, 2, "DRAW") is None
    split = head_to_head_split(1, 2, "P1_WIN")
    assert split == [[1], [2]]
    updated = replace_cluster(clusters, 0, split or [])
    assert first_unresolved_prize_cluster(updated, 3) == 2
    ranked = rank_mini_tournament(
        [1, 2, 3],
        [
            TiebreakGame(1, 2, "DRAW"),
            TiebreakGame(1, 3, "DRAW"),
            TiebreakGame(2, 3, "DRAW"),
        ],
    )
    assert ranked == [[1, 2, 3]]
    assert prize_slot_count(2) == 2
    assert unawarded_prize_places(2) == [3]
    assert remove_contender([[1, 2, 3], [4]], 2) == [[1, 3], [4]]
