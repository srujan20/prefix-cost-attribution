"""The optional path to a pretrained vocabulary, written here and never run here.

This module exists so that the choice recorded in ADR-003 is a choice rather than
a limitation. The environment this repository was built in cannot reach the hosts
that serve pretrained vocabularies, so the code below has been written, reviewed
and typed out, and has never executed against a real download. That is stated
plainly because the alternative is a reader assuming it was tested.

What it changes, and what it does not. Every published figure in this repository
is a ratio of token counts produced by one tokeniser, so the vocabulary cancels
out of all of them. What a real vocabulary would change is the absolute counts:
a production tokeniser with fifty thousand or a hundred thousand tokens segments
ordinary prose more coarsely than the small vocabulary trained from this corpus,
so both the prompt token count and the distinct prefix token count would fall,
and their ratio would not move much.

What it would cost is the property the whole repository rests on. A downloaded
vocabulary is a file the reader cannot inspect, so a token count that disagreed
with theirs could not be checked by following the merges. `make verify` would
also stop working on a machine with no network.

The import is inside the functions rather than at module scope, and a test walks
the syntax tree of every file under src asserting that. A module scope import
would make `pip install prefixcost` followed by a first run reach the network,
which is the one thing this repository promises it never does.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import VocabularyUnavailable

# The encodings a caller is most likely to want. Named rather than accepted as
# free text so a typo fails here with a list rather than at download time with a
# stack trace from somebody else's library.
KNOWN_ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base")


def pretrained_available() -> bool:
    """Whether a pretrained vocabulary could be loaded at all.

    Answers on a machine with no tiktoken installed, which is what `plan` calls
    it for. It reports whether the library is importable and not whether the
    download would succeed: those are different failures and the second one only
    has an answer at the moment it is attempted.
    """
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        return False
    return True


def require_pretrained() -> None:
    """Refuse clearly rather than fail obscurely three frames later."""
    if not pretrained_available():
        raise VocabularyUnavailable(
            "a pretrained vocabulary needs the vocabularies extra: "
            "pip install -e '.[vocabularies]'. The default tokeniser is trained "
            "from the committed corpus and needs nothing, see ADR-003"
        )


@dataclass(frozen=True)
class PretrainedEncoder:
    """A tiktoken encoding, wrapped to look like the trained vocabulary.

    Only the two methods the rest of the package uses. The tokens are integers
    here rather than strings, which the trie does not care about: it keys on
    equality and hashes, and both work for either.
    """

    name: str
    _encoding: object

    @property
    def size(self) -> int:
        return int(self._encoding.n_vocab)

    def encode(self, text: str) -> tuple[int, ...]:
        return tuple(self._encoding.encode(text))

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def as_dict(self) -> dict[str, object]:
        return {"size": self.size, "encoding": self.name, "trained_here": False}


def load_pretrained(name: str = "cl100k_base") -> PretrainedEncoder:
    """Load a named encoding, downloading it if it is not already cached.

    Written and never run in this environment. A caller reaching for this is
    trading reproducibility for realism, and ADR-003 states which of the two this
    repository chose and why.
    """
    require_pretrained()
    if name not in KNOWN_ENCODINGS:
        raise VocabularyUnavailable(
            f"unknown encoding {name!r}, expected one of {list(KNOWN_ENCODINGS)}"
        )
    import tiktoken

    try:
        encoding = tiktoken.get_encoding(name)
    except Exception as error:  # pragma: no cover - needs a real network failure
        # Deliberately broad. tiktoken raises whatever its transport raised, and
        # the caller's question is the same whichever it was: the vocabulary is
        # not here, and no figure computed with it would be reproducible.
        raise VocabularyUnavailable(
            f"the encoding {name!r} could not be loaded: {error}. This is the "
            "failure ADR-003 is about, and the default tokeniser has it by design"
        ) from error
    return PretrainedEncoder(name=name, _encoding=encoding)
