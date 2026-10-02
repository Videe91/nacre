"""
Functionality: Narrow the candidate pool by identity addresses: conjunctive exact matching, relaxed one level at a time
  (most specific first) until the pool is large enough, with every relaxation recorded.
Owns: the address format and its levels, the conjunctive match, the relaxation order and the min_pool rule, and the
  exact-match count that feeds the entity channel (R15).
Public entry: narrow_by_identity(), Narrowed, LEVELS, MIN_POOL
Decisions: D-0025
Assumptions: A-0038
Notes: D-0025 §3. Addresses are typed strings "<type>:<value>" with type in {file, code, entity, system, cluster,
  domain}; matching is exact on the whole string (after strip; types are lower-cased). Addresses only ever come from
  the caller's request and from capture (never inferred by a model).
  - With no request addresses the pool is returned whole (relaxations empty).
  - Relaxation order (D1; D-0025 names code -> system -> cluster -> domain -> global and does not place file and
    entity): file and code first (the most specific), then entity, system, cluster, domain; after the last, global
    (no address constraint).
  - `exact_matches[version_event_id]` counts the ORIGINAL request addresses a candidate matches, before relaxation:
    it is the entity channel's score.
  Pure function: no database, no keys; addresses come from the decrypted index entries (R10).
"""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

LEVELS: tuple[tuple[str, ...], ...] = (("file", "code"), ("entity",), ("system",), ("cluster",), ("domain",))
TYPES = frozenset(t for level in LEVELS for t in level)
MIN_POOL = 16


class AddressError(ValueError):
    """An address is not "<type>:<value>" with a known type."""


@dataclass(frozen=True)
class Narrowed:
    version_event_ids: tuple[UUID, ...]                # in the pool's order
    relaxations: tuple[str, ...]                       # level names dropped, in order ("file+code", ...)
    exact_matches: Mapping[UUID, int]


def _norm(address: str) -> str:
    kind, sep, value = address.strip().partition(":")
    if not sep or kind.lower() not in TYPES or not value.strip():
        raise AddressError(f"address must be '<type>:<value>' with type in {sorted(TYPES)}: {address!r}")
    return f"{kind.lower()}:{value.strip()}"


def narrow_by_identity(pool: Sequence[UUID], addresses_of: Mapping[UUID, Iterable[str]],
                       request: Sequence[str], *, min_pool: int = MIN_POOL) -> Narrowed:
    """Narrow `pool` (version event ids) using each candidate's addresses and the request's addresses."""
    want = [_norm(a) for a in request]
    have = {v: {_norm(a) for a in addresses_of.get(v, ())} for v in pool}
    exact = {v: sum(1 for a in set(want) if a in have[v]) for v in pool}
    if not want:
        return Narrowed(tuple(pool), (), exact)
    active, relaxed = list(dict.fromkeys(want)), []
    levels = list(LEVELS)
    while True:
        kept = [v for v in pool if all(a in have[v] for a in active)]
        if len(kept) >= min_pool or not active:
            return Narrowed(tuple(kept), tuple(relaxed), exact)
        while levels and not any(a.split(":", 1)[0] in levels[0] for a in active):
            levels.pop(0)                              # skip levels the request does not use
        if not levels:                                 # nothing left to relax: global
            return Narrowed(tuple(pool), tuple(relaxed + ["global"]), exact)
        dropped = levels.pop(0)
        active = [a for a in active if a.split(":", 1)[0] not in dropped]
        relaxed.append("+".join(dropped))
        if not active:
            return Narrowed(tuple(pool), tuple(relaxed + ["global"]), exact)
