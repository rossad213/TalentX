#!/usr/bin/env python3
"""Run Billboard refresh on the calibrated Music outcome policy."""
from __future__ import annotations

# Importing this module patches the shared outcome functions before Billboard
# binds them with ``from non_athlete_outcome_refresh import ...``.
import non_athlete_outcome_refresh_calibrated  # noqa: F401
import billboard_chart_refresh as base

if __name__ == "__main__":
    raise SystemExit(base.main())
