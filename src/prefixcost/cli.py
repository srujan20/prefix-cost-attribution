"""The commands, and the exit codes they carry.

Exit codes are the interface. A billing check that prints a table and returns zero
is a report; one that returns 2 when the scheme it was asked about is not a
division of the bill is a build step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .audit import audit
from .config import SCHEMES, load_policy
from .errors import PolicyError, PrefixCostError, UnanswerableError, UsageError
from .report import html_report, json_report, text_report
from .serving import serve
from .trie import build_trie
from .workload import build_workload

EXIT_UNANSWERABLE = 3
EXIT_USAGE = 4


def command_plan(args: argparse.Namespace) -> int:
    """What this configuration will build, before it builds it."""
    policy = load_policy(args.policy)
    print(f"prefixcost {__version__}, policy from {policy.source}")
    print()
    print(
        f"workload: {policy.workload.tenants} tenants over "
        f"{policy.workload.prompt_families} prompt families, "
        f"{policy.workload.requests} requests"
    )
    print(f"cache:    {policy.cache.capacity_tokens} tokens, {policy.cache.policy} eviction")
    from .vocabularies import pretrained_available

    trained = f"trained here, target {policy.vocabulary.target_size} tokens"
    optional = "available" if pretrained_available() else "not installed"
    print(f"tokens:   {trained}. Pretrained alternative: {optional}")
    print(
        f"pricing:  {policy.pricing.prefill_per_token} per prefill token, "
        f"{policy.pricing.decode_per_token} per decode token"
    )
    print()
    print("schemes, and the properties each one has")
    from .attribution import ALLOCATORS
    from .config import ORDER_INDEPENDENT

    for name in SCHEMES:
        independent = "order independent" if name in ORDER_INDEPENDENT else "order DEPENDENT"
        summary = ALLOCATORS[name].__doc__.splitlines()[0]
        print(f"  {name:<14}{independent:<20}{summary}")
    return 0


def command_audit(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    workload = build_workload(policy, seed=args.seed)
    result = audit(workload, policy, scheme=args.scheme, orderings_count=args.orderings)
    print(text_report(result, policy), end="")
    if args.html:
        Path(args.html).write_text(html_report(result, policy), encoding="utf-8")
        print(f"\nwrote {args.html}")
    if args.json:
        Path(args.json).write_text(json_report(result, policy), encoding="utf-8")
        print(f"wrote {args.json}")
    return result.verdict.exit_code


def command_cache(args: argparse.Namespace) -> int:
    """Serve the workload at several capacities and print what the cache bought."""
    policy = load_policy(args.policy)
    workload = build_workload(policy, seed=args.seed)
    trie = build_trie(workload.requests)
    print(f"prompt tokens {workload.prompt_tokens}, distinct prefix tokens {trie.nodes}")
    print(f"  {'capacity':>10}{'policy':>9}{'prefill':>10}{'hit':>9}{'evictions':>11}")
    for capacity in args.capacities:
        for name in ("lru", "oracle") if args.oracle else ("lru",):
            served = serve(workload, policy, capacity_tokens=capacity, cache_policy=name, trie=trie)
            print(
                f"  {capacity:>10}{name:>9}{served.prefill_tokens:>10}"
                f"{served.hit_share:>9.4f}{served.evictions:>11}"
            )
    print()
    print(
        "  a capacity at or above the distinct prefix token count evicts nothing, and "
        "the prefill then equals that count exactly"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prefixcost",
        description="Attribute the cost of LLM serving with a prefix cache.",
    )
    parser.add_argument("--version", action="version", version=f"prefixcost {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="what this configuration will build")
    plan.add_argument("--policy", help="path to a policy YAML file")
    plan.set_defaults(handler=command_plan)

    audit_parser = subparsers.add_parser("audit", help="allocate one workload five ways")
    audit_parser.add_argument("--seed", type=int, default=11)
    audit_parser.add_argument("--scheme", default=None, choices=list(SCHEMES))
    audit_parser.add_argument(
        "--orderings",
        type=int,
        default=12,
        help="arrival orders to test stability over",
    )
    audit_parser.add_argument("--policy", help="path to a policy YAML file")
    audit_parser.add_argument("--html", help="write the HTML report here")
    audit_parser.add_argument("--json", help="write a JSON summary here")
    audit_parser.set_defaults(handler=command_audit)

    cache = subparsers.add_parser("cache", help="what the prefix cache buys, by capacity")
    cache.add_argument("--seed", type=int, default=11)
    cache.add_argument("--capacities", type=int, nargs="+", default=[0, 2000, 8000, 32000, 200000])
    cache.add_argument("--oracle", action="store_true", help="also run the unimplementable policy")
    cache.add_argument("--policy", help="path to a policy YAML file")
    cache.set_defaults(handler=command_cache)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except UnanswerableError as error:
        print(f"cannot answer: {error}", file=sys.stderr)
        return EXIT_UNANSWERABLE
    except (UsageError, PolicyError) as error:
        print(f"usage: {error}", file=sys.stderr)
        return EXIT_USAGE
    except PrefixCostError as error:  # pragma: no cover - defensive
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
