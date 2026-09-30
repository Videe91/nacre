# mypy: allow-untyped-defs
from __future__ import annotations

import abc
from collections import defaultdict
from collections import deque
from collections import OrderedDict
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
import dataclasses
import functools
import inspect
import os
from pathlib import Path
import sys
import types
from typing import Any
from typing import cast
from typing import Final
from typing import final
from typing import Generic
from typing import Literal
from typing import NoReturn
from typing import overload
from typing import TYPE_CHECKING
from typing import TypeVar
import warnings

import _pytest
from _pytest import nodes
from _pytest._code import getfslineno
from _pytest._code import Source
from _pytest._code.code import ExceptionInfoFormatter
from _pytest._code.code import TerminalRepr
from _pytest._io import TerminalWriter
from _pytest.compat import assert_never
from _pytest.compat import deprecated
from _pytest.compat import get_real_func
from _pytest.compat import getfuncargnames
from _pytest.compat import getimfunc
from _pytest.compat import getlocation
from _pytest.compat import NOTSET
from _pytest.compat import NotSetType
from _pytest.compat import safe_getattr
from _pytest.compat import safe_isclass
from _pytest.compat import signature
from _pytest.config import _PluggyPlugin
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.deprecated import CLASS_FIXTURE_INSTANCE_METHOD
from _pytest.deprecated import FIXTURE_BASEID_DEPRECATED
from _pytest.deprecated import FIXTURE_GETFIXTUREVALUE_DURING_TEARDOWN
from _pytest.deprecated import FIXTURE_NODEID_DEPRECATED
from _pytest.deprecated import FIXTUREDEF_HAS_LOCATION_DEPRECATED
from _pytest.deprecated import PARSEFACTORIES_NODEID_DEPRECATED
from _pytest.deprecated import YIELD_FIXTURE
from _pytest.main import Session
from _pytest.mark import Mark
from _pytest.mark import ParameterSet
from _pytest.mark.structures import MarkDecorator
from _pytest.outcomes import fail
from _pytest.outcomes import skip
from _pytest.outcomes import TEST_OUTCOME
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath
from _pytest.scope import HIGH_SCOPES
from _pytest.scope import Scope
from _pytest.scope import ScopeName
from _pytest.warning_types import PytestWarning


if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup


if TYPE_CHECKING:
    from _pytest.python import CallSpec2
    from _pytest.python import Function
    from _pytest.python import Metafunc
    from _pytest.reports import CollectReport


# The value of the fixture -- return/yield of the fixture function (type variable).
FixtureValue = TypeVar("FixtureValue", covariant=True)
# The type of the fixture function (type alias).
FixtureFunction = Callable[..., object]
# The type of a fixture function (type alias generic in fixture value).
_FixtureFunc = Callable[..., FixtureValue] | Callable[..., Generator[FixtureValue]]
# The type of FixtureDef.cached_result (type alias generic in fixture value).
_FixtureCachedResult = (
    tuple[
        # The result.
        FixtureValue,
        # Cache key.
        object,
        None,
    ]
    | tuple[
        None,
        # Cache key.
        object,
        # The exception and the original traceback.
        tuple[BaseException, types.TracebackType | None],
    ]
)


def pytest_sessionstart(session: Session) -> None:
    session._fixturemanager = FixtureManager(session)


def get_scope_package(
    node: nodes.Item,
    fixturedef: FixtureDef[object],
) -> nodes.Node | None:
    from _pytest.python import Package

    for parent in node.iter_parents():
        if isinstance(parent, Package):
            if fixturedef.node is not None:
                if parent == fixturedef.node:
                    return parent
            else:
                if parent.nodeid == fixturedef.baseid:
                    return parent
    return node.session


def is_visibility_more_specific(
    candidate: FixtureDef[Any], other: FixtureDef[Any]
) -> bool:
    """Return whether the visibility of ``candidate`` is strictly more specific
    than that of ``other``, i.e. ``candidate`` is defined on a strict descendant
    in the collection tree of where ``other`` is defined."""
    if candidate.node is None or other.node is None:
        # Fallback for fixtures registered with a string nodeid (deprecated).
        # In this case compare baseids, which are nodeid prefixes.
        # This branch can be removed once baseid deprecation is done (pytest 10).
        if candidate.baseid == other.baseid:
            return False
        if other.baseid == "":
            return True
        # `candidate.baseid` must continue with a node separator for it to be a
        # true descendant.
        return candidate.baseid.startswith(other.baseid) and candidate.baseid[
            len(other.baseid)
        ] in ("/", ":")

    return (
        candidate.node is not other.node and other.node in candidate.node.iter_parents()
    )


def get_scope_node(node: nodes.Node, scope: Scope) -> nodes.Node | None:
    """Get the closest parent node (including self) which matches the given
    scope.

    If there is no parent node for the scope (e.g. asking for class scope on a
    Module, or on a Function when not defined in a class), returns None.
    """
    import _pytest.python

    if scope is Scope.Function:
        # Type ignored because this is actually safe, see:
        # https://github.com/python/mypy/issues/4717
        return node.getparent(nodes.Item)  # type: ignore[type-abstract]
    elif scope is Scope.Class:
        return node.getparent(_pytest.python.Class)
    elif scope is Scope.Module:
        return node.getparent(_pytest.python.Module)
    elif scope is Scope.Package:
        return node.getparent(_pytest.python.Package)
    elif scope is Scope.Session:
        return node.getparent(_pytest.main.Session)
    else:
        assert_never(scope)


# TODO: Try to use FixtureFunctionDefinition instead of the marker
def getfixturemarker(obj: object) -> FixtureFunctionMarker | None:
    """Return fixturemarker or None if it doesn't exist"""
    if isinstance(obj, FixtureFunctionDefinition):
        return obj._fixture_function_marker
    return None


# Algorithm for sorting on a per-parametrized resource setup basis.
# It is called for Session scope first and performs sorting
# down to the lower scopes such as to minimize number of "high scope"
# setups and teardowns.


@dataclasses.dataclass(frozen=True)
class ParamArgKey:
    """A key for a high-scoped parameter used by an item.

    For use as a hashable key in `reorder_items`. The combination of fields
    is meant to uniquely identify a particular "instance" of a param,
    potentially shared by multiple items in a scope.
    """

    #: The param name.
    argname: str
    param_index: int
    #: For scopes Package, Module, Class, the path to the file (directory in
    #: Package's case) of the package/module/class where the item is defined.
    scoped_item_path: Path | None
    #: For Class scope, the class where the item is defined.
    item_cls: type | None


_V = TypeVar("_V")
OrderedSet = dict[_V, None]


def get_param_argkeys(item: nodes.Item, scope: Scope) -> Iterator[ParamArgKey]:
    """Return all ParamArgKeys for item matching the specified high scope."""
    assert scope is not Scope.Function

    try:
        callspec: CallSpec2 = item.callspec  # type: ignore[attr-defined]
    except AttributeError:
        return

    item_cls = None
    if scope is Scope.Session:
        scoped_item_path = None
    elif scope is Scope.Package:
        # Package key = module's directory.
        scoped_item_path = item.path.parent
    elif scope is Scope.Module:
        scoped_item_path = item.path
    elif scope is Scope.Class:
        scoped_item_path = item.path
        item_cls = item.cls  # type: ignore[attr-defined]
    else:
        assert_never(scope)

    for argname in callspec.indices:
        if callspec._arg2scope[argname] != scope:
            continue
        param_index = callspec.indices[argname]
        yield ParamArgKey(argname, param_index, scoped_item_path, item_cls)


def reorder_items(items: Sequence[nodes.Item]) -> list[nodes.Item]:
    argkeys_by_item: dict[Scope, dict[nodes.Item, OrderedSet[ParamArgKey]]] = {}
    items_by_argkey: dict[Scope, dict[ParamArgKey, OrderedDict[nodes.Item, None]]] = {}
    for scope in HIGH_SCOPES:
        scoped_argkeys_by_item = argkeys_by_item[scope] = {}
        scoped_items_by_argkey = items_by_argkey[scope] = defaultdict(OrderedDict)
        for item in items:
            argkeys = dict.fromkeys(get_param_argkeys(item, scope))
            if argkeys:
                scoped_argkeys_by_item[item] = argkeys
                for argkey in argkeys:
                    scoped_items_by_argkey[argkey][item] = None

    items_set = dict.fromkeys(items)
    return list(
        reorder_items_atscope(
            items_set, argkeys_by_item, items_by_argkey, Scope.Session
        )
    )


def reorder_items_atscope(
    items: OrderedSet[nodes.Item],
    argkeys_by_item: Mapping[Scope, Mapping[nodes.Item, OrderedSet[ParamArgKey]]],
    items_by_argkey: Mapping[
        Scope, Mapping[ParamArgKey, OrderedDict[nodes.Item, None]]
    ],
    scope: Scope,
) -> OrderedSet[nodes.Item]:
    if scope is Scope.Function or len(items) < 3:
        return items

    scoped_items_by_argkey = items_by_argkey[scope]
    scoped_argkeys_by_item = argkeys_by_item[scope]

    ignore: set[ParamArgKey] = set()
    items_deque = deque(items)
    items_done: OrderedSet[nodes.Item] = {}
    while items_deque:
        no_argkey_items: OrderedSet[nodes.Item] = {}
        slicing_argkey = None
        while items_deque:
            item = items_deque.popleft()
            if item in items_done or item in no_argkey_items:
                continue
            argkeys = dict.fromkeys(
                k for k in scoped_argkeys_by_item.get(item, ()) if k not in ignore
            )
            if not argkeys:
                no_argkey_items[item] = None
            else:
                slicing_argkey, _ = argkeys.popitem()
                # We don't have to remove relevant items from later in the
                # deque because they'll just be ignored.
                matching_items = [
                    i for i in scoped_items_by_argkey[slicing_argkey] if i in items
                ]
                for i in reversed(matching_items):
                    items_deque.appendleft(i)
                    # Fix items_by_argkey order.
                    for other_scope in HIGH_SCOPES:
                        other_scoped_items_by_argkey = items_by_argkey[other_scope]
                        for argkey in argkeys_by_item[other_scope].get(i, ()):
                            argkey_dict = other_scoped_items_by_argkey[argkey]
                            if not hasattr(sys, "pypy_version_info"):
                                argkey_dict[i] = None
                                argkey_dict.move_to_end(i, last=False)
                            else:
                                # Work around a bug in PyPy:
                                # https://github.com/pypy/pypy/issues/5257
                                # https://github.com/pytest-dev/pytest/issues/13312
                                bkp = argkey_dict.copy()
                                argkey_dict.clear()
                                argkey_dict[i] = None
                                argkey_dict.update(bkp)
                break
        if no_argkey_items:
            reordered_no_argkey_items = reorder_items_atscope(
                no_argkey_items, argkeys_by_item, items_by_argkey, scope.next_lower()
            )
            items_done.update(reordered_no_argkey_items)
        if slicing_argkey is not None:
            ignore.add(slicing_argkey)
    return items_done


def traverse_fixture_closure(
    initialnames: Iterable[str],
    *,
    getfixturedefs: Callable[[str], Sequence[FixtureDef[Any]] | None],
) -> Iterator[str]:
    """Statically traverse the fixture dependency closure in DFS order starting
    from initialnames, yielding all requested fixture names (argnames).

    Each argname is only yielded once.
    """
    # Track the index for each fixture name in the simulated stack.
    # Needed for handling override chains correctly, similar to
    # FixtureRequest._get_active_fixturedef.
    # Using negative indices: -1 is the most specific (last), -2 is second to
    # last, etc.
    current_indices: dict[str, int] = {}

    def process_argname(argname: str) -> Iterator[str]:
        index = current_indices.get(argname)

        # Optimization: already processed this argname.
        if index == -1:
            return

        # Only yield each argname once.
        if index is None:
            yield argname
            current_indices[argname] = -1

        fixturedefs = getfixturedefs(argname)
        if not fixturedefs:
            return

        index = current_indices.get(argname, -1)
        if -index > len(fixturedefs):
            # Exhausted the override chain (will error during runtest).
            return
        fixturedef = fixturedefs[index]

        current_indices[argname] = index - 1
        for dep in fixturedef.argnames:
            yield from process_argname(dep)
        current_indices[argname] = index

    for argname in initialnames:
        yield from process_argname(argname)


@dataclasses.dataclass(frozen=True)
class FuncFixtureInfo:
    """Fixture-related information for a fixture-requesting item (e.g. test
    function).

    This is used to examine the fixtures which an item requests statically
    (known during collection). This includes autouse fixtures, fixtures
    requested by the `usefixtures` marker, fixtures requested in the function
    parameters, and the transitive closure of these.

    An item may also request fixtures dynamically (using `request.getfixturevalue`);
    these are not reflected here.
    """

    __slots__ = ("argnames", "initialnames", "name2fixturedefs", "names_closure")

    # Fixture names that the item requests directly by function parameters.
    argnames: tuple[str, ...]
    # Fixture names that the item immediately requires. These include
    # argnames + fixture names specified via usefixtures and via autouse=True in
    # fixture definitions.
    initialnames: tuple[str, ...]
    # The transitive closure of the fixture names that the item requires.
    # Note: can't include dynamic dependencies (`request.getfixturevalue` calls).
    names_closure: list[str]
    # A map from a fixture name in the transitive closure to the FixtureDefs
    # matching the name which are applicable to this function.
    # There may be multiple overriding fixtures with the same name. The
    # sequence is ordered from furthest to closes to the function.
    name2fixturedefs: dict[str, Sequence[FixtureDef[Any]]]

    def prune_dependency_tree(self) -> None:
        """Recompute names_closure from initialnames and name2fixturedefs.

        Can only reduce names_closure, which means that the new closure will
        always be a subset of the old one. The order is preserved.

        This method is needed because direct parametrization may shadow some
        of the fixtures that were included in the originally built dependency
        tree. In this way the dependency tree can get pruned, and the closure
        of argnames may get reduced.
        """
        closure = set(
            traverse_fixture_closure(
                self.initialnames,
                getfixturedefs=self.name2fixturedefs.get,
            )
        )
        self.names_closure[:] = (name for name in self.names_closure if name in closure)


class FixtureRequest(abc.ABC):
    """The type of the ``request`` fixture.

    A request object gives access to the requesting test context and has a
