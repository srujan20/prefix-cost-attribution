"""The failure kinds this tool distinguishes, and the exit codes they carry.

`UsageError` is the caller's mistake and exits 4. `UnanswerableError` is a
question the tool cannot answer with the input it was given and exits 3. The
distinction matters here for the same reason it matters in every repository in
this portfolio: an attribution that returns zero for "nothing was shared" and
zero for "I could not tell what was shared" is an attribution nobody can act on.
"""

from __future__ import annotations


class PrefixCostError(Exception):
    """Base for everything this package raises deliberately."""


class UsageError(PrefixCostError):
    """The invocation was wrong. Exit 4."""


class VocabularyUnavailable(UsageError):
    """A requested pretrained vocabulary could not be reached. Exit 4."""


class UnanswerableError(PrefixCostError):
    """The question cannot be answered with this input. Exit 3."""


class PolicyError(UsageError):
    """The policy file is missing, malformed, or contains an impossible value."""
