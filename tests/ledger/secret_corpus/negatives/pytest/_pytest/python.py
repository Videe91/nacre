# mypy: allow-untyped-defs
"""Python test discovery, setup and run of test functions."""

from __future__ import annotations

import abc
from collections import Counter
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
import dataclasses
import enum
import fnmatch
from functools import partial
import inspect
import itertools
import os
from pathlib import Path
import re
import textwrap
import types
from typing import Any
from typing import cast
from typing import final
from typing import Literal
from typing import NoReturn
from typing import TYPE_CHECKING
import warnings

import _pytest
from _pytest import fixtures
from _pytest import nodes
from _pytest._code import filter_traceback
from _pytest._code import getfslineno
from _pytest._code.code import ExceptionInfo
from _pytest._code.code import TerminalRepr
from _pytest._code.code import Traceback
from _pytest._io.saferepr import saferepr
from _pytest.compat import ascii_escaped
from _pytest.compat import get_default_arg_names
from _pytest.compat import get_real_func
from _pytest.compat import getimfunc
from _pytest.compat import is_async_function
from _pytest.compat import NOTSET
from _pytest.compat import safe_getattr
from _pytest.compat import safe_isclass
from _pytest.config import Config
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.fixtures import _resolve_args_directness
from _pytest.fixtures import FixtureDef
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import FixtureValue
from _pytest.fixtures import FuncFixtureInfo
from _pytest.fixtures import get_scope_node
from _pytest.main import Session
from _pytest.mark import ParameterSet
from _pytest.mark.structures import _HiddenParam
from _pytest.mark.structures import get_unpacked_marks
from _pytest.mark.structures import HIDDEN_PARAM
from _pytest.mark.structures import Mark
from _pytest.mark.structures import MarkDecorator
from _pytest.mark.structures import normalize_mark_list
from _pytest.outcomes import fail
from _pytest.outcomes import skip
from _pytest.pathlib import fnmatch_ex
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportPathMismatchError
from _pytest.pathlib import scandir
from _pytest.scope import Scope
from _pytest.scope import ScopeName
from _pytest.stash import StashKey
from _pytest.warning_types import PytestCollectionWarning
from _pytest.warning_types import PytestReturnNotNoneWarning


if TYPE_CHECKING:
    from typing_extensions import Self


def pytest_addoption(parser: Parser) -> None:
    parser.addini(
        "python_files",
        type="args",
        # NOTE: default is also used in AssertionRewritingHook.
        default=["test_*.py", "*_test.py"],
        help="Glob-style file patterns for Python test module discovery",
    )
    parser.addini(
        "python_classes",
        type="args",
        default=["Test"],
        help="Prefixes or glob names for Python test class discovery",
    )
    parser.addini(
        "python_functions",
        type="args",
        default=["test"],
        help="Prefixes or glob names for Python test function and method discovery",
    )
    parser.addini(
        "disable_test_id_escaping_and_forfeit_all_rights_to_community_support",
        type="bool",
        default=False,
        help="Disable string escape non-ASCII characters, might cause unwanted "
        "side effects(use at your own risk)",
    )
    parser.addini(
        "strict_parametrization_ids",
        type="bool",
        # None => fallback to `strict`.
        default=None,
        help="Emit an error if non-unique parameter set IDs are detected",
    )


def pytest_generate_tests(metafunc: Metafunc) -> None:
    for marker in metafunc.definition.iter_markers(name="parametrize"):
        metafunc.parametrize(*marker.args, **marker.kwargs, _param_mark=marker)


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers",
        "parametrize(argnames, argvalues): call a test function multiple "
        "times passing in different arguments in turn. argvalues generally "
        "needs to be a list of values if argnames specifies only one name "
        "or a list of tuples of values if argnames specifies multiple names. "
        "Example: @parametrize('arg1', [1,2]) would lead to two calls of the "
        "decorated test function, one with arg1=1 and another with arg1=2."
        "see https://docs.pytest.org/en/stable/how-to/parametrize.html for more info "
        "and examples.",
    )
    config.addinivalue_line(
        "markers",
        "usefixtures(fixturename1, fixturename2, ...): mark tests as needing "
        "all of the specified fixtures. see "
        "https://docs.pytest.org/en/stable/explanation/fixtures.html#usefixtures ",
    )


def async_fail(nodeid: str) -> None:
    msg = (
        "async def functions are not natively supported.\n"
        "You need to install a suitable plugin for your async framework, for example:\n"
        "  - anyio\n"
        "  - pytest-asyncio\n"
        "  - pytest-tornasync\n"
        "  - pytest-trio\n"
        "  - pytest-twisted"
    )
    fail(msg, pytrace=False)


@hookimpl(trylast=True)
def pytest_pyfunc_call(pyfuncitem: Function) -> object | None:
    testfunction = pyfuncitem.obj
    if is_async_function(testfunction):
        async_fail(pyfuncitem.nodeid)
    funcargs = pyfuncitem.funcargs
    testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames}
    result = testfunction(**testargs)
    if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
        async_fail(pyfuncitem.nodeid)
    elif result is not None:
        warnings.warn(
            PytestReturnNotNoneWarning(
                f"Test functions should return None, but {pyfuncitem.nodeid} returned {type(result)!r}.\n"
                "Did you mean to use `assert` instead of `return`?\n"
                "See https://docs.pytest.org/en/stable/how-to/assert.html#return-not-none for more information."
            )
        )
    return True


def pytest_collect_directory(
    path: Path, parent: nodes.Collector
) -> nodes.Collector | None:
    pkginit = path / "__init__.py"
    try:
        has_pkginit = pkginit.is_file()
    except PermissionError:
        # See https://github.com/pytest-dev/pytest/issues/12120#issuecomment-2106349096.
        return None
    if has_pkginit:
        return Package.from_parent(parent, path=path)
    return None


def pytest_collect_file(file_path: Path, parent: nodes.Collector) -> Module | None:
    if file_path.suffix == ".py":
        if not parent.session.isinitpath(file_path):
            if not path_matches_patterns(
                file_path, parent.config.getini("python_files")
            ):
                return None
        ihook = parent.session.gethookproxy(file_path)
        module: Module = ihook.pytest_pycollect_makemodule(
            module_path=file_path, parent=parent
        )
        return module
    return None


def path_matches_patterns(path: Path, patterns: Iterable[str]) -> bool:
    """Return whether path matches any of the patterns in the list of globs given."""
    return any(fnmatch_ex(pattern, path) for pattern in patterns)


def pytest_pycollect_makemodule(module_path: Path, parent) -> Module:
    return Module.from_parent(parent, path=module_path)


@hookimpl(trylast=True)
def pytest_pycollect_makeitem(
    collector: Module | Class, name: str, obj: object
) -> None | nodes.Item | nodes.Collector | list[nodes.Item | nodes.Collector]:
    assert isinstance(collector, Class | Module), type(collector)
    # Nothing was collected elsewhere, let's do it here.
    if safe_isclass(obj):
        if collector.istestclass(obj, name):
            return Class.from_parent(collector, name=name, obj=obj)
    elif collector.istestfunction(obj, name):
        # mock seems to store unbound methods (issue473), normalize it.
        obj = getattr(obj, "__func__", obj)
        # We need to try and unwrap the function if it's a functools.partial
        # or a functools.wrapped.
        # We mustn't if it's been wrapped with mock.patch (python 2 only).
        if not (inspect.isfunction(obj) or inspect.isfunction(get_real_func(obj))):
            filename, lineno = getfslineno(obj)
            warnings.warn_explicit(
                message=PytestCollectionWarning(
                    f"cannot collect {name!r} because it is not a function."
                ),
                category=None,
                filename=str(filename),
                lineno=lineno + 1,
            )
        elif getattr(obj, "__test__", True):
            if inspect.isgeneratorfunction(obj):
                fail(
                    f"'yield' keyword is allowed in fixtures, but not in tests ({name})",
                    pytrace=False,
                )
            return list(collector._genfunctions(name, obj))
        return None
    return None


class PyobjMixin(nodes.Node):
    """this mix-in inherits from Node to carry over the typing information

    as its intended to always mix in before a node
    its position in the mro is unaffected"""

    _ALLOW_MARKERS = True

    @property
    def module(self):
        """Python module object this node was collected from (can be None)."""
        node = self.getparent(Module)
        return node.obj if node is not None else None

    @property
    def cls(self):
        """Python class object this node was collected from (can be None)."""
        node = self.getparent(Class)
        return node.obj if node is not None else None

    @property
    def instance(self):
        """Python instance object the function is bound to.

        Returns None if not a test method, e.g. for a standalone test function,
        a class or a module.
        """
        # Overridden by Function.
        return None

    @property
    def obj(self):
        """Underlying Python object."""
        obj = getattr(self, "_obj", None)
        if obj is None:
            self._obj = obj = self._getobj()
            # XXX evil hack
            # used to avoid Function marker duplication
            if self._ALLOW_MARKERS:
                self.own_markers.extend(get_unpacked_marks(self.obj))
                # This assumes that `obj` is called before there is a chance
                # to add custom keys to `self.keywords`, so no fear of overriding.
                self.keywords.update((mark.name, mark) for mark in self.own_markers)
        return obj

    @obj.setter
    def obj(self, value):
        self._obj = value

    def _getobj(self):
        """Get the underlying Python object. May be overwritten by subclasses."""
        # TODO: Improve the type of `parent` such that assert/ignore aren't needed.
        assert self.parent is not None
        obj = self.parent.obj  # type: ignore[attr-defined]
        return getattr(obj, self.name)

    def getmodpath(self, stopatmodule: bool = True, includemodule: bool = False) -> str:
        """Return Python path relative to the containing module."""
        parts = []
        for node in self.iter_parents():
            name = node.name
            if isinstance(node, Module):
                name = os.path.splitext(name)[0]
                if stopatmodule:
                    if includemodule:
                        parts.append(name)
                    break
            parts.append(name)
        parts.reverse()
        return ".".join(parts)

    def reportinfo(self) -> tuple[os.PathLike[str] | str, int | None, str]:
        # XXX caching?
        path, lineno = getfslineno(self.obj)
        modpath = self.getmodpath()
        return path, lineno, modpath


# As an optimization, these builtin attribute names are pre-ignored when
# iterating over an object during collection -- the pytest_pycollect_makeitem
# hook is not called for them.
# fmt: off
class _EmptyClass: pass  # noqa: E701
IGNORED_ATTRIBUTES = frozenset.union(
    frozenset(),
    # Module.
    dir(types.ModuleType("empty_module")),
    # Some extra module attributes the above doesn't catch.
    {"__builtins__", "__file__", "__cached__"},
    # Class.
    dir(_EmptyClass),
    # Instance.
    dir(_EmptyClass()),
)
del _EmptyClass
# fmt: on


class PyCollector(PyobjMixin, nodes.Collector, abc.ABC):
    def funcnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_functions", name)

    def isnosetest(self, obj: object) -> bool:
        """Look for the __test__ attribute, which is applied by the
        @nose.tools.istest decorator.
        """
        # We explicitly check for "is True" here to not mistakenly treat
        # classes with a custom __getattr__ returning something truthy (like a
        # function) as test classes.
        return safe_getattr(obj, "__test__", False) is True

    def classnamefilter(self, name: str) -> bool:
        return self._matches_prefix_or_glob_option("python_classes", name)

    def istestfunction(self, obj: object, name: str) -> bool:
        if self.funcnamefilter(name) or self.isnosetest(obj):
            if isinstance(obj, staticmethod | classmethod):
                # staticmethods and classmethods need to be unwrapped.
                obj = safe_getattr(obj, "__func__", False)
            return callable(obj) and fixtures.getfixturemarker(obj) is None
        else:
            return False

    def istestclass(self, obj: object, name: str) -> bool:
        if not (self.classnamefilter(name) or self.isnosetest(obj)):
            return False
        if inspect.isabstract(obj):
            return False
        return True

    def _matches_prefix_or_glob_option(self, option_name: str, name: str) -> bool:
        """Check if the given name matches the prefix or glob-pattern defined
        in configuration."""
        for option in self.config.getini(option_name):
            if name.startswith(option):
                return True
            # Check that name looks like a glob-string before calling fnmatch
            # because this is called for every name in each collected module,
            # and fnmatch is somewhat expensive to call.
            elif ("*" in option or "?" in option or "[" in option) and fnmatch.fnmatch(
                name, option
            ):
                return True
        return False

    def collect(self) -> Iterable[nodes.Item | nodes.Collector]:
        if not getattr(self.obj, "__test__", True):
            return []

        # Avoid random getattrs and peek in the __dict__ instead.
        dicts = [getattr(self.obj, "__dict__", {})]
        if isinstance(self.obj, type):
            for basecls in self.obj.__mro__:
                dicts.append(basecls.__dict__)

        # In each class, nodes should be definition ordered.
        # __dict__ is definition ordered.
        seen: set[str] = set()
        dict_values: list[list[nodes.Item | nodes.Collector]] = []
        collect_imported_tests = self.session.config.getini("collect_imported_tests")
        ihook = self.ihook
        for dic in dicts:
            values: list[nodes.Item | nodes.Collector] = []
            # Note: seems like the dict can change during iteration -
            # be careful not to remove the list() without consideration.
            for name, obj in list(dic.items()):
                if name in IGNORED_ATTRIBUTES:
                    continue
                if name in seen:
                    continue
                seen.add(name)

                if not collect_imported_tests and isinstance(self, Module):
                    # Do not collect functions and classes from other modules.
                    if inspect.isfunction(obj) or inspect.isclass(obj):
                        if obj.__module__ != self._getobj().__name__:
                            continue

                res = ihook.pytest_pycollect_makeitem(
                    collector=self, name=name, obj=obj
                )
                if res is None:
                    continue
                elif isinstance(res, list):
                    values.extend(res)
                else:
