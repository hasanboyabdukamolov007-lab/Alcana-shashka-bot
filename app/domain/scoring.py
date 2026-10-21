"""Pure Swiss scoring, Buchholz, Sonneborn-Berger, and shared ranks.

These are project rules for a 2/1/0 scale. They are not a claim of FIDE or
other federation compliance.

Buchholz adds, for each counted round:
- the opponent's final Swiss score when the game was actually played;
- otherwise the virtual-opponent score defined below.

Sonneborn-Berger weights that same contribution by 1 for a real or awarded
win (2 points), 0.5 for a real draw (1 point), and 0 for a defeat.

Virtual opponent for an unplayed round r (pairing bye or technical result),
with scoring horizon H equal to the number of Swiss rounds actually completed:

    virtual_score = score_before_round + (H - r + 1) * 1

`score_before_round` is this participant's Swiss score from rounds before r.
Each virtual remaining round, including r, contributes one point (a draw on
this project's scale). Withdrawn opponents still contribute the Swiss points
they had already earned. Rounds that were never played are outside H and are
not invented.

Technical victories and byes award Swiss points but are not real victories.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import LOSS_POINTS, ResultKind


@dataclass(frozen=True)
class SwissGame:
    participant_id: int
    round_number: int
    opponent_id: int | None
    points: int
    kind: ResultKind


@dataclass(frozen=True)
class StandingRow:
    participant_id: int
    points: int
    buchholz: int
    sonneborn_berger_halves: int
    real_wins: int
    real_draws: int
    real_losses: int
    technical_wins: int
    technical_losses: int
    rank: int

    @property
    def sonneborn_berger(self) -> float:
        return self.sonneborn_berger_halves / 2


def virtual_opponent_score(score_before_round: int, horizon: int, round_number: int) -> int:
    if round_number < 1 or round_number > horizon:
        raise ValueError("Raund gorizont ichida bo‘lishi kerak.")
    return score_before_round + (horizon - round_number + 1)


def score_before_round(games: list[SwissGame], participant_id: int, round_number: int) -> int:
    return sum(
        game.points
        for game in games
        if game.participant_id == participant_id and game.round_number < round_number
    )


def swiss_points(games: list[SwissGame], participant_id: int, horizon: int) -> int:
    return sum(
        game.points
        for game in games
        if game.participant_id == participant_id and game.round_number <= horizon
    )


def _sb_halves(points: int, contribution: int) -> int:
    """Return Sonneborn-Berger contribution in half-points."""

    if points == 2:
        return 2 * contribution
    if points == 1:
        return contribution
    if points == LOSS_POINTS:
        return 0
    raise ValueError("Ball 0, 1 yoki 2 bo‘lishi kerak.")


def calculate_standings(
    participant_ids: list[int],
    games: list[SwissGame],
    *,
    horizon: int,
    final_scores: dict[int, int] | None = None,
) -> list[StandingRow]:
    """Rank participants. `final_scores` defaults to points inside `horizon`."""

    if horizon < 0:
        raise ValueError("Gorizont manfiy bo‘lishi mumkin emas.")
    everyone = set(participant_ids)
    everyone.update(game.participant_id for game in games)
    scores = {
        participant_id: swiss_points(games, participant_id, horizon) for participant_id in everyone
    }
    if final_scores is not None:
        scores.update(final_scores)

    raw: list[StandingRow] = []
    for participant_id in participant_ids:
        own_games = [
            game
            for game in games
            if game.participant_id == participant_id and 1 <= game.round_number <= horizon
        ]
        buchholz = 0
        sb_halves = 0
        real_wins = real_draws = real_losses = 0
        technical_wins = technical_losses = 0
        for game in own_games:
            if game.kind == ResultKind.REAL:
                if game.opponent_id is None:
                    raise ValueError("Haqiqiy o‘yinning raqibi bo‘lishi kerak.")
                contribution = scores[game.opponent_id]
                if game.points == 2:
                    real_wins += 1
                elif game.points == 1:
                    real_draws += 1
                else:
                    real_losses += 1
            else:
                entering = score_before_round(games, participant_id, game.round_number)
                contribution = virtual_opponent_score(entering, horizon, game.round_number)
                if game.kind == ResultKind.TECHNICAL:
                    if game.points == 2:
                        technical_wins += 1
                    elif game.points == 0:
                        technical_losses += 1
            buchholz += contribution
            sb_halves += _sb_halves(game.points, contribution)
        raw.append(
            StandingRow(
                participant_id=participant_id,
                points=scores[participant_id],
                buchholz=buchholz,
                sonneborn_berger_halves=sb_halves,
                real_wins=real_wins,
                real_draws=real_draws,
                real_losses=real_losses,
                technical_wins=technical_wins,
                technical_losses=technical_losses,
                rank=0,
            )
        )
    return _assign_ranks(raw)


def _strength(row: StandingRow) -> tuple[int, int, int, int]:
    return (row.points, row.buchholz, row.sonneborn_berger_halves, row.real_wins)


def _assign_ranks(rows: list[StandingRow]) -> list[StandingRow]:
    ordered = sorted(
        rows, key=lambda row: (*(-value for value in _strength(row)), row.participant_id)
    )
    ranked: list[StandingRow] = []
    previous_key: tuple[int, int, int, int] | None = None
    previous_rank = 0
    for index, row in enumerate(ordered):
        key = _strength(row)
        rank = previous_rank if key == previous_key else index + 1
        previous_key = key
        previous_rank = rank
        ranked.append(
            StandingRow(
                participant_id=row.participant_id,
                points=row.points,
                buchholz=row.buchholz,
                sonneborn_berger_halves=row.sonneborn_berger_halves,
                real_wins=row.real_wins,
                real_draws=row.real_draws,
                real_losses=row.real_losses,
                technical_wins=row.technical_wins,
                technical_losses=row.technical_losses,
                rank=rank,
            )
        )
    return ranked


def strictly_tied(left: StandingRow, right: StandingRow) -> bool:
    return _strength(left) == _strength(right)
