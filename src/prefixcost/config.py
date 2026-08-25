"""Load the attribution policy, and refuse the values that make a number meaningless.

Two refusals are load bearing rather than defensive.

More prompt families than tenants is refused, because it produces a workload in
which no prefix is shared across tenants and the attribution question this
repository is about does not arise. The tool would run and report that every
scheme agrees, which is true and is a statement about the configuration.

A material share shift below the measured floor is refused for the same reason a
churn threshold has to sit above its noise floor: a threshold under it calls every
workload unstable and therefore says nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import PolicyError

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "configs" / "policy.yaml"

CACHE_POLICIES = ("lru", "oracle", "none")
SCHEMES = ("per_request", "marginal", "equal_split", "proportional", "shapley")
# Schemes whose allocation is a function of the workload alone, so two orderings
# of the same requests must produce identical shares. Not a claim about quality:
# a scheme can be order independent and still unfair. It is the property the
# stability experiment grades against, and it is exact rather than approximate.
ORDER_INDEPENDENT = ("per_request", "equal_split", "proportional", "shapley")


@dataclass(frozen=True)
class VocabularyPolicy:
    target_size: int


@dataclass(frozen=True)
class WorkloadPolicy:
    tenants: int
    prompt_families: int
    conversations_per_tenant: int
    turns_per_conversation: int
    mean_output_tokens: int

    @property
    def requests(self) -> int:
        return self.tenants * self.conversations_per_tenant * self.turns_per_conversation


@dataclass(frozen=True)
class CachePolicy:
    capacity_tokens: int
    policy: str


@dataclass(frozen=True)
class PricingPolicy:
    prefill_per_token: float
    decode_per_token: float


@dataclass(frozen=True)
class AttributionPolicy:
    default_scheme: str
    material_share_shift: float
    replications: int


@dataclass(frozen=True)
class Policy:
    vocabulary: VocabularyPolicy
    workload: WorkloadPolicy
    cache: CachePolicy
    pricing: PricingPolicy
    attribution: AttributionPolicy
    source: str = field(default="<defaults>")

    @property
    def resolution_floor(self) -> float:
        """The smallest non zero rate the replication count can express."""
        return 1.0 / self.attribution.replications


def _require(mapping: dict, key: str, kind: type, where: str):
    if key not in mapping:
        raise PolicyError(f"{where} is missing the required key {key!r}")
    value = mapping[key]
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise PolicyError(f"{where}.{key} must be {kind.__name__}, got {value!r}")
    return value


def policy_from_mapping(raw: dict, *, source: str = "<mapping>") -> Policy:
    vocabulary_raw = _require(raw, "vocabulary", dict, "policy")
    workload_raw = _require(raw, "workload", dict, "policy")
    cache_raw = _require(raw, "cache", dict, "policy")
    pricing_raw = _require(raw, "pricing", dict, "policy")
    attribution_raw = _require(raw, "attribution", dict, "policy")

    vocabulary = VocabularyPolicy(
        target_size=_require(vocabulary_raw, "target_size", int, "vocabulary")
    )
    workload = WorkloadPolicy(
        tenants=_require(workload_raw, "tenants", int, "workload"),
        prompt_families=_require(workload_raw, "prompt_families", int, "workload"),
        conversations_per_tenant=_require(
            workload_raw, "conversations_per_tenant", int, "workload"
        ),
        turns_per_conversation=_require(workload_raw, "turns_per_conversation", int, "workload"),
        mean_output_tokens=_require(workload_raw, "mean_output_tokens", int, "workload"),
    )
    if workload.tenants < 2:
        raise PolicyError("an attribution among fewer than two tenants is not an attribution")
    if workload.prompt_families > workload.tenants:
        raise PolicyError(
            f"workload.prompt_families is {workload.prompt_families} and there are "
            f"{workload.tenants} tenants. With more families than tenants no prefix is "
            "shared between tenants, every scheme agrees, and the agreement is a "
            "property of the configuration rather than a finding"
        )
    if workload.prompt_families < 1:
        raise PolicyError("a workload needs at least one prompt family")
    if workload.turns_per_conversation < 1 or workload.conversations_per_tenant < 1:
        raise PolicyError("a tenant needs at least one conversation of at least one turn")

    cache = CachePolicy(
        capacity_tokens=_require(cache_raw, "capacity_tokens", int, "cache"),
        policy=str(_require(cache_raw, "policy", str, "cache")),
    )
    if cache.policy not in CACHE_POLICIES:
        raise PolicyError(
            f"unknown cache policy {cache.policy!r}, expected one of {list(CACHE_POLICIES)}"
        )
    if cache.capacity_tokens < 0:
        raise PolicyError("a cache capacity cannot be negative")

    pricing = PricingPolicy(
        prefill_per_token=_require(pricing_raw, "prefill_per_token", float, "pricing"),
        decode_per_token=_require(pricing_raw, "decode_per_token", float, "pricing"),
    )
    if pricing.prefill_per_token < 0 or pricing.decode_per_token < 0:
        raise PolicyError("a price per token cannot be negative")

    attribution = AttributionPolicy(
        default_scheme=str(_require(attribution_raw, "default_scheme", str, "attribution")),
        material_share_shift=_require(
            attribution_raw, "material_share_shift", float, "attribution"
        ),
        replications=_require(attribution_raw, "replications", int, "attribution"),
    )
    if attribution.default_scheme not in SCHEMES:
        raise PolicyError(
            f"unknown scheme {attribution.default_scheme!r}, expected one of {list(SCHEMES)}"
        )
    if attribution.replications < 1:
        raise PolicyError("attribution.replications must be at least 1")

    return Policy(
        vocabulary=vocabulary,
        workload=workload,
        cache=cache,
        pricing=pricing,
        attribution=attribution,
        source=source,
    )


def _display_path(resolved: Path) -> str:
    """How a policy path is named in reports, which end up in committed images.

    Relative to the repository when the file is inside it, absolute otherwise. An
    absolute path is correct and is also a photograph of somebody's home
    directory: the first screenshots taken for the README carried the full build
    path of the machine that made them, which tells a reader nothing and dates
    the image the moment the checkout moves.
    """
    try:
        return str(resolved.relative_to(DEFAULT_POLICY_PATH.parents[1]))
    except ValueError:
        return str(resolved)


def load_policy(path: str | os.PathLike[str] | None = None) -> Policy:
    resolved = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not resolved.is_file():
        raise PolicyError(f"policy file not found: {resolved}")
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file {resolved} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"policy file {resolved} must contain a mapping at the top level")
    return policy_from_mapping(raw, source=_display_path(resolved.resolve()))
