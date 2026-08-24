"""A byte pair encoding tokeniser, trained here rather than downloaded.

Every published token count in this repository is produced by this file, so it is
worth saying why it exists rather than a `pip install` of somebody's vocabulary.

The environment this was built in cannot reach the hosts that serve pretrained
vocabularies. That could have been a limitation to apologise for; here it turned
out to be the better arrangement, because a cost attribution repository whose
token counts depend on a downloaded artefact has made every number a statement
about a file nobody in the reader's position can inspect. A vocabulary trained
from the committed corpus, by an algorithm written out in this file, is
reproducible from a clean clone with no network at all, and a reader who doubts a
token count can follow the merges.

The algorithm is the standard one and the implementation is deliberately plain.
Train: start from bytes, repeatedly find the most frequent adjacent pair in the
corpus, and merge it. Encode: apply the learned merges in the order they were
learned. Two details are not incidental.

Ties in the merge frequency are broken by the pair itself, in lexicographic order,
rather than by whichever the dictionary happened to yield first. Without that the
vocabulary depends on dictionary iteration order, which is stable within a Python
version and is not a property anybody should rely on. With it, training is a pure
function of the corpus and the target size.

Encoding applies merges by rank in a loop over the whole word, rather than
scanning left to right and taking the first applicable merge. Those two produce
different token sequences, and the rank ordered one is what every BPE
implementation in production does, so a prefix shared by two strings tokenises
identically in both. That property is what the whole repository is built on: if
two requests share a text prefix, they must share a token prefix, or a prefix
cache would be measuring something other than what it claims.

The optional path to a real vocabulary is in `vocabularies.py`, written and never
run here, and ADR-003 states exactly what that costs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

from .errors import UsageError


# Words are split on this boundary before training and before encoding, so a
# merge can never cross a space. Real tokenisers do the same thing with a regex
# over unicode categories; this corpus is ASCII by construction, so a split that
# keeps the leading space attached to the word is enough and is far easier to
# check by eye. The leading space matters: " the" and "the" are different tokens
# in every production vocabulary, and collapsing them would understate the token
# count of ordinary prose.
def split_words(text: str) -> list[str]:
    words: list[str] = []
    current = ""
    for character in text:
        if character == " ":
            if current:
                words.append(current)
            current = " "
        else:
            current += character
    if current:
        words.append(current)
    return words


@dataclass(frozen=True)
class Vocabulary:
    """A learned merge list, plus the token table it implies.

    `merges` is ordered: index 0 was learned first and is applied first. `ranks`
    is the same thing as a lookup, carried rather than recomputed because encoding
    consults it once per adjacent pair per iteration.
    """

    merges: tuple[tuple[str, str], ...]
    tokens: tuple[str, ...]
    ranks: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.tokens:
            raise UsageError("a vocabulary with no tokens cannot encode anything")
        object.__setattr__(self, "ranks", {pair: index for index, pair in enumerate(self.merges)})

    @property
    def size(self) -> int:
        return len(self.tokens)

    def as_dict(self) -> dict[str, object]:
        return {
            "size": self.size,
            "merges": len(self.merges),
            "first_merges": ["".join(pair) for pair in self.merges[:12]],
        }


def train(corpus: list[str], target_size: int) -> Vocabulary:
    """Learn a vocabulary of `target_size` tokens from `corpus`.

    Returns as soon as the target is reached or no pair occurs more than once,
    whichever comes first. A corpus with nothing left to merge produces a smaller
    vocabulary than asked for, and that is reported by `Vocabulary.size` rather
    than padded, because padding it would invent tokens that never occur.
    """
    if target_size < 1:
        raise UsageError(f"a vocabulary needs at least one token, got {target_size}")

    word_counts: Counter[str] = Counter()
    for document in corpus:
        word_counts.update(split_words(document))
    if not word_counts:
        raise UsageError("cannot train a vocabulary on an empty corpus")

    # Each word as a tuple of symbols, starting from single characters, with the
    # count of how often that word appears. Merging operates on this table rather
    # than on the corpus, which is what makes training fast enough to do on every
    # run instead of committing a vocabulary file.
    words = {tuple(word): count for word, count in word_counts.items()}
    alphabet = sorted({symbol for word in words for symbol in word})
    tokens = list(alphabet)
    merges: list[tuple[str, str]] = []

    while len(tokens) < target_size:
        pairs: Counter[tuple[str, str]] = Counter()
        for symbols, count in words.items():
            for left, right in pairwise(symbols):
                pairs[(left, right)] += count
        if not pairs:  # pragma: no cover - unreachable while any word has two symbols
            # No adjacent pair anywhere. Reachable only from a corpus of
            # single character words, which the empty corpus guard above and the
            # `best_count < 2` stop below already cover between them.
            break
        best_count = max(pairs.values())
        if best_count < 2:
            break
        # Lexicographic tie break, so training is a pure function of the corpus
        # rather than of dictionary iteration order.
        best = min(pair for pair, count in pairs.items() if count == best_count)
        merged = "".join(best)
        merges.append(best)
        tokens.append(merged)
        words = {_apply(symbols, best, merged): count for symbols, count in words.items()}

    return Vocabulary(merges=tuple(merges), tokens=tuple(tokens))


def _apply(symbols: tuple[str, ...], pair: tuple[str, str], merged: str) -> tuple[str, ...]:
    out: list[str] = []
    index = 0
    while index < len(symbols):
        if index < len(symbols) - 1 and symbols[index] == pair[0] and symbols[index + 1] == pair[1]:
            out.append(merged)
            index += 2
        else:
            out.append(symbols[index])
            index += 1
    return tuple(out)


def encode(text: str, vocabulary: Vocabulary) -> tuple[str, ...]:
    """Encode text to tokens, applying merges in the order they were learned.

    By rank rather than left to right. The two differ, and the rank ordered one is
    what production tokenisers do, which is the version under which a shared text
    prefix produces a shared token prefix.
    """
    tokens: list[str] = []
    for word in split_words(text):
        tokens.extend(_encode_word(word, vocabulary))
    return tuple(tokens)


def _encode_word(word: str, vocabulary: Vocabulary) -> list[str]:
    symbols = list(word)
    if len(symbols) < 2:
        return symbols
    while True:
        best_rank: int | None = None
        best_index = -1
        for index, pair in enumerate(pairwise(symbols)):
            rank = vocabulary.ranks.get(pair)
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank, best_index = rank, index
        if best_rank is None:
            return symbols
        symbols[best_index : best_index + 2] = ["".join(symbols[best_index : best_index + 2])]
        if len(symbols) == 1:
            return symbols


def count(text: str, vocabulary: Vocabulary) -> int:
    return len(encode(text, vocabulary))
