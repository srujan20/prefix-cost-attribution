"""The interval arithmetic, checked against values computed by hand.

The tests that matter here are the two that pin `excludes`, because that method
is what decides whether a measured number is reported as a finding. An early
version of this test asserted the opposite of the arithmetic in a sibling
repository and passed for a while, so both directions are checked with counts far
enough apart that the answers cannot both be right by accident.
"""

from __future__ import annotations

import math

import pytest

from prefixcost.rates import Z_95, Rate


def test_a_rate_cannot_have_more_successes_than_trials():
    with pytest.raises(ValueError, match="out of"):
        Rate(successes=5, trials=4)


def test_a_rate_cannot_have_a_negative_count():
    with pytest.raises(ValueError, match="negative"):
        Rate(successes=-1, trials=4)


def test_no_trials_gives_nan_rather_than_a_number():
    rate = Rate(successes=0, trials=0)
    assert math.isnan(rate.value)
    assert math.isnan(rate.floor)
    assert math.isnan(rate.interval()[0])
    assert rate.excludes(0.5) is False
    assert rate.render() == "no trials"


def test_the_interval_around_a_measured_zero_is_not_zero_width():
    """The reason this is Wilson rather than the normal approximation.

    Zero out of two hundred is the shape of most of the claims in this
    repository, and the normal approximation would give it an interval of zero
    width, which would let a zero rule out every other value in the world.
    """
    rate = Rate(successes=0, trials=200)
    low, high = rate.interval()
    assert low == 0.0
    assert 0.0 < high < 0.03
    assert rate.is_measured_zero
    assert rate.floor == pytest.approx(0.005)


def test_a_small_sample_does_not_exclude_and_a_large_one_does():
    """Same observed share, different denominators, different claims.

    8 in 100 is 0.08 and its interval reaches below 0.05, so it does not rule
    0.05 out. 240 in 3000 is the same 0.08 on thirty times the evidence and does.
    """
    assert Rate(successes=8, trials=100).value == pytest.approx(0.08)
    assert Rate(successes=240, trials=3000).value == pytest.approx(0.08)
    assert Rate(successes=8, trials=100).excludes(0.05) is False
    assert Rate(successes=240, trials=3000).excludes(0.05) is True


def test_a_measured_zero_excludes_a_value_only_once_the_sample_is_large_enough():
    assert Rate(successes=0, trials=12).excludes(0.05) is False
    assert Rate(successes=0, trials=200).excludes(0.05) is True


def test_the_interval_matches_the_wilson_formula_computed_directly():
    successes, trials = 37, 400
    p = successes / trials
    z = Z_95
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    spread = (z / denominator) * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    low, high = Rate(successes=successes, trials=trials).interval()
    assert low == pytest.approx(centre - spread)
    assert high == pytest.approx(centre + spread)


def test_render_says_the_denominator_either_way():
    assert "over 400" in Rate(successes=37, trials=400).render()
    assert "0 in 200" in Rate(successes=0, trials=200).render()


def test_as_dict_carries_the_floor_and_the_bounds():
    payload = Rate(successes=3, trials=50).as_dict()
    assert payload["successes"] == 3
    assert payload["trials"] == 50
    assert payload["floor"] == pytest.approx(0.02)
    assert payload["low"] < payload["value"] < payload["high"]
    assert payload["measured_zero"] is False
