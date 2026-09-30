# mypy: allow-untyped-defs
# ruff: noqa: T100
"""Hook specifications for pytest plugins which are invoked by pytest itself
and by builtin plugins."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

from pluggy import HookspecMarker


if TYPE_CHECKING:
    import pdb
    from typing import Literal
    import warnings

    from _pytest._code.code import ExceptionInfo
    from _pytest._code.code import ExceptionRepr
    from _pytest.config import _PluggyPlugin
    from _pytest.config import Config
    from _pytest.config import ExitCode
    from _pytest.config import PytestPluginManager
    from _pytest.config.argparsing import Parser
    from _pytest.fixtures import FixtureDef
    from _pytest.fixtures import SubRequest
    from _pytest.main import Session
    from _pytest.nodes import Collector
    from _pytest.nodes import Item
    from _pytest.outcomes import Exit
    from _pytest.python import Class
    from _pytest.python import Function
    from _pytest.python import Metafunc
    from _pytest.python import Module
    from _pytest.reports import CollectReport
    from _pytest.reports import TestReport
    from _pytest.runner import CallInfo
    from _pytest.terminal import TerminalReporter
    from _pytest.terminal import TestShortLogReport


hookspec = HookspecMarker("pytest")

# -------------------------------------------------------------------------
# Initialization hooks called for every plugin
# -------------------------------------------------------------------------


@hookspec(historic=True)
def pytest_addhooks(pluginmanager: PytestPluginManager) -> None:
    """Called at plugin registration time to allow adding new hooks via a call to
    :func:`pluginmanager.add_hookspecs(module_or_class, prefix) <pytest.PytestPluginManager.add_hookspecs>`.

    :param pluginmanager: The pytest plugin manager.

    .. note::
        This hook is incompatible with hook wrappers.

    Use in conftest plugins
    =======================

    If a conftest plugin implements this hook, it will be called immediately
    when the conftest is registered.
    """


@hookspec(historic=True)
def pytest_plugin_registered(
    plugin: _PluggyPlugin,
    plugin_name: str,
    manager: PytestPluginManager,
) -> None:
    """A new pytest plugin got registered.

    :param plugin: The plugin module or instance.
    :param plugin_name: The name by which the plugin is registered.
    :param manager: The pytest plugin manager.

    .. note::
        This hook is incompatible with hook wrappers.

    Use in conftest plugins
    =======================

    If a conftest plugin implements this hook, it will be called immediately
    when the conftest is registered, once for each plugin registered thus far
    (including itself!), and for all plugins thereafter when they are
    registered.
    """


@hookspec(historic=True)
def pytest_addoption(parser: Parser, pluginmanager: PytestPluginManager) -> None:
    """Register argparse-style options and config-style config values,
    called once at the beginning of a test run.

    :param parser:
        To add command line options, call
        :py:func:`parser.addoption(...) <pytest.Parser.addoption>`.
        To add config-file values call :py:func:`parser.addini(...)
        <pytest.Parser.addini>`.

    :param pluginmanager:
        The pytest plugin manager, which can be used to install :py:func:`~pytest.hookspec`'s
        or :py:func:`~pytest.hookimpl`'s and allow one plugin to call another plugin's hooks
        to change how command line options are added.

    Options can later be accessed through the
    :py:class:`config <pytest.Config>` object, respectively:

    - :py:func:`config.getoption(name) <pytest.Config.getoption>` to
      retrieve the value of a command line option.

    - :py:func:`config.getini(name) <pytest.Config.getini>` to retrieve
      a value read from a configuration file.

    The config object is passed around on many internal objects via the ``.config``
    attribute or can be retrieved as the ``pytestconfig`` fixture.

    .. note::
        This hook is incompatible with hook wrappers.

    Use in conftest plugins
    =======================

    If a conftest plugin implements this hook, it will be called immediately
    when the conftest is registered.

    This hook is only called for :ref:`initial conftests <pluginorder>`.
    """


@hookspec(historic=True)
def pytest_configure(config: Config) -> None:
    """Allow plugins and conftest files to perform initial configuration.

    .. note::
        This hook is incompatible with hook wrappers.

    :param config: The pytest config object.

    Use in conftest plugins
    =======================

    This hook is called for every :ref:`initial conftest <pluginorder>` file
    after command line options have been parsed. After that, the hook is called
    for other conftest files as they are registered.
    """


# -------------------------------------------------------------------------
# Bootstrapping hooks called for plugins registered early enough:
# internal and 3rd party plugins.
# -------------------------------------------------------------------------


@hookspec(firstresult=True)
def pytest_cmdline_parse(
    pluginmanager: PytestPluginManager, args: list[str]
) -> Config | None:
    """Return an initialized :class:`~pytest.Config`, parsing the specified args.

    Stops at first non-None result, see :ref:`firstresult`.

    .. note::
        This hook is only called for plugin classes passed to the
        ``plugins`` arg when using `pytest.main`_ to perform an in-process
        test run.

    :param pluginmanager: The pytest plugin manager.
    :param args: List of arguments passed on the command line.
    :returns: A pytest config object.

    Use in conftest plugins
    =======================

    This hook is not called for conftest files.
    """


def pytest_load_initial_conftests(
    early_config: Config, parser: Parser, args: list[str]
) -> None:
    """Called to implement the loading of :ref:`initial conftest files
    <pluginorder>` ahead of command line option parsing.

    :param early_config: The pytest config object.
    :param args: Arguments passed on the command line.
    :param parser: To add command line options.

    Use in conftest plugins
    =======================

    This hook is not called for conftest files.
    """


@hookspec(firstresult=True)
def pytest_cmdline_main(config: Config) -> ExitCode | int | None:
    """Called for performing the main command line action.

    The default implementation will invoke the configure hooks and
    :hook:`pytest_runtestloop`.

    Stops at first non-None result, see :ref:`firstresult`.

    :param config: The pytest config object.
    :returns: The exit code.

    Use in conftest plugins
    =======================

    This hook is only called for :ref:`initial conftests <pluginorder>`.
    """


# -------------------------------------------------------------------------
# collection hooks
# -------------------------------------------------------------------------


@hookspec(firstresult=True)
def pytest_collection(session: Session) -> object | None:
    """Perform the collection phase for the given session.

    Stops at first non-None result, see :ref:`firstresult`.
    The return value is not used, but only stops further processing.

    The default collection phase is this (see individual hooks for full details):

    1. Starting from ``session`` as the initial collector:

      1. ``pytest_collectstart(collector)``
      2. ``report = pytest_make_collect_report(collector)``
      3. ``pytest_exception_interact(collector, call, report)`` if an interactive exception occurred
      4. For each collected node:

        1. If an item, ``pytest_itemcollected(item)``
        2. If a collector, recurse into it.

      5. ``pytest_collectreport(report)``

    2. ``pytest_collection_modifyitems(session, config, items)``

      1. ``pytest_deselected(items)`` for any deselected items (may be called multiple times)

    3. Set ``session.items`` to the list of collected items
    4. ``pytest_collection_finish(session)``
    5. Set ``session.testscollected`` to the number of collected items

    You can implement this hook to only perform some action before collection,
    for example the terminal plugin uses it to start displaying the collection
    counter (and returns `None`).

    :param session: The pytest session object.

    Use in conftest plugins
    =======================

    This hook is only called for :ref:`initial conftests <pluginorder>`.
    """


def pytest_collection_modifyitems(
    session: Session, config: Config, items: list[Item]
) -> None:
    """Called after collection has been performed. May filter or re-order
    the items in-place.

    When items are deselected (filtered out from ``items``),
    the hook :hook:`pytest_deselected` must be called explicitly
    with the deselected items to properly notify other plugins,
    e.g. with ``config.hook.pytest_deselected(items=deselected_items)``.

    :param session: The pytest session object.
    :param config: The pytest config object.
    :param items: List of item objects.

    Use in conftest plugins
    =======================

    Any conftest plugin can implement this hook.
    """


def pytest_collection_finish(session: Session) -> None:
    """Called after collection has been performed and modified.

    :param session: The pytest session object.

    Use in conftest plugins
    =======================

    Any conftest plugin can implement this hook.
    """


@hookspec(firstresult=True)
def pytest_ignore_collect(collection_path: Path, config: Config) -> bool | None:
    """Return ``True`` to ignore this path for collection.

    Return ``None`` to let other plugins ignore the path for collection.

    Returning ``False`` will forcefully *not* ignore this path for collection,
    without giving a chance for other plugins to ignore this path.

    This hook is consulted for all files and directories prior to calling
    more specific hooks.

    Stops at first non-None result, see :ref:`firstresult`.

    :param collection_path: The path to analyze.
    :type collection_path: pathlib.Path
    :param config: The pytest config object.

    .. versionchanged:: 7.0.0
        The ``collection_path`` parameter was added as a :class:`pathlib.Path`
        equivalent of the ``path`` parameter. The ``path`` parameter
        has been deprecated and removed in pytest 9.0.0.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given collection path, only
    conftest files in parent directories of the collection path are consulted
    (if the path is a directory, its own conftest file is *not* consulted - a
    directory cannot ignore itself!).
    """


@hookspec(firstresult=True)
def pytest_collect_directory(path: Path, parent: Collector) -> Collector | None:
    """Create a :class:`~pytest.Collector` for the given directory, or None if
    not relevant.

    .. versionadded:: 8.0

    For best results, the returned collector should be a subclass of
    :class:`~pytest.Directory`, but this is not required.

    The new node needs to have the specified ``parent`` as a parent.

    Stops at first non-None result, see :ref:`firstresult`.

    :param path: The path to analyze.
    :type path: pathlib.Path

    See :ref:`custom directory collectors` for a simple example of use of this
    hook.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given collection path, only
    conftest files in parent directories of the collection path are consulted
    (if the path is a directory, its own conftest file is *not* consulted - a
    directory cannot collect itself!).
    """


def pytest_collect_file(file_path: Path, parent: Collector) -> Collector | None:
    """Create a :class:`~pytest.Collector` for the given path, or None if not relevant.

    For best results, the returned collector should be a subclass of
    :class:`~pytest.File`, but this is not required.

    The new node needs to have the specified ``parent`` as a parent.

    :param file_path: The path to analyze.
    :type file_path: pathlib.Path

    .. versionchanged:: 7.0.0
        The ``file_path`` parameter was added as a :class:`pathlib.Path`
        equivalent of the ``path`` parameter. The ``path`` parameter
        has been deprecated and removed in pytest 9.0.0.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given file path, only
    conftest files in parent directories of the file path are consulted.
    """


# logging hooks for collection


def pytest_collectstart(collector: Collector) -> None:
    """Collector starts collecting.

    :param collector:
        The collector.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given collector, only
    conftest files in the collector's directory and its parent directories are
    consulted.
    """


def pytest_itemcollected(item: Item) -> None:
    """We just collected a test item.

    :param item:
        The item.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given item, only conftest
    files in the item's directory and its parent directories are consulted.
    """


def pytest_collectreport(report: CollectReport) -> None:
    """Collector finished collecting.

    :param report:
        The collect report.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given collector, only
    conftest files in the collector's directory and its parent directories are
    consulted.
    """


def pytest_deselected(items: Sequence[Item]) -> None:
    """Called for deselected test items, e.g. by keyword.

    Note that this hook has two integration aspects for plugins:

    - it can be *implemented* to be notified of deselected items
    - it must be *called* from :hook:`pytest_collection_modifyitems`
      implementations when items are deselected (to properly notify other plugins).

    May be called multiple times.

    :param items:
        The items.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook.
    """


@hookspec(firstresult=True)
def pytest_make_collect_report(collector: Collector) -> CollectReport | None:
    """Perform :func:`collector.collect() <pytest.Collector.collect>` and return
    a :class:`~pytest.CollectReport`.

    Stops at first non-None result, see :ref:`firstresult`.

    :param collector:
        The collector.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given collector, only
    conftest files in the collector's directory and its parent directories are
    consulted.
    """


# -------------------------------------------------------------------------
# Python test function related hooks
# -------------------------------------------------------------------------


@hookspec(firstresult=True)
def pytest_pycollect_makemodule(module_path: Path, parent) -> Module | None:
    """Return a :class:`pytest.Module` collector or None for the given path.

    This hook will be called for each matching test module path.
    The :hook:`pytest_collect_file` hook needs to be used if you want to
    create test modules for files that do not match as a test module.

    Stops at first non-None result, see :ref:`firstresult`.

    :param module_path: The path of the module to collect.
    :type module_path: pathlib.Path

    .. versionchanged:: 7.0.0
        The ``module_path`` parameter was added as a :class:`pathlib.Path`
        equivalent of the ``path`` parameter. The ``path`` parameter has been
        deprecated in favor of ``module_path`` and removed in pytest 9.0.0.

    Use in conftest plugins
    =======================

    Any conftest file can implement this hook. For a given parent collector,
