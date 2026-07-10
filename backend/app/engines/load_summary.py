"""Weekly run-volume + strength-tonnage aggregation for the /plan load dashboard.

Pure functions -- callers shape ORM rows into plain dicts first (see
main.py's plan_view), no DB/HTTP dependency here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class WeeklyLoadPoint:
    week_start: date
    run_km: float
    run_pct: float  # 0-100, normalized against this series' own max
    tonnage_kg: float | None  # None = week hasn't happened yet
    tonnage_pct: float | None
    is_future: bool


def sum_run_km_by_week(run_rows: list[dict]) -> dict[date, float]:
    totals: dict[date, float] = {}
    for row in run_rows:
        totals[row["week_start"]] = totals.get(row["week_start"], 0.0) + (row.get("distance_km") or 0.0)
    return totals


def sum_strength_tonnage_by_week(completed_rows: list[dict]) -> dict[date, float]:
    totals: dict[date, float] = {}
    for row in completed_rows:
        sets = row.get("actual", {}).get("sets", [])
        tonnage = sum((s.get("reps") or 0) * (s.get("weight_kg") or 0) for s in sets)
        totals[row["week_start"]] = totals.get(row["week_start"], 0.0) + tonnage
    return totals


def build_weekly_load_series(
    week_starts: list[date],
    run_km_by_week: dict[date, float],
    tonnage_by_week: dict[date, float],
    current_week_start: date,
) -> list[WeeklyLoadPoint]:
    max_run_km = max(run_km_by_week.values(), default=0.0) or 1.0
    max_tonnage_kg = max(tonnage_by_week.values(), default=0.0) or 1.0

    points = []
    for week in sorted(week_starts):
        is_future = week > current_week_start
        run_km = run_km_by_week.get(week, 0.0)
        tonnage_kg = None if is_future else tonnage_by_week.get(week, 0.0)
        points.append(
            WeeklyLoadPoint(
                week_start=week,
                run_km=run_km,
                run_pct=round(run_km / max_run_km * 100, 1),
                tonnage_kg=tonnage_kg,
                tonnage_pct=None if tonnage_kg is None else round(tonnage_kg / max_tonnage_kg * 100, 1),
                is_future=is_future,
            )
        )
    return points
