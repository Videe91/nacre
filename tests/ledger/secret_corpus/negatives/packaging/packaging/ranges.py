# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""Public :class:`VersionRange` API.

A set-algebra view of the versions accepted by a
:class:`~packaging.specifiers.SpecifierSet`. Ranges support intersection,
union, complement, and difference; membership and filtering match the
originating specifier set; and conversion back to a
:class:`~packaging.specifiers.SpecifierSet` is available where a PEP 440 form
exists.

.. testsetup::

    from packaging.ranges import VersionRange
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
"""

from __future__ import annotations

import enum
import typing
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    Union,
)

from ._ranges import (
    FULL_RANGE,
    MIN_VERSION,
    NEG_INF,
    POS_INF,
    BoundaryKind,
    BoundaryVersion,
    LowerBound,
    UpperBound,
    coerce_version,
    filter_by_ranges,
    intersect_ranges,
    least_version_above,
    matches_bounds_only,
    range_is_empty,
    ranges_are_prerelease_only,
    trim_release,
)
from .version import Version

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from ._ranges import Interval
    from .specifiers import SpecifierSet


__all__ = ["VersionRange"]

T = TypeVar("T")
UnparsedVersion = Union[Version, str]
UnparsedVersionVar = TypeVar("UnparsedVersionVar", bound=UnparsedVersion)

#: The most ``!=`` exclusion fragments (``!=V`` points or ``!=P.*`` prefixes)
#: that :meth:`VersionRange.to_specifier_set` will materialize to spell a
#: single gap or run. Every site that expands a version-number-driven chain
#: charges it against this cap, and chains that spell one gap together share
#: it (see :func:`_decompose_dev0_gap` and :func:`_encode_gap`),
#: so no gap ever materializes more than this many exclusions. Past the cap
#: the recovery returns ``None`` rather than emit the unbounded chain a range
#: such as ``==5.* | ==1000000.*`` would otherwise drive.
_MAX_EXCLUSION_RUN = 128


class _SetOp(enum.Enum):
    """The binary set operation ``_combine_literals`` resolves over ``===`` literals."""

    INTERSECTION = enum.auto()
    UNION = enum.auto()
    DIFFERENCE = enum.auto()


def __dir__() -> list[str]:
    return __all__


# Range algebra: intersection and the empty-interval test live in the engine
# (``intersect_ranges`` / ``range_is_empty``); union and complement are only
# needed here, so they live in this module.


def _union_ranges(
    left: Sequence[Interval],
    right: Sequence[Interval],
) -> list[Interval]:
    """Union two sorted, non-overlapping interval lists.

    A linear merge over the two pre-sorted inputs followed by a single
    coalescing pass: adjacent or overlapping intervals collapse so the result
    is itself sorted and non-overlapping.
    """
    if not left:
        return list(right)
    if not right:
        return list(left)

    merged_input: list[Interval] = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        if left[left_index][0] <= right[right_index][0]:
            merged_input.append(left[left_index])
            left_index += 1
        else:
            merged_input.append(right[right_index])
            right_index += 1
    merged_input.extend(left[left_index:])
    merged_input.extend(right[right_index:])

    merged: list[Interval] = [merged_input[0]]
    for lower, upper in merged_input[1:]:
        prev_lower, prev_upper = merged[-1]

        if (
            prev_upper.version is None
            or lower.version is None
            or prev_upper.version > lower.version
        ):
            overlaps = True
        elif prev_upper.version == lower.version:
            overlaps = prev_upper.inclusive or lower.inclusive
        else:
            # An ordering gap may still hold no version when the two bounds
            # straddle a synthetic boundary; merge across an empty gap to
            # stay canonical.
            gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
            gap_upper = UpperBound(lower.version, not lower.inclusive)
            overlaps = range_is_empty(gap_lower, gap_upper)

        if overlaps:
            merged[-1] = (prev_lower, max(prev_upper, upper))
        else:
            merged.append((lower, upper))

    return merged


def _complement_ranges(ranges: Sequence[Interval]) -> list[Interval]:
    """Complement a sorted, non-overlapping interval list.

    Yields the gaps between intervals plus a leading gap before the first and
    a trailing gap after the last. Bound inclusivity flips so that
    complement-of-complement round-trips back to the input.
    """
    if not ranges:
        return list(FULL_RANGE)

    result: list[Interval] = []
    prev_upper: UpperBound | None = None

    for lower, upper in ranges:
        if prev_upper is None:
            # Leading gap below the first interval. Every range reaching here is
            # floor-canonical: ``_canonical_floor`` has already folded an
            # inclusive lower at or below ``0.dev0`` into ``-inf``. So a finite
            # first lower always leaves a non-empty gap down to ``-inf``, while a
            # ``-inf`` lower leaves no leading gap at all.
            if lower.version is not None:
                gap_upper = UpperBound(lower.version, not lower.inclusive)
                result.append((NEG_INF, gap_upper))
        else:
            gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
            gap_upper = UpperBound(lower.version, not lower.inclusive)
            # Input intervals are canonical (sorted, disjoint, non-touching),
            # so the gap between two of them always holds at least one version.
            result.append((gap_lower, gap_upper))
        prev_upper = upper

    # The empty-input early return guarantees the loop ran.
    assert prev_upper is not None
    if prev_upper.version is not None:
        gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
        result.append((gap_lower, POS_INF))

    return result


def _canonical_floor(bounds: tuple[Interval, ...]) -> tuple[Interval, ...]:
    """Collapse the PEP 440 floor in a sorted interval list.

    Only the first interval can touch ``0.dev0`` (the minimum version). An
    inclusive lower at or below it admits everything below, the same as
    ``-inf``, so ``>=0.dev0`` becomes the one canonical full range. An
    exclusive upper at or below it leaves the interval empty, so it is dropped.
    """
    if not bounds:
        return bounds

    lower, upper = bounds[0]
    if range_is_empty(NEG_INF, upper):
        return bounds[1:]

    if (
        lower.inclusive
        and isinstance(lower.version, Version)
        and lower.version <= MIN_VERSION
    ):
        return ((NEG_INF, upper), *bounds[1:])

    return bounds


def _predecessor_boundary(version: Version) -> BoundaryVersion | None:
    """The boundary whose least successor is *version*, or ``None``.

    Inverse of :func:`~packaging._ranges.least_version_above`. A plain version
    that is exactly such a successor (``1.0a2.dev0`` sits just above
    ``AFTER_POSTS(1.0a1)``) folds back to that boundary, so ``>=1.0a2.dev0`` and
    ``>1.0a1`` share one form. The proposed boundary is confirmed by
    round-tripping through ``least_version_above``.
    """
    # Only a least successor carries a dev segment, so nothing else can fold.
    if version.dev is None:
        return None

    candidate: BoundaryVersion | None = None
    if version.pre is not None and version.dev == 0 and version.post is None:
        # 1.0a2.dev0 -> AFTER_POSTS(1.0a1)
        kind, number = version.pre
        if number >= 1:
            candidate = BoundaryVersion(
                version.__replace__(pre=(kind, number - 1), dev=None),
                BoundaryKind.AFTER_POSTS,
            )
    elif version.dev >= 1:
        # 1.0.dev3 -> AFTER_LOCALS(1.0.dev2)
        candidate = BoundaryVersion(
            version.__replace__(dev=version.dev - 1), BoundaryKind.AFTER_LOCALS
        )
    elif version.dev == 0 and version.post is not None:
        # 1.0.post1.dev0 -> AFTER_LOCALS(1.0.post0); 1.0.post0.dev0 -> AFTER_LOCALS(1.0)
        base = (
            version.__replace__(post=None, dev=None)
            if version.post == 0
            else version.__replace__(post=version.post - 1, dev=None)
        )
        candidate = BoundaryVersion(base, BoundaryKind.AFTER_LOCALS)

    if candidate is not None and least_version_above(candidate) == version:
        return candidate
    return None


def _canonicalize(bounds: tuple[Interval, ...]) -> tuple[Interval, ...]:
    """Fold least-successor bounds to their boundary form.

    ``>=1.0a2.dev0`` and ``>1.0a1`` denote the same set, so both must reduce to
    one representation for ``==`` and ``hash`` to agree. An inclusive lower or
    exclusive upper sitting on a boundary's least successor becomes that
    boundary; the engine's emptiness check has already dropped the synthetic
    gaps such intervals would otherwise leave.
    """
    result: list[Interval] = []
    for lower, upper in bounds:
        new_lower, new_upper = lower, upper

        if isinstance(lower.version, Version) and lower.inclusive:
            boundary = _predecessor_boundary(lower.version)
            if boundary is not None:
                new_lower = LowerBound(boundary, inclusive=False)

        if isinstance(upper.version, Version) and not upper.inclusive:
            boundary = _predecessor_boundary(upper.version)
            if boundary is not None:
                new_upper = UpperBound(boundary, inclusive=True)

        result.append((new_lower, new_upper))
    return tuple(result)


def _struct_admits(
    bounds: tuple[Interval, ...], admit_arbitrary: bool, literal: str
) -> bool:
    """True when the bounds (plus arbitrary admission) admit ``literal``.

    Skips the explicit admit/reject sets, which the caller layers on top. A
    non-version string matches via ``admit_arbitrary`` only on full bounds;
    on narrower bounds the flag is metadata only.
    """
    parsed = coerce_version(literal)
    if parsed is None:
        return admit_arbitrary and bounds == FULL_RANGE

    return matches_bounds_only(bounds, parsed)


# Repr helpers:


def _bound_version_str(value: BoundaryVersion | Version) -> str:
    """Printout for a bound's inner value, kind-tagged for boundaries."""
    if isinstance(value, BoundaryVersion):
        return f"{value.version}[{value.kind.name}]"
    return str(value)


def _format_lower(bound: LowerBound) -> str:
    if bound.version is None:
        return "(-inf"
    bracket = "[" if bound.inclusive else "("
    return f"{bracket}{_bound_version_str(bound.version)}"


def _format_upper(bound: UpperBound) -> str:
    if bound.version is None:
        return "+inf)"
    bracket = "]" if bound.inclusive else ")"
    return f"{_bound_version_str(bound.version)}{bracket}"


def _format_intervals(intervals: Sequence[Interval]) -> str:
    """Render a sorted interval list as ``lower, upper | lower, upper``."""
    return " | ".join(
        f"{_format_lower(lower)}, {_format_upper(upper)}" for lower, upper in intervals
    )


# ``to_specifier_set`` recovery: encode a range's interval list back into
# specifier fragments. Each helper returns ``None`` when its shape has no
# PEP 440 form. The ``keep_dev0`` argument threaded through is a spelling
# mode, not a pre-release policy: false emits a prerelease-free form (no
# synthetic ``.dev0``, so the recovered range has an empty opt-in region),
# true keeps the ``.dev0`` markers (so the range opts its bounds in).
# ``to_specifier_set`` encodes in both modes and keeps whichever round-trips.
#
# Bound and interval encoding: turn one interval's bounds into fragments.


def _is_dev0_version(version: Version) -> bool:
    """True when version is exactly ``X[.Y]*.dev0`` (the shape ``<X`` makes)."""
    return (
        version.dev == 0
        and version.pre is None
        and version.post is None
        and version.local is None
    )


def _clean_lower(version: Version) -> list[str] | None:
    """A prerelease-free spelling for an inclusive ``[version`` lower, or ``None``.

    Several ``[V`` lowers come from an operator whose own spelling carries no
    synthetic ``.dev0``. Recovering that spelling gives the range an empty opt-in
    region, so it is offered in the prerelease-free spelling mode (see
    :meth:`VersionRange.to_specifier_set`).
    """
    if version.dev != 0 or version.pre is not None or version.local is not None:
        return None

    # ``[B.post(k).dev0`` is the lower ``>B.post(k-1)`` builds (k >= 1).
    if version.post is not None:
        if version.post < 1:
            return None
        return [f">{version.__replace__(post=version.post - 1, dev=None)}"]

    # ``[F.dev0`` is family F's base. The prefix P just below F has
    # ``==P.* == [P.dev0, F.dev0)``, so ``>=P,!=P.*`` lands exactly on ``[F.dev0``.
    family = trim_release(version.release)
    last = family[-1]
    if last < 1:
        return None

    below_release = (*family[:-1], last - 1)
    below = Version.from_parts(epoch=version.epoch, release=below_release)

    # At the epoch-0 floor ``==P.*`` already reaches ``0.dev0``, so the ``>=P``
    # half is redundant: ``[1.dev0, +inf)`` is plain ``!=0.*``.
    if version.epoch == 0 and not any(below_release):
        return [f"!={below}.*"]

    return [f">={below}", f"!={below}.*"]


def _epoch_floor_lower(
    lower: LowerBound, upper: UpperBound
) -> tuple[Version, int, bool] | None:
    """The ``E!0`` family of a lower sitting on an epoch>0 zero-family floor.

    An epoch>0 zero-family base such as ``1!0.dev0`` has no ``>=P,!=P.*`` spelling
    since no version sorts below ``E!0`` within the epoch. While the interval
    stays within ``==E!0.*`` it is that wildcard, trimmed by the upper and with a
    leading ``.dev`` run excluded: an ``AFTER_LOCALS(E!0.dev(k))`` lower drops
    ``E!0.dev0..E!0.dev(k)``, a plain inclusive ``E!0.dev0`` lower drops none.
    Returns the ``E!0`` family, how many leading ``.dev`` releases to exclude, and
    whether the upper sits at the family cap (so ``==E!0.*`` needs no upper), else
    ``None``.
    """
    version = lower.version
    if isinstance(version, BoundaryVersion):
        if version.kind != BoundaryKind.AFTER_LOCALS:
            return None
        version = version.version
        if version.dev is None:
            return None
        excluded_devs = version.dev + 1
    elif isinstance(version, Version) and lower.inclusive:
        # A plain inclusive lower only reaches the floor as ``>=E!0.dev0``;
        # higher ``.dev`` would have canonicalized to an AFTER_LOCALS boundary.
        if version.dev != 0:
            return None
        excluded_devs = 0
    else:
        return None

    # Only the bare ``E!0`` floor of a non-zero epoch qualifies.
    if version.epoch == 0:
        return None
    if version.pre is not None or version.post is not None or version.local is not None:
        return None
    if any(trim_release(version.release)):
        return None

    # ``==E!0.*`` spans ``[E!0.dev0, E!1.dev0)``; it fits only below that cap.
    next_family = Version.from_parts(epoch=version.epoch, release=(1,), dev=0)
    cap = UpperBound(next_family, False)
    if upper > cap:
        return None

    family = Version.from_parts(epoch=version.epoch, release=(0,))
    return family, excluded_devs, upper == cap


def _dev_family_anchor(family: Version) -> list[str] | None:
    """Prerelease-free fragments for ``[family, ..)``, or ``None`` if it has none.

    ``family`` is an ``X.dev0``. The floor gives ``[]`` (every version); a release
    base its ``_clean_lower`` family-floor spelling (``!=0.*`` ...); an ``X.post0``
    base ``>=X,!=X``. A pre-release base has no prerelease-free spelling.
    """
    if family <= MIN_VERSION:
        return []
    clean = _clean_lower(family)
    if clean is not None:
        return clean
    if family.pre is None and family.post == 0:
        base = family.__replace__(post=None, dev=None)
        return [f">={base}", f"!={base}"]
    return None


