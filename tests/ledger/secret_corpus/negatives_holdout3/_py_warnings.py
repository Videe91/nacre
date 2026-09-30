"""Python part of the warnings subsystem."""

import sys
import _contextvars
import _thread


__all__ = ["warn", "warn_explicit", "showwarning",
           "formatwarning", "filterwarnings", "simplefilter",
           "resetwarnings", "catch_warnings", "deprecated"]


# Normally '_wm' is sys.modules['warnings'] but for unit tests it can be
# a different module.  User code is allowed to reassign global attributes
# of the 'warnings' module, commonly 'filters' or 'showwarning'. So we
# need to lookup these global attributes dynamically on the '_wm' object,
# rather than binding them earlier.  The code in this module consistently uses
# '_wm.<something>' rather than using the globals of this module.  If the
# '_warnings' C extension is in use, some globals are replaced by functions
# and variables defined in that extension.
_wm = None


def _set_module(module):
    global _wm
    _wm = module


# filters contains a sequence of filter 5-tuples
# The components of the 5-tuple are:
# - an action: error, ignore, always, all, default, module, or once
# - a compiled regex that must match the warning message
# - a class representing the warning category
# - a compiled regex that must match the module that is being warned
# - a line number for the line being warning, or 0 to mean any line
# If either if the compiled regexs are None, match anything.
filters = []


defaultaction = "default"
onceregistry = {}
_lock = _thread.RLock()
_filters_version = 1


# If true, catch_warnings() will use a context var to hold the modified
# filters list.  Otherwise, catch_warnings() will operate on the 'filters'
# global of the warnings module.
_use_context = sys.flags.context_aware_warnings


class _Context:
    def __init__(self, filters):
        self._filters = filters
        self.log = None  # if set to a list, logging is enabled

    def copy(self):
        context = _Context(self._filters[:])
        if self.log is not None:
            context.log = self.log
        return context

    def _record_warning(self, msg):
        self.log.append(msg)


class _GlobalContext(_Context):
    def __init__(self):
        self.log = None

    @property
    def _filters(self):
        # Since there is quite a lot of code that assigns to
        # warnings.filters, this needs to return the current value of
        # the module global.
        try:
            return _wm.filters
        except AttributeError:
            # 'filters' global was deleted.  Do we need to actually handle this case?
            return []


_global_context = _GlobalContext()


_warnings_context = _contextvars.ContextVar('warnings_context')


def _get_context():
    if not _use_context:
        return _global_context
    try:
        return _wm._warnings_context.get()
    except LookupError:
        return _global_context


def _set_context(context):
    assert _use_context
    _wm._warnings_context.set(context)


def _new_context():
    assert _use_context
    old_context = _wm._get_context()
    new_context = old_context.copy()
    _wm._set_context(new_context)
    return old_context, new_context


def _get_filters():
    """Return the current list of filters.  This is a non-public API used by
    module functions and by the unit tests."""
    return _wm._get_context()._filters


def _filters_mutated_lock_held():
    _wm._filters_version += 1


def showwarning(message, category, filename, lineno, file=None, line=None):
    """Hook to write a warning to a file; replace if you like."""
    msg = _wm.WarningMessage(message, category, filename, lineno, file, line)
    _wm._showwarnmsg_impl(msg)


def formatwarning(message, category, filename, lineno, line=None):
    """Function to format a warning the standard way."""
    msg = _wm.WarningMessage(message, category, filename, lineno, None, line)
    return _wm._formatwarnmsg_impl(msg)


def _showwarnmsg_impl(msg):
    context = _wm._get_context()
    if context.log is not None:
        context._record_warning(msg)
        return
    file = msg.file
    if file is None:
        file = sys.stderr
        if file is None:
            # sys.stderr is None when run with pythonw.exe:
            # warnings get lost
            return
    text = _wm._formatwarnmsg(msg)
    try:
        file.write(text)
    except OSError:
        # the file (probably stderr) is invalid - this warning gets lost.
        pass


def _formatwarnmsg_impl(msg):
    category = msg.category.__name__
    s =  f"{msg.filename}:{msg.lineno}: {category}: {msg.message}\n"

    if msg.line is None:
        try:
            import linecache
            line = linecache.getline(msg.filename, msg.lineno)
        except Exception:
            # When a warning is logged during Python shutdown, linecache
            # and the import machinery don't work anymore
            line = None
            linecache = None
    else:
        line = msg.line
    if line:
        line = line.strip()
        s += "  %s\n" % line

    if msg.source is not None:
        try:
            import tracemalloc
        # Logging a warning should not raise a new exception:
        # catch Exception, not only ImportError and RecursionError.
        except Exception:
            # don't suggest to enable tracemalloc if it's not available
            suggest_tracemalloc = False
            tb = None
        else:
            try:
                suggest_tracemalloc = not tracemalloc.is_tracing()
                tb = tracemalloc.get_object_traceback(msg.source)
            except Exception:
                # When a warning is logged during Python shutdown, tracemalloc
                # and the import machinery don't work anymore
                suggest_tracemalloc = False
                tb = None

        if tb is not None:
            s += 'Object allocated at (most recent call last):\n'
            for frame in tb:
                s += ('  File "%s", lineno %s\n'
                      % (frame.filename, frame.lineno))

                try:
                    if linecache is not None:
                        line = linecache.getline(frame.filename, frame.lineno)
                    else:
                        line = None
                except Exception:
                    line = None
                if line:
                    line = line.strip()
                    s += '    %s\n' % line
        elif suggest_tracemalloc:
            s += (f'{category}: Enable tracemalloc to get the object '
                  f'allocation traceback\n')
    return s


# Keep a reference to check if the function was replaced
_showwarning_orig = showwarning


def _showwarnmsg(msg):
    """Hook to write a warning to a file; replace if you like."""
    try:
        sw = _wm.showwarning
    except AttributeError:
        pass
    else:
        if sw is not _showwarning_orig:
            # warnings.showwarning() was replaced
            if not callable(sw):
                raise TypeError("warnings.showwarning() must be set to a "
                                "function or method")

            sw(msg.message, msg.category, msg.filename, msg.lineno,
               msg.file, msg.line)
            return
    _wm._showwarnmsg_impl(msg)


# Keep a reference to check if the function was replaced
_formatwarning_orig = formatwarning


def _formatwarnmsg(msg):
    """Function to format a warning the standard way."""
    try:
        fw = _wm.formatwarning
    except AttributeError:
        pass
    else:
        if fw is not _formatwarning_orig:
            # warnings.formatwarning() was replaced
            return fw(msg.message, msg.category,
                      msg.filename, msg.lineno, msg.line)
    return _wm._formatwarnmsg_impl(msg)


def filterwarnings(action, message="", category=Warning, module="", lineno=0,
                   append=False):
    """Insert an entry into the list of warnings filters (at the front).

    'action' -- one of "error", "ignore", "always", "all", "default", "module",
                or "once"
    'message' -- a regex that the warning message must match
    'category' -- a class that the warning must be a subclass of
    'module' -- a regex that the module name must match
    'lineno' -- an integer line number, 0 matches all warnings
    'append' -- if true, append to the list of filters
    """
    if action not in {"error", "ignore", "always", "all", "default", "module", "once"}:
        raise ValueError(f"invalid action: {action!r}")
    if not isinstance(message, str):
        raise TypeError("message must be a string")
    if not isinstance(category, type) or not issubclass(category, Warning):
        raise TypeError("category must be a Warning subclass")
    if not isinstance(module, str):
        raise TypeError("module must be a string")
    if not isinstance(lineno, int):
        raise TypeError("lineno must be an int")
    if lineno < 0:
        raise ValueError("lineno must be an int >= 0")

    if message or module:
        import re

    if message:
        message = re.compile(message, re.I)
    else:
        message = None
    if module:
        module = re.compile(module)
    else:
        module = None

    _wm._add_filter(action, message, category, module, lineno, append=append)


def simplefilter(action, category=Warning, lineno=0, append=False):
    """Insert a simple entry into the list of warnings filters (at the front).

    A simple filter matches all modules and messages.
    'action' -- one of "error", "ignore", "always", "all", "default", "module",
                or "once"
    'category' -- a class that the warning must be a subclass of
    'lineno' -- an integer line number, 0 matches all warnings
    'append' -- if true, append to the list of filters
    """
    if action not in {"error", "ignore", "always", "all", "default", "module", "once"}:
        raise ValueError(f"invalid action: {action!r}")
    if not isinstance(lineno, int):
        raise TypeError("lineno must be an int")
    if lineno < 0:
        raise ValueError("lineno must be an int >= 0")
    _wm._add_filter(action, None, category, None, lineno, append=append)


def _filters_mutated():
    # Even though this function is not part of the public API, it's used by
    # a fair amount of user code.
    with _wm._lock:
        _wm._filters_mutated_lock_held()


def _add_filter(*item, append):
    with _wm._lock:
        filters = _wm._get_filters()
        if not append:
            # Remove possible duplicate filters, so new one will be placed
            # in correct place. If append=True and duplicate exists, do nothing.
            try:
                filters.remove(item)
            except ValueError:
                pass
            filters.insert(0, item)
        else:
            if item not in filters:
                filters.append(item)
        _wm._filters_mutated_lock_held()


def resetwarnings():
    """Clear the list of warning filters, so that no filters are active."""
    with _wm._lock:
        del _wm._get_filters()[:]
        _wm._filters_mutated_lock_held()


class _OptionError(Exception):
    """Exception used by option processing helpers."""
    pass


# Helper to process -W options passed via sys.warnoptions
def _processoptions(args):
    for arg in args:
        try:
            _wm._setoption(arg)
        except _wm._OptionError as msg:
            print("Invalid -W option ignored:", msg, file=sys.stderr)


# Helper for _processoptions()
def _setoption(arg):
    parts = arg.split(':')
    if len(parts) > 5:
        raise _wm._OptionError("too many fields (max 5): %r" % (arg,))
    while len(parts) < 5:
        parts.append('')
    action, message, category, module, lineno = [s.strip()
                                                 for s in parts]
    action = _wm._getaction(action)
    category = _wm._getcategory(category)
    if message or module:
        import re
    if message:
        message = re.escape(message)
    if module:
        module = re.escape(module) + r'\z'
    if lineno:
        try:
            lineno = int(lineno)
            if lineno < 0:
                raise ValueError
        except (ValueError, OverflowError):
            raise _wm._OptionError("invalid lineno %r" % (lineno,)) from None
    else:
        lineno = 0
    _wm.filterwarnings(action, message, category, module, lineno)


# Helper for _setoption()
def _getaction(action):
    if not action:
        return "default"
    for a in ('default', 'always', 'all', 'ignore', 'module', 'once', 'error'):
        if a.startswith(action):
            return a
    raise _wm._OptionError("invalid action: %r" % (action,))


# Helper for _setoption()
def _getcategory(category):
    if not category:
        return Warning
    if '.' not in category:
        import builtins as m
        klass = category
    else:
        module, _, klass = category.rpartition('.')
        try:
            m = __import__(module, None, None, [klass])
        except ImportError:
            raise _wm._OptionError("invalid module name: %r" % (module,)) from None
    try:
        cat = getattr(m, klass)
    except AttributeError:
        raise _wm._OptionError("unknown warning category: %r" % (category,)) from None
    if not issubclass(cat, Warning):
        raise _wm._OptionError("invalid warning category: %r" % (category,))
    return cat


def _is_internal_filename(filename):
    return 'importlib' in filename and '_bootstrap' in filename


def _is_filename_to_skip(filename, skip_file_prefixes):
    return any(filename.startswith(prefix) for prefix in skip_file_prefixes)


def _is_internal_frame(frame):
    """Signal whether the frame is an internal CPython implementation detail."""
    return _is_internal_filename(frame.f_code.co_filename)


def _next_external_frame(frame, skip_file_prefixes):
    """Find the next frame that doesn't involve Python or user internals."""
    frame = frame.f_back
    while frame is not None and (
            _is_internal_filename(filename := frame.f_code.co_filename) or
            _is_filename_to_skip(filename, skip_file_prefixes)):
        frame = frame.f_back
    return frame


# Code typically replaced by _warnings
def warn(message, category=None, stacklevel=1, source=None,
         *, skip_file_prefixes=()):
    """Issue a warning, or maybe ignore it or raise an exception."""
    # Check if message is already a Warning object
    if isinstance(message, Warning):
        category = message.__class__
    # Check category argument
    if category is None:
        category = UserWarning
    if not (isinstance(category, type) and issubclass(category, Warning)):
        raise TypeError("category must be a Warning subclass, "
                        "not '{:s}'".format(type(category).__name__))
    if not isinstance(skip_file_prefixes, tuple):
        # The C version demands a tuple for implementation performance.
        raise TypeError('skip_file_prefixes must be a tuple of strs.')
    if skip_file_prefixes:
        stacklevel = max(2, stacklevel)
    # Get context information
    try:
        if stacklevel <= 1 or _is_internal_frame(sys._getframe(1)):
            # If frame is too small to care or if the warning originated in
            # internal code, then do not try to hide any frames.
            frame = sys._getframe(stacklevel)
        else:
            frame = sys._getframe(1)
            # Look for one frame less since the above line starts us off.
            for x in range(stacklevel-1):
                frame = _next_external_frame(frame, skip_file_prefixes)
                if frame is None:
                    raise ValueError
    except ValueError:
        globals = sys.__dict__
        filename = "<sys>"
        lineno = 0
    else:
        globals = frame.f_globals
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
    if '__name__' in globals:
        module = globals['__name__']
    else:
        module = "<string>"
    registry = globals.setdefault("__warningregistry__", {})
    _wm.warn_explicit(
        message,
        category,
        filename,
        lineno,
        module,
        registry,
        globals,
        source=source,
    )


def warn_explicit(message, category, filename, lineno,
                  module=None, registry=None, module_globals=None,
                  source=None):
    lineno = int(lineno)
    if module is None:
        module = filename or "<unknown>"
