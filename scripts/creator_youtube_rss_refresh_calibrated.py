#!/usr/bin/env python3
"""Run Creator YouTube RSS outcomes with stronger breakout calibration."""
from __future__ import annotations

import creator_youtube_refresh as youtube
from market_outcome_calibration_v2 import (
    MODEL_VERSION as CALIBRATION_VERSION,
    creator_performance_target,
)

_original_youtube_event = youtube.youtube_event


def calibrated_youtube_event(*args, **kwargs):
    event = _original_youtube_event(*args, **kwargs)
    event["pricingCalibrationVersion"] = CALIBRATION_VERSION
    return event


youtube.performance_target = creator_performance_target
youtube.youtube_event = calibrated_youtube_event

# Import only after patching because the RSS adapter binds these helpers directly.
import creator_youtube_rss_refresh as base  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(base.main())
