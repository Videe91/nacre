"""Rewrite assertion AST to produce nice error messages."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Sequence
import errno
import functools
import importlib.abc
import importlib.machinery
import importlib.util
import io
import itertools
import marshal
import os
from pathlib import Path
from pathlib import PurePath
import struct
import sys
import tokenize
import types
from typing import IO
from typing import TYPE_CHECKING


if sys.version_info >= (3, 12):
    from importlib.resources.abc import TraversableResources
else:
    from importlib.abc import TraversableResources
if sys.version_info < (3, 11):
    from importlib.readers import FileReader
else:
    from importlib.resources.readers import FileReader


from _pytest._io.saferepr import DEFAULT_REPR_MAX_SIZE
from _pytest._io.saferepr import saferepr
from _pytest._io.saferepr import saferepr_unlimited
from _pytest._version import version
from _pytest.assertion import util
from _pytest.config import Config
from _pytest.fixtures import FixtureFunctionDefinition
from _pytest.main import Session
from _pytest.pathlib import absolutepath
from _pytest.pathlib import fnmatch_ex
from _pytest.stash import StashKey


# fmt: off
from _pytest.assertion.util import format_explanation as _format_explanation  # noqa:F401, isort:skip
# fmt:on

if TYPE_CHECKING:
    from _pytest.assertion import AssertionState


class Sentinel:
    pass


assertstate_key = StashKey["AssertionState"]()

# pytest caches rewritten pycs in pycache dirs
PYTEST_TAG = f"{sys.implementation.cache_tag}-pytest-{version}"
PYC_EXT = ".py" + ((__debug__ and "c") or "o")
PYC_TAIL = "." + PYTEST_TAG + PYC_EXT

# Special marker that denotes we have just left a scope definition
_SCOPE_END_MARKER = Sentinel()


class AssertionRewritingHook(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """PEP302/PEP451 import hook which rewrites asserts."""

    def __init__(self, config: Config) -> None:
        self.config = config
        try:
            self.fnpats = config.getini("python_files")
        except ValueError:
            self.fnpats = ["test_*.py", "*_test.py"]
        self.session: Session | None = None
        self._rewritten_names: dict[str, Path] = {}
        self._must_rewrite: set[str] = set()
        # flag to guard against trying to rewrite a pyc file while we are already writing another pyc file,
        # which might result in infinite recursion (#3506)
        self._writing_pyc = False
        self._basenames_to_check_rewrite = {"conftest"}
        self._marked_for_rewrite_cache: dict[str, bool] = {}
        self._session_paths_checked = False

    def set_session(self, session: Session | None) -> None:
        self.session = session
        self._session_paths_checked = False

    # Indirection so we can mock calls to find_spec originated from the hook during testing
    _find_spec = importlib.machinery.PathFinder.find_spec

    def find_spec(
        self,
        name: str,
        path: Sequence[str | bytes] | None = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if self._writing_pyc:
            return None
        state = self.config.stash[assertstate_key]
        if self._early_rewrite_bailout(name, state):
            return None
        state.trace(f"find_module called for: {name}")

        # Type ignored because mypy is confused about the `self` binding here.
        spec = self._find_spec(name, path)  # type: ignore

        if (
            # the import machinery could not find a file to import
            spec is None
            # this is a namespace package (without `__init__.py`)
            # there's nothing to rewrite there
            or spec.origin is None
            # we can only rewrite source files
            or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            # if the file doesn't exist, we can't rewrite it
            or not os.path.exists(spec.origin)
        ):
            return None
        else:
            fn = spec.origin

        if not self._should_rewrite(name, fn, state):
            return None

        return importlib.util.spec_from_file_location(
            name,
            fn,
            loader=self,
            submodule_search_locations=spec.submodule_search_locations,
        )

    def create_module(
        self, spec: importlib.machinery.ModuleSpec
    ) -> types.ModuleType | None:
        return None  # default behaviour is fine

    def exec_module(self, module: types.ModuleType) -> None:
        assert module.__spec__ is not None
        assert module.__spec__.origin is not None
        fn = Path(module.__spec__.origin)
        state = self.config.stash[assertstate_key]

        self._rewritten_names[module.__name__] = fn

        # The requested module looks like a test file, so rewrite it. This is
        # the most magical part of the process: load the source, rewrite the
        # asserts, and load the rewritten source. We also cache the rewritten
        # module code in a special pyc. We must be aware of the possibility of
        # concurrent pytest processes rewriting and loading pycs. To avoid
        # tricky race conditions, we maintain the following invariant: The
        # cached pyc is always a complete, valid pyc. Operations on it must be
        # atomic. POSIX's atomic rename comes in handy.
        write = not sys.dont_write_bytecode
        cache_dir = get_cache_dir(fn)
        if write:
            ok = try_makedirs(cache_dir)
            if not ok:
                write = False
                state.trace(f"read only directory: {cache_dir}")

        cache_name = fn.name[:-3] + PYC_TAIL
        pyc = cache_dir / cache_name
        # Notice that even if we're in a read-only directory, I'm going
        # to check for a cached pyc. This may not be optimal...
        co = _read_pyc(fn, pyc, state.trace)
        if co is None:
            state.trace(f"rewriting {fn!r}")
            source_stat, co = _rewrite_test(fn, self.config)
            if write:
                self._writing_pyc = True
                try:
                    _write_pyc(state, co, source_stat, pyc)
                finally:
                    self._writing_pyc = False
        else:
            state.trace(f"found cached rewritten pyc for {fn}")
        exec(co, module.__dict__)

    def _early_rewrite_bailout(self, name: str, state: AssertionState) -> bool:
        """A fast way to get out of rewriting modules.

        Profiling has shown that the call to PathFinder.find_spec (inside of
        the find_spec from this class) is a major slowdown, so, this method
        tries to filter what we're sure won't be rewritten before getting to
        it.
        """
        if self.session is not None and not self._session_paths_checked:
            self._session_paths_checked = True
            for initial_path in self.session._initialpaths:
                # Make something as c:/projects/my_project/path.py ->
                #     ['c:', 'projects', 'my_project', 'path.py']
                parts = str(initial_path).split(os.sep)
                # add 'path' to basenames to be checked.
                self._basenames_to_check_rewrite.add(os.path.splitext(parts[-1])[0])

        # Note: conftest already by default in _basenames_to_check_rewrite.
        parts = name.split(".")
        if parts[-1] in self._basenames_to_check_rewrite:
            return False

        # For matching the name it must be as if it was a filename.
        path = PurePath(*parts).with_suffix(".py")

        for pat in self.fnpats:
            # if the pattern contains subdirectories ("tests/**.py" for example) we can't bail out based
            # on the name alone because we need to match against the full path
            if os.path.dirname(pat):
                return False
            if fnmatch_ex(pat, path):
                return False

        if self._is_marked_for_rewrite(name, state):
            return False

        state.trace(f"early skip of rewriting module: {name}")
        return True

    def _should_rewrite(self, name: str, fn: str, state: AssertionState) -> bool:
        # always rewrite conftest files
        if os.path.basename(fn) == "conftest.py":
            state.trace(f"rewriting conftest file: {fn!r}")
            return True

        if self.session is not None:
            if self.session.isinitpath(absolutepath(fn)):
                state.trace(f"matched test file (was specified on cmdline): {fn!r}")
                return True

        # modules not passed explicitly on the command line are only
        # rewritten if they match the naming convention for test files
        fn_path = PurePath(fn)
        for pat in self.fnpats:
            if fnmatch_ex(pat, fn_path):
                state.trace(f"matched test file {fn!r}")
                return True

        return self._is_marked_for_rewrite(name, state)

    def _is_marked_for_rewrite(self, name: str, state: AssertionState) -> bool:
        try:
            return self._marked_for_rewrite_cache[name]
        except KeyError:
            for marked in self._must_rewrite:
                if name == marked or name.startswith(marked + "."):
                    state.trace(f"matched marked file {name!r} (from {marked!r})")
                    self._marked_for_rewrite_cache[name] = True
                    return True

            self._marked_for_rewrite_cache[name] = False
            return False

    def mark_rewrite(self, *names: str) -> None:
        """Mark import names as needing to be rewritten.

        The named module or package as well as any nested modules will
        be rewritten on import.
        """
        already_imported = (
            set(names).intersection(sys.modules).difference(self._rewritten_names)
        )
        for name in already_imported:
            mod = sys.modules[name]
            if not AssertionRewriter.is_rewrite_disabled(
                mod.__doc__ or ""
            ) and not isinstance(mod.__loader__, type(self)):
                self._warn_already_imported(name)
        self._must_rewrite.update(names)
        self._marked_for_rewrite_cache.clear()

    def _warn_already_imported(self, name: str) -> None:
        from _pytest.warning_types import PytestAssertRewriteWarning

        self.config.issue_config_time_warning(
            PytestAssertRewriteWarning(
                f"Module already imported so cannot be rewritten; {name}"
            ),
            stacklevel=5,
        )

    def get_data(self, pathname: str | bytes) -> bytes:
        """Optional PEP302 get_data API."""
        with open(pathname, "rb") as f:
            return f.read()

    def get_resource_reader(self, name: str) -> TraversableResources:
        return FileReader(types.SimpleNamespace(path=self._rewritten_names[name]))  # type: ignore[arg-type]


def _write_pyc_fp(
    fp: IO[bytes], source_stat: os.stat_result, co: types.CodeType
) -> None:
    # Technically, we don't have to have the same pyc format as
    # (C)Python, since these "pycs" should never be seen by builtin
    # import. However, there's little reason to deviate.
    fp.write(importlib.util.MAGIC_NUMBER)
    # https://www.python.org/dev/peps/pep-0552/
    flags = b"\x00\x00\x00\x00"
    fp.write(flags)
    # as of now, bytecode header expects 32-bit numbers for size and mtime (#4903)
    mtime = int(source_stat.st_mtime) & 0xFFFFFFFF
    size = source_stat.st_size & 0xFFFFFFFF
    # "<LL" stands for 2 unsigned longs, little-endian.
    fp.write(struct.pack("<LL", mtime, size))
    fp.write(marshal.dumps(co))


def _write_pyc(
    state: AssertionState,
    co: types.CodeType,
    source_stat: os.stat_result,
    pyc: Path,
) -> bool:
    proc_pyc = f"{pyc}.{os.getpid()}"
    try:
        with open(proc_pyc, "wb") as fp:
            _write_pyc_fp(fp, source_stat, co)
    except OSError as e:
        state.trace(f"error writing pyc file at {proc_pyc}: errno={e.errno}")
        return False

    try:
        os.replace(proc_pyc, pyc)
    except OSError as e:
        state.trace(f"error writing pyc file at {pyc}: {e}")
        # we ignore any failure to write the cache file
        # there are many reasons, permission-denied, pycache dir being a
        # file etc.
        return False
    return True


def _rewrite_test(fn: Path, config: Config) -> tuple[os.stat_result, types.CodeType]:
    """Read and rewrite *fn* and return the code object."""
    stat = os.stat(fn)
    source = fn.read_bytes()
    strfn = str(fn)
    tree = ast.parse(source, filename=strfn)
    rewrite_asserts(tree, source, strfn, config)
    co = compile(tree, strfn, "exec", dont_inherit=True)
    return stat, co


def _read_pyc(
    source: Path, pyc: Path, trace: Callable[[str], None] = lambda x: None
) -> types.CodeType | None:
    """Possibly read a pytest pyc containing rewritten code.

    Return rewritten code if successful or None if not.
    """
    try:
        fp = open(pyc, "rb")
    except OSError:
        return None
    with fp:
        try:
            stat_result = os.stat(source)
            mtime = int(stat_result.st_mtime)
            size = stat_result.st_size
            data = fp.read(16)
        except OSError as e:
            trace(f"_read_pyc({source}): OSError {e}")
            return None
        # Check for invalid or out of date pyc file.
        if len(data) != (16):
            trace(f"_read_pyc({source}): invalid pyc (too short)")
            return None
        if data[:4] != importlib.util.MAGIC_NUMBER:
            trace(f"_read_pyc({source}): invalid pyc (bad magic number)")
            return None
        if data[4:8] != b"\x00\x00\x00\x00":
            trace(f"_read_pyc({source}): invalid pyc (unsupported flags)")
            return None
        mtime_data = data[8:12]
        if int.from_bytes(mtime_data, "little") != mtime & 0xFFFFFFFF:
            trace(f"_read_pyc({source}): out of date")
            return None
        size_data = data[12:16]
        if int.from_bytes(size_data, "little") != size & 0xFFFFFFFF:
            trace(f"_read_pyc({source}): invalid pyc (incorrect size)")
            return None
        try:
            co = marshal.load(fp)
        except Exception as e:
            trace(f"_read_pyc({source}): marshal.load error {e}")
            return None
        if not isinstance(co, types.CodeType):
            trace(f"_read_pyc({source}): not a code object")
            return None
        return co


def rewrite_asserts(
    mod: ast.Module,
    source: bytes,
    module_path: str | None = None,
    config: Config | None = None,
) -> None:
    """Rewrite the assert statements in mod."""
    AssertionRewriter(module_path, config, source).run(mod)


def _saferepr(obj: object) -> str:
    r"""Get a safe repr of an object for assertion error messages.

    The assertion formatting (util.format_explanation()) requires
    newlines to be escaped since they are a special character for it.
    Normally assertion.util.format_explanation() does this but for a
    custom repr it is possible to contain one of the special escape
    sequences, especially '\n{' and '\n}' are likely to be present in
    JSON reprs.
    """
    if isinstance(obj, types.MethodType):
        # for bound methods, skip redundant <bound method ...> information
        return obj.__name__

    maxsize = _get_maxsize_for_saferepr(util._config)
    if not maxsize:
        return saferepr_unlimited(obj).replace("\n", "\\n")
    return saferepr(obj, maxsize=maxsize).replace("\n", "\\n")


def _get_maxsize_for_saferepr(config: Config | None) -> int | None:
    """Get `maxsize` configuration for saferepr based on the given config object."""
    if config is None:
        verbosity = 0
    else:
        verbosity = config.get_verbosity(Config.VERBOSITY_ASSERTIONS)
    if verbosity >= 2:
        return None
    if verbosity >= 1:
        return DEFAULT_REPR_MAX_SIZE * 10
    return DEFAULT_REPR_MAX_SIZE


def _format_assertmsg(obj: object) -> str:
    r"""Format the custom assertion message given.

    For strings this simply replaces newlines with '\n~' so that
