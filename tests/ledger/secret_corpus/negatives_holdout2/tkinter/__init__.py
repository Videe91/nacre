"""Wrapper functions for Tcl/Tk.

Tkinter provides classes which allow the display, positioning and
control of widgets. Toplevel widgets are Tk and Toplevel. Other
widgets are Frame, Label, Entry, Text, Canvas, Button, Radiobutton,
Checkbutton, Scale, Listbox, Scrollbar, OptionMenu, Spinbox
LabelFrame and PanedWindow.

Properties of the widgets are specified with keyword arguments.
Keyword arguments have the same name as the corresponding options
under Tk.

Widgets are positioned with one of the geometry managers Place, Pack
or Grid. These managers can be called with methods place, pack, grid
available in every Widget.

Actions are bound to events by options (e.g. the command
keyword argument) or with the bind() method.

Example (Hello, World):
import tkinter
from tkinter.constants import *
tk = tkinter.Tk()
frame = tkinter.Frame(tk, relief=RIDGE, borderwidth=2)
frame.pack(fill=BOTH, expand=1)
label = tkinter.Label(frame, text="Hello, World")
label.pack(fill=X, expand=1)
button = tkinter.Button(frame, text="Exit", command=tk.destroy)
button.pack(side=BOTTOM)
tk.mainloop()
"""

import collections
import enum
import sys
import types

import _tkinter # If this fails your Python may not be configured for Tk
TclError = _tkinter.TclError
from tkinter.constants import *
import re

wantobjects = 1
_debug = False  # set to True to print executed Tcl/Tk commands

TkVersion = float(_tkinter.TK_VERSION)
TclVersion = float(_tkinter.TCL_VERSION)

READABLE = _tkinter.READABLE
WRITABLE = _tkinter.WRITABLE
EXCEPTION = _tkinter.EXCEPTION


_magic_re = re.compile(r'([\\{}])')
_space_re = re.compile(r'([\s])', re.ASCII)


def _join(value):
    """Internal function."""
    return ' '.join(map(_stringify, value))


def _stringify(value):
    """Internal function."""
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            value = _stringify(value[0])
            if _magic_re.search(value):
                value = '{%s}' % value
        else:
            value = '{%s}' % _join(value)
    else:
        if isinstance(value, bytes):
            value = str(value, 'latin1')
        else:
            value = str(value)
        if not value:
            value = '{}'
        elif _magic_re.search(value):
            # add '\' before special characters and spaces
            value = _magic_re.sub(r'\\\1', value)
            value = value.replace('\n', r'\n')
            value = _space_re.sub(r'\\\1', value)
            if value[0] == '"':
                value = '\\' + value
        elif value[0] == '"' or _space_re.search(value):
            value = '{%s}' % value
    return value


def _flatten(seq):
    """Internal function."""
    res = ()
    for item in seq:
        if isinstance(item, (tuple, list)):
            res = res + _flatten(item)
        elif item is not None:
            res = res + (item,)
    return res


try: _flatten = _tkinter._flatten
except AttributeError: pass


def _cnfmerge(cnfs):
    """Internal function."""
    if isinstance(cnfs, dict):
        return cnfs
    elif isinstance(cnfs, (type(None), str)):
        return cnfs
    else:
        cnf = {}
        for c in _flatten(cnfs):
            try:
                cnf.update(c)
            except (AttributeError, TypeError) as msg:
                print("_cnfmerge: fallback due to:", msg)
                for k, v in c.items():
                    cnf[k] = v
        return cnf


try: _cnfmerge = _tkinter._cnfmerge
except AttributeError: pass


def _splitdict(tk, v, cut_minus=True, conv=None):
    """Return a properly formatted dict built from Tcl list pairs.

    If cut_minus is True, the supposed '-' prefix will be removed from
    keys. If conv is specified, it is used to convert values.

    Tcl list is expected to contain an even number of elements.
    """
    t = tk.splitlist(v)
    if len(t) % 2:
        raise RuntimeError('Tcl list representing a dict is expected '
                           'to contain an even number of elements')
    it = iter(t)
    dict = {}
    for key, value in zip(it, it):
        key = str(key)
        if cut_minus and key[0] == '-':
            key = key[1:]
        if conv:
            value = conv(value)
        dict[key] = value
    return dict

class _VersionInfoType(collections.namedtuple('_VersionInfoType',
        ('major', 'minor', 'micro', 'releaselevel', 'serial'))):
    def __str__(self):
        if self.releaselevel == 'final':
            return f'{self.major}.{self.minor}.{self.micro}'
        else:
            return f'{self.major}.{self.minor}{self.releaselevel[0]}{self.serial}'

def _parse_version(version):
    import re
    m = re.fullmatch(r'(\d+)\.(\d+)([ab.])(\d+)', version)
    major, minor, releaselevel, serial = m.groups()
    major, minor, serial = int(major), int(minor), int(serial)
    if releaselevel == '.':
        micro = serial
        serial = 0
        releaselevel = 'final'
    else:
        micro = 0
        releaselevel = {'a': 'alpha', 'b': 'beta'}[releaselevel]
    return _VersionInfoType(major, minor, micro, releaselevel, serial)


@enum._simple_enum(enum.StrEnum)
class EventType:
    KeyPress = '2'
    Key = KeyPress
    KeyRelease = '3'
    ButtonPress = '4'
    Button = ButtonPress
    ButtonRelease = '5'
    Motion = '6'
    Enter = '7'
    Leave = '8'
    FocusIn = '9'
    FocusOut = '10'
    Keymap = '11'           # undocumented
    Expose = '12'
    GraphicsExpose = '13'   # undocumented
    NoExpose = '14'         # undocumented
    Visibility = '15'
    Create = '16'
    Destroy = '17'
    Unmap = '18'
    Map = '19'
    MapRequest = '20'
    Reparent = '21'
    Configure = '22'
    ConfigureRequest = '23'
    Gravity = '24'
    ResizeRequest = '25'
    Circulate = '26'
    CirculateRequest = '27'
    Property = '28'
    SelectionClear = '29'   # undocumented
    SelectionRequest = '30' # undocumented
    Selection = '31'        # undocumented
    Colormap = '32'
    ClientMessage = '33'    # undocumented
    Mapping = '34'          # undocumented
    VirtualEvent = '35'     # undocumented
    Activate = '36'
    Deactivate = '37'
    MouseWheel = '38'


class Event:
    """Container for the properties of an event.

    Instances of this type are generated if one of the following events occurs:

    KeyPress, KeyRelease - for keyboard events
    ButtonPress, ButtonRelease, Motion, Enter, Leave, MouseWheel - for mouse events
    Visibility, Unmap, Map, Expose, FocusIn, FocusOut, Circulate,
    Colormap, Gravity, Reparent, Property, Destroy, Activate,
    Deactivate - for window events.

    If a callback function for one of these events is registered
    using bind, bind_all, bind_class, or tag_bind, the callback is
    called with an Event as first argument. It will have the
    following attributes (in braces are the event types for which
    the attribute is valid):

        serial - serial number of event
    num - mouse button pressed (ButtonPress, ButtonRelease)
    focus - whether the window has the focus (Enter, Leave)
    height - height of the exposed window (Configure, Expose)
    width - width of the exposed window (Configure, Expose)
    keycode - keycode of the pressed key (KeyPress, KeyRelease)
    state - state of the event as a number (ButtonPress, ButtonRelease,
                            Enter, KeyPress, KeyRelease,
                            Leave, Motion)
    state - state as a string (Visibility)
    time - when the event occurred
    x - x-position of the mouse
    y - y-position of the mouse
    x_root - x-position of the mouse on the screen
             (ButtonPress, ButtonRelease, KeyPress, KeyRelease, Motion)
    y_root - y-position of the mouse on the screen
             (ButtonPress, ButtonRelease, KeyPress, KeyRelease, Motion)
    char - pressed character (KeyPress, KeyRelease)
    send_event - see X/Windows documentation
    keysym - keysym of the event as a string (KeyPress, KeyRelease)
    keysym_num - keysym of the event as a number (KeyPress, KeyRelease)
    type - type of the event as a number
    widget - widget in which the event occurred
    delta - delta of wheel movement (MouseWheel)
    """

    def __repr__(self):
        attrs = {k: v for k, v in self.__dict__.items() if v != '??'}
        if not self.char:
            del attrs['char']
        elif self.char != '??':
            attrs['char'] = repr(self.char)
        if not getattr(self, 'send_event', True):
            del attrs['send_event']
        if self.state == 0:
            del attrs['state']
        elif isinstance(self.state, int):
            state = self.state
            mods = ('Shift', 'Lock', 'Control',
                    'Mod1', 'Mod2', 'Mod3', 'Mod4', 'Mod5',
                    'Button1', 'Button2', 'Button3', 'Button4', 'Button5')
            s = []
            for i, n in enumerate(mods):
                if state & (1 << i):
                    s.append(n)
            state = state & ~((1<< len(mods)) - 1)
            if state or not s:
                s.append(hex(state))
            attrs['state'] = '|'.join(s)
        if self.delta == 0:
            del attrs['delta']
        # widget usually is known
        # serial and time are not very interesting
        # keysym_num duplicates keysym
        # x_root and y_root mostly duplicate x and y
        keys = ('send_event',
                'state', 'keysym', 'keycode', 'char',
                'num', 'delta', 'focus',
                'x', 'y', 'width', 'height')
        return '<%s event%s>' % (
            getattr(self.type, 'name', self.type),
            ''.join(' %s=%s' % (k, attrs[k]) for k in keys if k in attrs)
        )

    __class_getitem__ = classmethod(types.GenericAlias)


_support_default_root = True
_default_root = None


def NoDefaultRoot():
    """Inhibit setting of default root window.

    Call this function to inhibit that the first instance of
    Tk is used for windows without an explicit parent window.
    """
    global _support_default_root, _default_root
    _support_default_root = False
    # Delete, so any use of _default_root will immediately raise an exception.
    # Rebind before deletion, so repeated calls will not fail.
    _default_root = None
    del _default_root


def _get_default_root(what=None):
    if not _support_default_root:
        raise RuntimeError("No master specified and tkinter is "
                           "configured to not support default root")
    if _default_root is None:
        if what:
            raise RuntimeError(f"Too early to {what}: no default root window")
        root = Tk()
        assert _default_root is root
    return _default_root


def _get_temp_root():
    global _support_default_root
    if not _support_default_root:
        raise RuntimeError("No master specified and tkinter is "
                           "configured to not support default root")
    root = _default_root
    if root is None:
        assert _support_default_root
        _support_default_root = False
        root = Tk()
        _support_default_root = True
        assert _default_root is None
        root.withdraw()
        root._temporary = True
    return root


def _destroy_temp_root(master):
    if getattr(master, '_temporary', False):
        try:
            master.destroy()
        except TclError:
            pass


def _tkerror(err):
    """Internal function."""
    pass


def _exit(code=0):
    """Internal function. Calling it will raise the exception SystemExit."""
    try:
        code = int(code)
    except ValueError:
        pass
    raise SystemExit(code)


_varnum = 0


class Variable:
    """Class to define value holders for e.g. buttons.

    Subclasses StringVar, IntVar, DoubleVar, BooleanVar are specializations
    that constrain the type of the value returned from get()."""
    _default = ""
    _tk = None
    _tclCommands = None

    def __init__(self, master=None, value=None, name=None):
        """Construct a variable

        MASTER can be given as master widget.
        VALUE is an optional value (defaults to "")
        NAME is an optional Tcl name (defaults to PY_VARnum).

        If NAME matches an existing variable and VALUE is omitted
        then the existing value is retained.
        """
        # check for type of NAME parameter to override weird error message
        # raised from Modules/_tkinter.c:SetVar like:
        # TypeError: setvar() takes exactly 3 arguments (2 given)
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be a string")
        global _varnum
        if master is None:
            master = _get_default_root('create variable')
        self._root = master._root()
        self._tk = master.tk
        if name:
            self._name = name
        else:
            self._name = 'PY_VAR' + repr(_varnum)
            _varnum += 1
        if value is not None:
            self.initialize(value)
        elif not self._tk.getboolean(self._tk.call("info", "exists", self._name)):
            self.initialize(self._default)

    def __del__(self):
        """Unset the variable in Tcl."""
        if self._tk is None:
            return
        if self._tk.getboolean(self._tk.call("info", "exists", self._name)):
            self._tk.globalunsetvar(self._name)
        if self._tclCommands is not None:
            for name in self._tclCommands:
                self._tk.deletecommand(name)
            self._tclCommands = None

    def __str__(self):
        """Return the name of the variable in Tcl."""
        return self._name

    def set(self, value):
        """Set the variable to VALUE."""
        return self._tk.globalsetvar(self._name, value)

    initialize = set

    def get(self):
        """Return value of variable."""
        return self._tk.globalgetvar(self._name)

    def _register(self, callback):
        f = CallWrapper(callback, None, self._root).__call__
        cbname = repr(id(f))
        try:
            callback = callback.__func__
        except AttributeError:
            pass
        try:
            cbname = cbname + callback.__name__
        except AttributeError:
            pass
        self._tk.createcommand(cbname, f)
        if self._tclCommands is None:
            self._tclCommands = []
        self._tclCommands.append(cbname)
        return cbname

    def trace_add(self, mode, callback):
        """Define a trace callback for the variable.

        Mode is one of "read", "write", "unset", or a list or tuple of
        such strings.
        Callback must be a function which is called when the variable is
        read, written or unset.

        Return the name of the callback.
        """
        cbname = self._register(callback)
        self._tk.call('trace', 'add', 'variable',
                      self._name, mode, (cbname,))
        return cbname

    def trace_remove(self, mode, cbname):
        """Delete the trace callback for a variable.

        Mode is one of "read", "write", "unset" or a list or tuple of
        such strings.  Must be same as were specified in trace_add().
        cbname is the name of the callback returned from trace_add().
        """
        self._tk.call('trace', 'remove', 'variable',
                      self._name, mode, cbname)
        for m, ca in self.trace_info():
            if self._tk.splitlist(ca)[0] == cbname:
                break
        else:
            self._tk.deletecommand(cbname)
            try:
                self._tclCommands.remove(cbname)
            except ValueError:
                pass

    def trace_info(self):
        """Return all trace callback information."""
        splitlist = self._tk.splitlist
        return [(splitlist(k), v) for k, v in map(splitlist,
            splitlist(self._tk.call('trace', 'info', 'variable', self._name)))]

    def trace_variable(self, mode, callback):
        """Define a trace callback for the variable.

        MODE is one of "r", "w", "u" for read, write, undefine.
        CALLBACK must be a function which is called when
        the variable is read, written or undefined.

        Return the name of the callback.

        This deprecated method wraps a deprecated Tcl method removed
        in Tcl 9.0.  Use trace_add() instead.
        """
        import warnings
        warnings.warn(
                "trace_variable() is deprecated and not supported with Tcl 9; "
