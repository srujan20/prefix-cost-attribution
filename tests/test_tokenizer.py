"""The tokeniser, and the one property the whole repository depends on.

If two strings share a text prefix and their token sequences do not share a token
prefix, a prefix cache is measuring something other than what it claims, and every
number downstream is about a fiction. That is the first test here and it is the
most valuable one in the file.
"""

from __future__ import annotations

import pytest

from prefixcost.errors import UsageError
from prefixcost.tokenizer import count, encode, split_words, train

CORPUS = [
    "the quick brown fox jumps over the lazy dog",
    "the quick brown fox is quick and brown",
    "a lazy dog sleeps while the quick fox runs",
    "policy one cite the document policy two prefer the later date",
]


@pytest.fixture
def vocabulary():
    return train(CORPUS, 120)


def test_a_shared_text_prefix_gives_a_shared_token_prefix(vocabulary):
    """The property a prefix cache needs, asserted rather than assumed."""
    base = "the quick brown fox"
    left = encode(base + " jumps over the lazy dog", vocabulary)
    right = encode(base + " is quick and brown", vocabulary)
    expected = encode(base, vocabulary)
    assert left[: len(expected)] == expected
    assert right[: len(expected)] == expected


def test_training_is_a_pure_function_of_the_corpus():
    """No dictionary iteration order anywhere, which the tie break is there for."""
    first = train(CORPUS, 120)
    second = train(list(CORPUS), 120)
    assert first.merges == second.merges
    assert first.tokens == second.tokens


def test_the_vocabulary_never_exceeds_its_target():
    assert train(CORPUS, 40).size <= 40


def test_a_corpus_with_nothing_left_to_merge_stops_early():
    """Reported as a smaller vocabulary rather than padded with invented tokens."""
    vocabulary = train(["ab"], 500)
    assert vocabulary.size < 500


def test_encoding_round_trips_to_the_original_text(vocabulary):
    text = "the quick brown fox jumps over the lazy dog"
    assert "".join(encode(text, vocabulary)) == text


def test_encoding_is_deterministic(vocabulary):
    text = "policy one cite the document"
    assert encode(text, vocabulary) == encode(text, vocabulary)


def test_more_merges_never_lengthen_the_encoding():
    """A larger vocabulary can only merge more, so the token count cannot rise."""
    text = "the quick brown fox jumps over the lazy dog"
    small = count(text, train(CORPUS, 60))
    large = count(text, train(CORPUS, 200))
    assert large <= small


def test_words_keep_their_leading_space():
    """Collapsing " the" into "the" would understate the token count of prose."""
    assert split_words("the quick fox") == ["the", " quick", " fox"]
    assert split_words(" leading") == [" leading"]


def test_an_empty_corpus_is_refused():
    with pytest.raises(UsageError, match="empty corpus"):
        train([], 100)


def test_a_target_below_one_is_refused():
    with pytest.raises(UsageError, match="at least one token"):
        train(CORPUS, 0)


def test_a_single_character_word_needs_no_merging(vocabulary):
    assert encode("a", vocabulary) == ("a",)
