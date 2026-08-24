"""Entry point for `python -m prefixcost`.

Excluded from coverage rather than left red: it is exercised by a subprocess test,
and a subprocess runs in its own interpreter where this one's coverage cannot see
it.
"""

from .cli import main

raise SystemExit(main())  # pragma: no cover
