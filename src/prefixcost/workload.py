"""The request stream, and where its shared prefixes come from.

Two kinds of sharing exist in a real multi tenant deployment and the corpus has
both, because they behave differently under attribution and conflating them is
how the interesting question gets missed.

Sharing *within* a tenant comes from conversation history. Turn three of a
conversation resends turns one and two, so every turn after the first is almost
entirely a prefix of work already done. Nobody argues about who pays for that:
it is the same tenant either way.

Sharing *across* tenants comes from the system prompt. A product built on a base
template gives many tenants the same opening tokens, and with a prefix cache the
first tenant to arrive pays to process them and everyone after gets them free.
That is the case where attribution is a real question with a real answer, and it
is the case the shipped scheme gets wrong.

The generator is deterministic in its seed and offline by construction. Text is
assembled from a small template grammar rather than sampled from anything, so the
tokeniser trained on it in `tokenizer.py` has a vocabulary a reader can inspect.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Policy
from .errors import UnanswerableError
from .tokenizer import Vocabulary, encode, train

SYSTEM_TEMPLATES = (
    (
        "you are a careful assistant for the {domain} team. answer only from the "
        "documents provided. if the documents do not contain the answer say that "
        "plainly rather than guessing. keep replies short and cite the document id."
    ),
    (
        "you are an operations assistant for {domain}. every reply must name the "
        "runbook step it came from. never invent a step. if the runbook is silent "
        "escalate to a human and say who."
    ),
    (
        "you help the {domain} desk triage incoming requests. classify each request, "
        "give one sentence of reasoning, and never promise a delivery date. ask a "
        "clarifying question when the request is ambiguous."
    ),
)
DOMAINS = ("billing", "logistics", "onboarding", "compliance", "support", "research")
TOPICS = (
    "the invoice for last quarter",
    "a delayed shipment",
    "a failed payment",
    "an account migration",
    "a permissions request",
    "a duplicate record",
    "a refund that was not applied",
    "an export that timed out",
)
# A block of shared reference material pinned into the system prompt, which is
# what a retrieval augmented or policy grounded deployment actually sends. It is
# here because without it the prompts are short, prefill is a few percent of the
# bill, and the attribution question this repository is about would be an argument
# over the rounding. Long shared preambles are the reason prefix caching exists at
# all, so a corpus without one would be studying the phenomenon in the one regime
# where it does not matter.
POLICY_DOCUMENT = (
    " reference policy. one: every answer must cite the document it came from by "
    "identifier, and an answer with no identifier is not an answer. two: when two "
    "documents disagree prefer the one with the later effective date and say that "
    "you did. three: never quote a figure that does not appear in a document, and "
    "never round a figure that does. four: a request that names no account and no "
    "order cannot be actioned, so ask for one rather than guessing which was "
    "meant. five: escalate anything involving a refund above the desk limit, a "
    "regulated product, or a customer who has already been escalated once. six: "
    "if the customer asks for a delivery date, give the range in the document and "
    "say it is a range. seven: close every reply with the reference number."
)
FOLLOW_UPS = (
    "can you check whether that applies to the other account as well",
    "what should i tell the customer in the meantime",
    "is there a runbook step i skipped",
    "how long does that usually take to clear",
    "who owns that system now",
)


@dataclass(frozen=True)
class Request:
    """One request, as the tokens the server would actually process."""

    tenant: int
    conversation: int
    turn: int
    tokens: tuple[str, ...]
    output_tokens: int
    prompt_family: int

    @property
    def prompt_tokens(self) -> int:
        return len(self.tokens)


@dataclass(frozen=True)
class Workload:
    requests: tuple[Request, ...]
    vocabulary: Vocabulary
    seed: int

    def __post_init__(self) -> None:
        if not self.requests:
            raise UnanswerableError("a workload with no requests has no cost to attribute")
        if len({request.tenant for request in self.requests}) < 2:
            raise UnanswerableError(
                "every request belongs to one tenant, so there is nothing to attribute "
                "between. An attribution among one tenant is a total"
            )

    @property
    def tenants(self) -> tuple[int, ...]:
        return tuple(sorted({request.tenant for request in self.requests}))

    @property
    def prompt_tokens(self) -> int:
        return sum(request.prompt_tokens for request in self.requests)

    @property
    def output_tokens(self) -> int:
        return sum(request.output_tokens for request in self.requests)


def build_workload(policy: Policy, seed: int) -> Workload:
    """Generate the request stream and the vocabulary that tokenises it.

    The vocabulary is trained on this corpus rather than downloaded, so every
    token count in the repository is reproducible from a clean clone with no
    network. ADR-003 covers what that costs and what the alternative would have
    been.
    """
    rng = np.random.default_rng(seed)
    corpus = _corpus_text(policy)
    vocabulary = train(corpus, policy.vocabulary.target_size)

    families = [
        SYSTEM_TEMPLATES[index % len(SYSTEM_TEMPLATES)].format(domain=DOMAINS[index % len(DOMAINS)])
        + POLICY_DOCUMENT
        for index in range(policy.workload.prompt_families)
    ]

    requests: list[Request] = []
    for tenant in range(policy.workload.tenants):
        family = tenant % policy.workload.prompt_families
        system = families[family]
        for conversation in range(policy.workload.conversations_per_tenant):
            history = system
            topic = TOPICS[int(rng.integers(len(TOPICS)))]
            for turn in range(policy.workload.turns_per_conversation):
                message = (
                    f" user: {topic}"
                    if turn == 0
                    else f" user: {FOLLOW_UPS[int(rng.integers(len(FOLLOW_UPS)))]}"
                )
                history = history + message
                output = int(max(1, rng.poisson(policy.workload.mean_output_tokens)))
                requests.append(
                    Request(
                        tenant=tenant,
                        conversation=conversation,
                        turn=turn,
                        tokens=encode(history, vocabulary),
                        output_tokens=output,
                        prompt_family=family,
                    )
                )
                # The assistant's reply becomes part of the next turn's prompt,
                # which is what makes a conversation a growing prefix rather than
                # a set of independent requests. The reply text is a stand in
                # rather than generated: its content cannot matter to a cost
                # model, only its length, and pretending otherwise would invite a
                # reader to take the corpus for a language benchmark.
                history = history + f" assistant: {'ok ' * (output // 12)}".rstrip()

    return Workload(requests=tuple(requests), vocabulary=vocabulary, seed=seed)


def _corpus_text(policy: Policy) -> list[str]:
    """Everything the vocabulary is trained on, which is everything it will see.

    Training on exactly the text that will be encoded is normally a mistake, and
    here it is the correct choice for a reason worth stating: this vocabulary is
    not a model of language, it is a deterministic segmentation of a known corpus.
    Nothing is predicted, so nothing can leak.
    """
    documents = []
    for index in range(max(policy.workload.prompt_families, len(SYSTEM_TEMPLATES))):
        documents.append(
            SYSTEM_TEMPLATES[index % len(SYSTEM_TEMPLATES)].format(
                domain=DOMAINS[index % len(DOMAINS)]
            )
        )
    documents.append(POLICY_DOCUMENT)
    documents.extend(f" user: {topic}" for topic in TOPICS)
    documents.extend(f" user: {follow}" for follow in FOLLOW_UPS)
    documents.append(" assistant: ok")
    return documents


def orderings(workload: Workload, count: int, seed: int) -> list[tuple[int, ...]]:
    """Arrival orders to replay the same workload in.

    Conversations are permuted against each other and turns inside a conversation
    keep their order. That restriction is not tidiness. A permutation that put
    turn three before turn one would model a server receiving a conversation
    backwards, which does not happen, and it would flatter the cache by making
    the longest prefix arrive first. Every ordering here contains exactly the same
    requests, so the spread across them is a property of the attribution scheme
    rather than of the workload.
    """
    rng = np.random.default_rng(seed)
    blocks: dict[tuple[int, int], list[int]] = {}
    for index, request in enumerate(workload.requests):
        blocks.setdefault((request.tenant, request.conversation), []).append(index)
    keys = list(blocks)
    out: list[tuple[int, ...]] = []
    for _ in range(count):
        order = rng.permutation(len(keys)).tolist()
        out.append(tuple(index for position in order for index in blocks[keys[position]]))
    return out


def causal_order(workload: Workload) -> tuple[int, ...]:
    """The order the requests were generated in, which respects conversations.

    A permutation that put turn three before turn one would model a server
    receiving a conversation backwards, which does not happen and would flatter
    the cache. The stability experiment permutes conversations against each other
    rather than turns within one, and this is the reference it permutes from.
    """
    return tuple(range(len(workload.requests)))
