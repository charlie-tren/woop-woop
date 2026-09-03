"""Tests for the two corrections that decide a shipped distance.

Both of these had real bugs on 03/09/2026, and both bugs produced output that looked
entirely plausible - which is the bar this project uses for needing a test.

`cap_by_field` corrects distances the per-chunk buffer could not have measured. The
first version applied the correction to EVERY peak, and since the continental grid is
2 km and a city has a road in every cell, it silently rewrote every urban answer to
"0 m from anything". The remote answers it was designed for were all correct, so the
verifier passed; only an end-to-end run caught it.

`keep_substantial` decides which water bodies survive into the land mask. Area alone
cannot separate the Todd River from Sydney Harbour - both are long and convoluted - so
the rule is width, and the failure mode is a water stripe painted through Alice Springs.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "build"))

from water_areas import keep_substantial            # noqa: E402


# ------------------------------------------------------------- keep_substantial
def test_a_one_cell_wide_river_is_dropped():
    """The Todd through Alice Springs: long, thin, nowhere two cells across."""
    w = np.zeros((40, 40), bool)
    w[20, 2:38] = True
    assert not keep_substantial(w).any()


def test_a_wide_body_survives_whole():
    """A harbour is kept entire, not eroded - the arms have to come with it."""
    w = np.zeros((40, 40), bool)
    w[10:20, 10:20] = True                  # the wide channel
    w[14, 20:35] = True                     # a one-cell arm off it
    keep = keep_substantial(w)
    assert keep[10:20, 10:20].all(), "the wide part was eroded away"
    assert keep[14, 20:35].all(), "the narrow arm was lost - erosion, not selection"


def test_a_thin_arm_not_joined_to_anything_wide_is_dropped():
    w = np.zeros((40, 40), bool)
    w[10:20, 10:20] = True
    w[30, 2:38] = True                      # separate, thin
    keep = keep_substantial(w)
    assert keep[10:20, 10:20].all()
    assert not keep[30, :].any()


def test_an_empty_mask_stays_empty():
    assert not keep_substantial(np.zeros((10, 10), bool)).any()


# ------------------------------------------------------------------ cap_by_field
# cap_by_field needs a Grid and a saved field, so the RULE is tested directly here:
# below the trust threshold the local value must survive untouched, above it the
# minimum applies. This is the invariant the first version violated.
LOCAL_TRUST_M = 50_000.0


def apply_cap(local, coarse, trust=LOCAL_TRUST_M):
    """The rule as au_build.cap_by_field applies it."""
    local = np.asarray(local, float).copy()
    coarse = np.asarray(coarse, float)
    suspect = local > trust
    local[suspect] = np.minimum(local[suspect], coarse[suspect])
    return local


@pytest.mark.parametrize("local", [0.0, 100.0, 200.0, 1_000.0, 49_999.0])
def test_a_city_distance_is_never_touched(local):
    """The bug: a 2 km grid reads 0 m inside any city, so an unconditional minimum
    rewrote every urban answer to zero."""
    assert apply_cap([local], [0.0])[0] == local


def test_a_distance_beyond_the_buffer_is_corrected_down():
    assert apply_cap([176_400.0], [137_500.0])[0] == 137_500.0


def test_the_cap_never_raises_a_distance():
    """A per-chunk transform can only OVER-estimate, so the correction is one-way."""
    out = apply_cap([60_000.0], [90_000.0])
    assert out[0] == 60_000.0


def test_the_threshold_is_exclusive_at_the_boundary():
    assert apply_cap([LOCAL_TRUST_M], [0.0])[0] == LOCAL_TRUST_M
    assert apply_cap([LOCAL_TRUST_M + 1], [0.0])[0] == 0.0


def test_a_mixed_population_corrects_only_the_tail():
    local = np.array([0.0, 250.0, 40_000.0, 120_000.0, 176_400.0])
    coarse = np.array([0.0, 0.0, 0.0, 95_000.0, 137_500.0])
    out = apply_cap(local, coarse)
    assert list(out[:3]) == [0.0, 250.0, 40_000.0]
    assert list(out[3:]) == [95_000.0, 137_500.0]
