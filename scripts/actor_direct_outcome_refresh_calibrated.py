#!/usr/bin/env python3
"""Run direct Actor outcomes with stronger uncapped breakout calibration."""
from __future__ import annotations

import actor_direct_outcome_refresh as base
from market_outcome_calibration_v2 import (
    MODEL_VERSION as CALIBRATION_VERSION,
    actor_direct_box_office_target,
    actor_netflix_target,
)

_original_outcome_event = base.outcome_event


def calibrated_outcome_event(*args, **kwargs):
    event = _original_outcome_event(*args, **kwargs)
    event["pricingCalibrationVersion"] = CALIBRATION_VERSION
    return event


base.box_office_target = actor_direct_box_office_target
base.netflix_target = actor_netflix_target
base.outcome_event = calibrated_outcome_event

if __name__ == "__main__":
    raise SystemExit(base.main())
