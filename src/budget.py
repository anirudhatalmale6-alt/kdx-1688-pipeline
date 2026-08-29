"""
The 300-points-a-day quota on the client's 1688 account.

Two rules from the client, and the whole module exists to enforce them:

  1. the run starts at 00:00 and keeps going until the points are gone, then
     picks up the next day;
  2. a point must never be spent on a product that was going to be rejected
     anyway - filter first, spend second.

Rule 2 is why `spend()` is called by the pull stage and not by the filter stage.
The counter lives on disk because the run is a cron job that may be restarted
mid-night, and a restart must not hand itself a fresh 300 points.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import paths


# Resolved when a budget is built, not when this module is imported: a cron job
# sets the environment before python starts, but a test - and the operator
# running a one-off with a different state directory - sets it afterwards.
def budget_state_path() -> str:
    return paths.state_path("points.json", "KDX_BUDGET_STATE")


def daily_points() -> int:
    return int(os.environ.get("KDX_DAILY_POINTS", "300"))


# The client's day, not UTC: they asked for the run to start at midnight their
# time. Saudi Arabia is UTC+3 all year with no daylight saving.
RIYADH = timezone(timedelta(hours=3))


class OutOfPoints(RuntimeError):
    pass


def business_day(now: datetime | None = None) -> str:
    now = now or datetime.now(RIYADH)
    return now.astimezone(RIYADH).strftime("%Y-%m-%d")


class PointBudget:
    def __init__(self, daily: int | None = None, state_path: str = ""):
        self.daily = daily_points() if daily is None else daily
        self.state_path = state_path or budget_state_path()
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = {}
        today = business_day()
        if state.get("day") != today:
            state = {"day": today, "spent": 0, "history": state.get("history", {})}
        return state

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=1)

    def roll_day_if_needed(self) -> None:
        today = business_day()
        if self.state.get("day") != today:
            history = self.state.setdefault("history", {})
            history[self.state["day"]] = self.state["spent"]
            # keep the last 60 days, no more
            for old in sorted(history)[:-60]:
                history.pop(old)
            self.state = {"day": today, "spent": 0, "history": history}
            self._save()

    @property
    def spent(self) -> int:
        return int(self.state.get("spent", 0))

    def remaining(self) -> int:
        self.roll_day_if_needed()
        return max(0, self.daily - self.spent)

    def can_spend(self, points: int = 1) -> bool:
        return self.remaining() >= points

    def spend(self, points: int = 1, note: str = "") -> int:
        """
        Consume points for a call that is about to be made. Raises rather than
        going negative: overrunning the quota gets the account throttled, and a
        half-finished night is cheaper than that.
        """
        self.roll_day_if_needed()
        if not self.can_spend(points):
            raise OutOfPoints(
                f"daily quota exhausted: {self.spent}/{self.daily} used on {self.state['day']}"
                + (f" ({note})" if note else "")
            )
        self.state["spent"] = self.spent + points
        self._save()
        return self.remaining()

    def seconds_until_next_day(self, now: datetime | None = None) -> int:
        now = (now or datetime.now(RIYADH)).astimezone(RIYADH)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # Round up, never down: truncating lands the caller at 23:59:59 and it
        # wakes to find the quota not reset yet.
        return math.ceil((midnight - now).total_seconds())

    def summary(self) -> dict:
        return {"day": self.state["day"], "spent": self.spent,
                "remaining": self.remaining(), "daily": self.daily,
                "resets_in_seconds": self.seconds_until_next_day()}


if __name__ == "__main__":
    budget = PointBudget()
    print(json.dumps(budget.summary(), indent=1))
    print("local time in Riyadh:", datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S"),
          "| unix", int(time.time()))
