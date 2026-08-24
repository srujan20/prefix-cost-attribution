"""A rate that cannot be quoted without the size of the thing it was measured on.

Most of the interesting rates in this repository are zeros. The schemes that are
order independent by construction move a tenant's share by exactly nothing across
orderings, and a zero is the easiest number in the world to publish carelessly:
zero out of twelve and zero out of two hundred read identically in prose and are
not the same claim at all.

So a rate here carries its denominator, a Wilson score interval, and an explicit
`excludes` for asking whether the interval rules a value out. The floor property
is the one that gets used most: it is the smallest non zero rate the denominator
could have expressed, and quoting it beside a zero is what turns "never happened"
into "did not happen in two hundred tries, so anything above 0.005 is ruled out".

Wilson rather than the normal approximation, for the reason that decides it here:
the normal approximation puts a symmetric interval around zero, which includes
negative rates and collapses to zero width when the count is zero. Both of those
are wrong in exactly the case this repository spends most of its time in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 97.5th percentile of the standard normal, which is the two sided 95 percent
# coefficient. Written out rather than imported so this module has no dependency
# and the number can be checked by anyone who remembers it.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Rate:
    """A share, the count it came from, and the interval around it."""

    successes: int
    trials: int

    def __post_init__(self) -> None:
        if self.trials < 0 or self.successes < 0:
            raise ValueError("a rate cannot have a negative count")
        if self.successes > self.trials:
            raise ValueError(f"{self.successes} successes out of {self.trials} trials")

    @property
    def value(self) -> float:
        return self.successes / self.trials if self.trials else float("nan")

    @property
    def floor(self) -> float:
        """The smallest non zero rate this denominator can express."""
        return 1.0 / self.trials if self.trials else float("nan")

    @property
    def is_measured_zero(self) -> bool:
        return self.trials > 0 and self.successes == 0

    def interval(self, z: float = Z_95) -> tuple[float, float]:
        if self.trials == 0:
            return (float("nan"), float("nan"))
        n = self.trials
        p = self.value
        denominator = 1.0 + z * z / n
        centre = (p + z * z / (2 * n)) / denominator
        spread = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        return (max(0.0, centre - spread), min(1.0, centre + spread))

    def excludes(self, value: float) -> bool:
        """Whether the interval rules this value out.

        The whole point of the type. A measured zero only rules out a rate of
        0.02 once the denominator is large enough, and this method is where that
        question gets answered rather than assumed.
        """
        low, high = self.interval()
        if math.isnan(low):
            return False
        return value < low or value > high

    def render(self) -> str:
        if self.trials == 0:
            return "no trials"
        low, high = self.interval()
        if self.is_measured_zero:
            return f"0 in {self.trials} (below {self.floor:.4g}, interval [{low:.4g}, {high:.4g}])"
        return f"{self.value:.4g} [{low:.4g}, {high:.4g}] over {self.trials}"

    def as_dict(self) -> dict[str, object]:
        low, high = self.interval()
        return {
            "value": self.value,
            "successes": self.successes,
            "trials": self.trials,
            "low": None if math.isnan(low) else low,
            "high": None if math.isnan(high) else high,
            "floor": self.floor,
            "measured_zero": self.is_measured_zero,
        }
