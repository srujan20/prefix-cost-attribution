# ADR-003: Train the tokeniser from the committed corpus rather than download a vocabulary

Status: accepted

## Context

Every token count in this repository comes from a tokeniser, so the choice of
tokeniser is upstream of every published figure.

The default choice is a pretrained vocabulary: `tiktoken`, or a Hugging Face
tokeniser, a few lines and a familiar name. The environment this repository was
built in cannot reach the hosts that serve those vocabularies, which is a
constraint rather than a preference, and it forced the question of what the
downloaded artefact would actually have bought.

The answer is: a more realistic absolute token count, and a dependency on a file
the reader cannot inspect. Every ratio in this repository is a ratio of token
counts produced by the same tokeniser, so the vocabulary cancels out of all of
them. What it does not cancel out of is reproducibility.

## Decision

`src/prefixcost/tokenizer.py` implements byte pair encoding, trains on the
committed corpus at load time, and produces **a vocabulary of 311** tokens on the
shipped configuration. A clean clone with no network reproduces every token count
in this repository.

`src/prefixcost/vocabularies.py` holds the optional path to a real vocabulary. It
is written and it has never been run here, and this record is the statement of
that rather than a comment nobody would find.

Two implementation details are load bearing and are not incidental to the choice.

Ties in merge frequency break lexicographically rather than by whichever pair the
dictionary yielded first, so training is a pure function of the corpus and the
target size rather than of dictionary iteration order, which is stable within a
Python version and is not a property anybody should build on.

Encoding applies merges by rank over the whole word rather than scanning left to
right and taking the first applicable merge. The two produce different token
sequences, and the rank ordered one is what production tokenisers do. It is also
the only one under which a shared text prefix reliably produces a shared token
prefix, which is the property this entire repository rests on: without it a
prefix cache would be measuring something other than what it claims.

## Consequences

The shipped vocabulary is smaller than the target in the policy file, because
training stops when no adjacent pair occurs more than once and this corpus runs
out of repeated pairs first. That is reported rather than padded: padding would
invent tokens that never occur.

A smaller vocabulary segments text more finely, so the absolute counts here are
larger than a production tokeniser would give on the same text. The **337,098
prompt tokens** and **24,613 distinct prefix tokens** are therefore internally
consistent and are not a prediction of what any particular provider would bill.
Every conclusion in this repository is a ratio, and the ratios are unaffected.

The corpus is also the training set, which is normally a mistake and here is
correct. This vocabulary is not a model of language, it is a deterministic
segmentation of a known corpus. Nothing is predicted, so nothing can leak.

## Alternatives rejected

**Vendor a pretrained vocabulary file into the repository.** It would work
offline. It would also add a large binary blob under somebody else's licence,
and a reader wanting to check a token count would have to trust the file rather
than follow the merges.

**Split on whitespace and count words.** Reproducible, offline, and wrong in the
way that matters: word splitting makes the shared prefix of two prompts fall on
word boundaries, which is exactly where a real tokeniser does not put it, and the
entire question here is about where two token sequences stop agreeing.

**Make the tokeniser pluggable and default to whichever is installed.** The
figures would then depend on what happened to be in the environment, so two runs
of `make verify` on two machines could legitimately publish different numbers.
The extra exists, it is opt in, and the default is the reproducible one.
