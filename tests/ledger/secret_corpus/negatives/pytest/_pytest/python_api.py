# mypy: allow-untyped-defs
from __future__ import annotations

import builtins
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence
from collections.abc import Sized
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
import math
from numbers import Complex
import pprint
import sys
from typing import Any
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from numpy import ndarray


def _compare_approx(
    full_object: object,
    message_data: Sequence[tuple[str, str, str]],
    number_of_elements: int,
    different_ids: Sequence[object],
    max_abs_diff: float,
    max_rel_diff: float,
) -> list[str]:
    message_list = list(message_data)
    message_list.insert(0, ("Index", "Obtained", "Expected"))
    max_sizes = [0, 0, 0]
    for index, obtained, expected in message_list:
        max_sizes[0] = max(max_sizes[0], len(index))
        max_sizes[1] = max(max_sizes[1], len(obtained))
        max_sizes[2] = max(max_sizes[2], len(expected))
    explanation = [
        f"comparison failed. Mismatched elements: {len(different_ids)} / {number_of_elements}:",
        f"Max absolute difference: {max_abs_diff}",
        f"Max relative difference: {max_rel_diff}",
    ] + [
        f"{indexes:<{max_sizes[0]}} | {obtained:<{max_sizes[1]}} | {expected:<{max_sizes[2]}}"
        for indexes, obtained, expected in message_list
    ]
    return explanation


# builtin pytest.approx helper


class ApproxBase:
    """Provide shared utilities for making approximate comparisons between
    numbers or sequences of numbers."""

    # Tell numpy to use our `__eq__` operator instead of its.
    __array_ufunc__ = None
    __array_priority__ = 100

    def __init__(self, expected, rel=None, abs=None, nan_ok: bool = False) -> None:
        __tracebackhide__ = True
        self.expected = expected
        self.abs = abs
        self.rel = rel
        self.nan_ok = nan_ok
        self._check_type()

    def __repr__(self) -> str:
        raise NotImplementedError

    def _repr_compare(self, other_side: Any) -> list[str]:
        return [
            "comparison failed",
            f"Obtained: {other_side}",
            f"Expected: {self}",
        ]

    def __eq__(self, actual) -> bool:
        return all(
            a == self._approx_scalar(x) for a, x in self._yield_comparisons(actual)
        )

    def __bool__(self):
        __tracebackhide__ = True
        raise AssertionError(
            "approx() is not supported in a boolean context.\nDid you mean: `assert a == approx(b)`?"
        )

    # Ignore type because of https://github.com/python/mypy/issues/4266.
    __hash__ = None  # type: ignore

    def __ne__(self, actual) -> bool:
        return not (actual == self)

    def _approx_scalar(self, x) -> ApproxBase:
        if isinstance(x, Decimal):
            return ApproxDecimal(x, rel=self.rel, abs=self.abs, nan_ok=self.nan_ok)
        if isinstance(x, (datetime, timedelta)):
            return ApproxTimedelta(x, rel=self.rel, abs=self.abs, nan_ok=self.nan_ok)
        return ApproxScalar(x, rel=self.rel, abs=self.abs, nan_ok=self.nan_ok)

    def _yield_comparisons(self, actual):
        """Yield all the pairs of numbers to be compared.

        This is used to implement the `__eq__` method.
        """
        raise NotImplementedError

    def _check_type(self) -> None:
        """Raise a TypeError if the expected value is not a valid type."""
        # This is only a concern if the expected value is a sequence.  In every
        # other case, the approx() function ensures that the expected value has
        # a numeric type.  For this reason, the default is to do nothing.  The
        # classes that deal with sequences should reimplement this method to
        # raise if there are any non-numeric elements in the sequence.


def _recursive_sequence_map(f, x):
    """Recursively map a function over a sequence of arbitrary depth"""
    if isinstance(x, list | tuple):
        seq_type = type(x)
        return seq_type(_recursive_sequence_map(f, xi) for xi in x)
    elif _is_sequence_like(x):
        return [_recursive_sequence_map(f, xi) for xi in x]
    else:
        return f(x)


class ApproxNumpy(ApproxBase):
    """Perform approximate comparisons where the expected value is numpy array."""

    def __repr__(self) -> str:
        list_scalars = _recursive_sequence_map(
            self._approx_scalar, self.expected.tolist()
        )
        return f"approx({list_scalars!r})"

    def _repr_compare(self, other_side: ndarray | list[Any]) -> list[str]:
        import itertools
        import math

        def get_value_from_nested_list(
            nested_list: list[Any], nd_index: tuple[Any, ...]
        ) -> Any:
            """
            Helper function to get the value out of a nested list, given an n-dimensional index.
            This mimics numpy's indexing, but for raw nested python lists.
            """
            value: Any = nested_list
            for i in nd_index:
                value = value[i]
            return value

        np_array_shape = self.expected.shape
        approx_side_as_seq = _recursive_sequence_map(
            self._approx_scalar, self.expected.tolist()
        )

        # convert other_side to numpy array to ensure shape attribute is available
        other_side_as_array = _as_numpy_array(other_side)
        assert other_side_as_array is not None

        if np_array_shape != other_side_as_array.shape:
            return [
                "Impossible to compare arrays with different shapes.",
                f"Shapes: {np_array_shape} and {other_side_as_array.shape}",
            ]

        number_of_elements = self.expected.size
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids = []
        for index in itertools.product(*(range(i) for i in np_array_shape)):
            approx_value = get_value_from_nested_list(approx_side_as_seq, index)
            other_value = get_value_from_nested_list(other_side_as_array, index)
            if approx_value != other_value:
                abs_diff = abs(approx_value.expected - other_value)
                max_abs_diff = max(max_abs_diff, abs_diff)
                if other_value == 0.0:
                    max_rel_diff = math.inf
                else:
                    max_rel_diff = max(max_rel_diff, abs_diff / abs(other_value))
                different_ids.append(index)

        message_data = [
            (
                str(index),
                str(get_value_from_nested_list(other_side_as_array, index)),
                str(get_value_from_nested_list(approx_side_as_seq, index)),
            )
            for index in different_ids
        ]
        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual) -> bool:
        import numpy as np

        # self.expected is supposed to always be an array here.

        if not np.isscalar(actual):
            try:
                actual = np.asarray(actual)
            except Exception as e:
                raise TypeError(f"cannot compare '{actual}' to numpy.ndarray") from e

        if not np.isscalar(actual) and actual.shape != self.expected.shape:
            return False

        return super().__eq__(actual)

    def _yield_comparisons(self, actual):
        import numpy as np

        # `actual` can either be a numpy array or a scalar, it is treated in
        # `__eq__` before being passed to `ApproxBase.__eq__`, which is the
        # only method that calls this one.

        if np.isscalar(actual):
            for i in np.ndindex(self.expected.shape):
                yield actual, self.expected[i].item()
        else:
            for i in np.ndindex(self.expected.shape):
                yield actual[i].item(), self.expected[i].item()


class ApproxMapping(ApproxBase):
    """Perform approximate comparisons where the expected value is a mapping
    with numeric values (the keys can be anything)."""

    def __repr__(self) -> str:
        return f"approx({ ({k: self._approx_scalar(v) for k, v in self.expected.items()})!r})"

    def _repr_compare(self, other_side: Mapping[object, float]) -> list[str]:
        import math

        if len(self.expected) != len(other_side):
            return [
                "Impossible to compare mappings with different sizes.",
                f"Lengths: {len(self.expected)} and {len(other_side)}",
            ]

        if self.expected.keys() != other_side.keys():
            return [
                "comparison failed.",
                f"Mappings has different keys: expected {self.expected.keys()} but got {other_side.keys()}",
            ]

        approx_side_as_map = {
            k: self._approx_scalar(v) for k, v in self.expected.items()
        }

        number_of_elements = len(approx_side_as_map)
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids = []
        for approx_key, approx_value in approx_side_as_map.items():
            other_value = other_side[approx_key]
            if approx_value != other_value:
                if approx_value.expected is not None and other_value is not None:
                    try:
                        max_abs_diff = max(
                            max_abs_diff, abs(approx_value.expected - other_value)
                        )
                        if approx_value.expected == 0.0:
                            max_rel_diff = math.inf
                        else:
                            max_rel_diff = max(
                                max_rel_diff,
                                abs(
                                    (approx_value.expected - other_value)
                                    / approx_value.expected
                                ),
                            )
                    except ZeroDivisionError:
                        pass
                different_ids.append(approx_key)

        message_data = [
            (str(key), str(other_side[key]), str(approx_side_as_map[key]))
            for key in different_ids
        ]

        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual) -> bool:
        try:
            if set(actual.keys()) != set(self.expected.keys()):
                return False
        except AttributeError:
            return False

        return super().__eq__(actual)

    def _yield_comparisons(self, actual):
        for k in self.expected.keys():
            yield actual[k], self.expected[k]

    def _check_type(self) -> None:
        __tracebackhide__ = True
        for key, value in self.expected.items():
            if isinstance(value, type(self.expected)):
                msg = "pytest.approx() does not support nested dictionaries: key={!r} value={!r}\n  full mapping={}"
                raise TypeError(msg.format(key, value, pprint.pformat(self.expected)))


class ApproxSequenceLike(ApproxBase):
    """Perform approximate comparisons where the expected value is a sequence of numbers."""

    def __repr__(self) -> str:
        seq_type = type(self.expected)
        if seq_type not in (tuple, list):
            seq_type = list
        return f"approx({seq_type(self._approx_scalar(x) for x in self.expected)!r})"

    def _repr_compare(self, other_side: Sequence[float]) -> list[str]:
        import math

        if len(self.expected) != len(other_side):
            return [
                "Impossible to compare lists with different sizes.",
                f"Lengths: {len(self.expected)} and {len(other_side)}",
            ]

        approx_side_as_map = _recursive_sequence_map(self._approx_scalar, self.expected)

        number_of_elements = len(approx_side_as_map)
        max_abs_diff = -math.inf
        max_rel_diff = -math.inf
        different_ids = []
        for i, (approx_value, other_value) in enumerate(
            zip(approx_side_as_map, other_side, strict=True)
        ):
            if approx_value != other_value:
                try:
                    abs_diff = abs(approx_value.expected - other_value)
                    max_abs_diff = max(max_abs_diff, abs_diff)
                # Ignore non-numbers for the diff calculations (#13012).
                except TypeError:
                    pass
                else:
                    if other_value == 0.0:
                        max_rel_diff = math.inf
                    else:
                        max_rel_diff = max(max_rel_diff, abs_diff / abs(other_value))
                different_ids.append(i)
        message_data = [
            (str(i), str(other_side[i]), str(approx_side_as_map[i]))
            for i in different_ids
        ]

        return _compare_approx(
            self.expected,
            message_data,
            number_of_elements,
            different_ids,
            max_abs_diff,
            max_rel_diff,
        )

    def __eq__(self, actual) -> bool:
        try:
            if len(actual) != len(self.expected):
                return False
        except TypeError:
            return False
        return super().__eq__(actual)

    def _yield_comparisons(self, actual):
        return zip(actual, self.expected, strict=True)

    def _check_type(self) -> None:
        __tracebackhide__ = True
        for index, x in enumerate(self.expected):
            if isinstance(x, type(self.expected)):
                msg = "pytest.approx() does not support nested data structures: {!r} at index {}\n  full sequence: {}"
                raise TypeError(msg.format(x, index, pprint.pformat(self.expected)))


class ApproxScalar(ApproxBase):
    """Perform approximate comparisons where the expected value is a single number."""

    # Using Real should be better than this Union, but not possible yet:
    # https://github.com/python/typeshed/pull/3108
    DEFAULT_ABSOLUTE_TOLERANCE: float | Decimal = 1e-12
    DEFAULT_RELATIVE_TOLERANCE: float | Decimal = 1e-6

    def __repr__(self) -> str:
        """Return a string communicating both the expected value and the
        tolerance for the comparison being made.

        For example, ``1.0 ± 1e-6``, ``(3+4j) ± 5e-6 ∠ ±180°``.
        """
        # Don't show a tolerance for values that aren't compared using
        # tolerances, i.e. non-numerics and infinities. Need to call abs to
        # handle complex numbers, e.g. (inf + 1j).
        if (
            isinstance(self.expected, bool)
            or (not isinstance(self.expected, Complex | Decimal))
            or math.isinf(abs(self.expected) or isinstance(self.expected, bool))
        ):
            return str(self.expected)

        # If a sensible tolerance can't be calculated, self.tolerance will
        # raise a ValueError.  In this case, display '???'.
        try:
            if 1e-3 <= self.tolerance < 1e3:
                vetted_tolerance = f"{self.tolerance:n}"
            else:
                vetted_tolerance = f"{self.tolerance:.1e}"

            if (
                isinstance(self.expected, Complex)
                and self.expected.imag
                and not math.isinf(self.tolerance)
            ):
                vetted_tolerance += " ∠ ±180°"
        except ValueError:
            vetted_tolerance = "???"

        return f"{self.expected} ± {vetted_tolerance}"

    def __eq__(self, actual) -> bool:
        """Return whether the given value is equal to the expected value
        within the pre-specified tolerance."""

        def is_bool(val: Any) -> bool:
            # Check if `val` is a native bool or numpy bool.
            if isinstance(val, bool):
                return True
            if np := sys.modules.get("numpy"):
                return isinstance(val, np.bool_)
            return False

        asarray = _as_numpy_array(actual)
        if asarray is not None:
            # Call ``__eq__()`` manually to prevent infinite-recursion with
            # numpy<1.13.  See #3748.
            return all(self.__eq__(a) for a in asarray.flat)

