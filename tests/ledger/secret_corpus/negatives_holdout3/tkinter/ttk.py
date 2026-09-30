"""Ttk wrapper.

This module provides classes to allow using Tk themed widget set.

Ttk is based on a revised and enhanced version of
TIP #48 (http://tip.tcl.tk/48) specified style engine.

Its basic idea is to separate, to the extent possible, the code
implementing a widget's behavior from the code implementing its
appearance. Widget class bindings are primarily responsible for
maintaining the widget state and invoking callbacks, all aspects
of the widgets appearance lies at Themes.
"""

__version__ = "0.3.1"

__author__ = "Guilherme Polo <ggpolo@gmail.com>"

__all__ = ["Button", "Checkbutton", "Combobox", "Entry", "Frame", "Label",
           "Labelframe", "LabelFrame", "Menubutton", "Notebook", "Panedwindow",
           "PanedWindow", "Progressbar", "Radiobutton", "Scale", "Scrollbar",
           "Separator", "Sizegrip", "Spinbox", "Style", "Treeview",
           # Extensions
           "LabeledScale", "OptionMenu",
           # functions
           "tclobjs_to_py", "setup_master"]

import tkinter
from tkinter import _flatten, _join, _stringify, _splitdict


def _format_optvalue(value, script=False):
    """Internal function."""
    if script:
        # if caller passes a Tcl script to tk.call, all the values need to
        # be grouped into words (arguments to a command in Tcl dialect)
        value = _stringify(value)
    elif isinstance(value, (list, tuple)):
        value = _join(value)
    return value

def _format_optdict(optdict, script=False, ignore=None):
    """Formats optdict to a tuple to pass it to tk.call.

    E.g. (script=False):
      {'foreground': 'blue', 'padding': [1, 2, 3, 4]} returns:
      ('-foreground', 'blue', '-padding', '1 2 3 4')"""

    opts = []
    for opt, value in optdict.items():
        if not ignore or opt not in ignore:
            opts.append("-%s" % opt)
            if value is not None:
                opts.append(_format_optvalue(value, script))

    return _flatten(opts)

def _mapdict_values(items):
    # each value in mapdict is expected to be a sequence, where each item
    # is another sequence containing a state (or several) and a value
    # E.g. (script=False):
    #   [('active', 'selected', 'grey'), ('focus', [1, 2, 3, 4])]
    #   returns:
    #   ['active selected', 'grey', 'focus', [1, 2, 3, 4]]
    opt_val = []
    for *state, val in items:
        if len(state) == 1:
            # if it is empty (something that evaluates to False), then
            # format it to Tcl code to denote the "normal" state
            state = state[0] or ''
        else:
            # group multiple states
            state = ' '.join(state) # raise TypeError if not str
        opt_val.append(state)
        if val is not None:
            opt_val.append(val)
    return opt_val

def _format_mapdict(mapdict, script=False):
    """Formats mapdict to pass it to tk.call.

    E.g. (script=False):
      {'expand': [('active', 'selected', 'grey'), ('focus', [1, 2, 3, 4])]}

      returns:

      ('-expand', '{active selected} grey focus {1, 2, 3, 4}')"""

    opts = []
    for opt, value in mapdict.items():
        opts.extend(("-%s" % opt,
                     _format_optvalue(_mapdict_values(value), script)))

    return _flatten(opts)

def _format_elemcreate(etype, script=False, *args, **kw):
    """Formats args and kw according to the given element factory etype."""
    specs = ()
    opts = ()
    if etype == "image": # define an element based on an image
        # first arg should be the default image name
        iname = args[0]
        # next args, if any, are statespec/value pairs which is almost
        # a mapdict, but we just need the value
        imagespec = (iname, *_mapdict_values(args[1:]))
        if script:
            specs = (imagespec,)
        else:
            specs = (_join(imagespec),)
        opts = _format_optdict(kw, script)

    if etype == "vsapi":
        # define an element whose visual appearance is drawn using the
        # Microsoft Visual Styles API which is responsible for the
        # themed styles on Windows XP and Vista.
        # Availability: Tk 8.6, Windows XP and Vista.
        if len(args) < 3:
            class_name, part_id = args
            statemap = (((), 1),)
        else:
            class_name, part_id, statemap = args
        specs = (class_name, part_id, tuple(_mapdict_values(statemap)))
        opts = _format_optdict(kw, script)

    elif etype == "from": # clone an element
        # it expects a themename and optionally an element to clone from,
        # otherwise it will clone {} (empty element)
        specs = (args[0],) # theme name
        if len(args) > 1: # elementfrom specified
            opts = (_format_optvalue(args[1], script),)

    if script:
        specs = _join(specs)
        opts = ' '.join(opts)
        return specs, opts
    else:
        return *specs, opts


def _format_layoutlist(layout, indent=0, indent_size=2):
    """Formats a layout list so we can pass the result to ttk::style
    layout and ttk::style settings. Note that the layout doesn't have to
    be a list necessarily.

    E.g.:
      [("Menubutton.background", None),
       ("Menubutton.button", {"children":
           [("Menubutton.focus", {"children":
               [("Menubutton.padding", {"children":
                [("Menubutton.label", {"side": "left", "expand": 1})]
               })]
           })]
       }),
       ("Menubutton.indicator", {"side": "right"})
      ]

      returns:

      Menubutton.background
      Menubutton.button -children {
        Menubutton.focus -children {
          Menubutton.padding -children {
            Menubutton.label -side left -expand 1
          }
        }
      }
      Menubutton.indicator -side right"""
    script = []

    for layout_elem in layout:
        elem, opts = layout_elem
        opts = opts or {}
        fopts = ' '.join(_format_optdict(opts, True, ("children",)))
        head = "%s%s%s" % (' ' * indent, elem, (" %s" % fopts) if fopts else '')

        if "children" in opts:
            script.append(head + " -children {")
            indent += indent_size
            newscript, indent = _format_layoutlist(opts['children'], indent,
                indent_size)
            script.append(newscript)
            indent -= indent_size
            script.append('%s}' % (' ' * indent))
        else:
            script.append(head)

    return '\n'.join(script), indent

def _script_from_settings(settings):
    """Returns an appropriate script, based on settings, according to
    theme_settings definition to be used by theme_settings and
    theme_create."""
    script = []
    # a script will be generated according to settings passed, which
    # will then be evaluated by Tcl
    for name, opts in settings.items():
        # will format specific keys according to Tcl code
        if opts.get('configure'): # format 'configure'
            s = ' '.join(_format_optdict(opts['configure'], True))
            script.append("ttk::style configure %s %s;" % (name, s))

        if opts.get('map'): # format 'map'
            s = ' '.join(_format_mapdict(opts['map'], True))
            script.append("ttk::style map %s %s;" % (name, s))

        if 'layout' in opts: # format 'layout' which may be empty
            if not opts['layout']:
                s = 'null' # could be any other word, but this one makes sense
            else:
                s, _ = _format_layoutlist(opts['layout'])
            script.append("ttk::style layout %s {\n%s\n}" % (name, s))

        if opts.get('element create'): # format 'element create'
            eopts = opts['element create']
            etype = eopts[0]

            # find where args end, and where kwargs start
            argc = 1 # etype was the first one
            while argc < len(eopts) and not hasattr(eopts[argc], 'items'):
                argc += 1

            elemargs = eopts[1:argc]
            elemkw = eopts[argc] if argc < len(eopts) and eopts[argc] else {}
            specs, eopts = _format_elemcreate(etype, True, *elemargs, **elemkw)

            script.append("ttk::style element create %s %s %s %s" % (
                name, etype, specs, eopts))

    return '\n'.join(script)

def _list_from_statespec(stuple):
    """Construct a list from the given statespec tuple according to the
    accepted statespec accepted by _format_mapdict."""
    if isinstance(stuple, str):
        return stuple
    result = []
    it = iter(stuple)
    for state, val in zip(it, it):
        if hasattr(state, 'typename'):  # this is a Tcl object
            state = str(state).split()
        elif isinstance(state, str):
            state = state.split()
        elif not isinstance(state, (tuple, list)):
            state = (state,)
        if hasattr(val, 'typename'):
            val = str(val)
        result.append((*state, val))

    return result

def _list_from_layouttuple(tk, ltuple):
    """Construct a list from the tuple returned by ttk::layout, this is
    somewhat the reverse of _format_layoutlist."""
    ltuple = tk.splitlist(ltuple)
    res = []

    indx = 0
    while indx < len(ltuple):
        name = ltuple[indx]
        opts = {}
        res.append((name, opts))
        indx += 1

        while indx < len(ltuple): # grab name's options
            opt, val = ltuple[indx:indx + 2]
            if not opt.startswith('-'): # found next name
                break

            opt = opt[1:] # remove the '-' from the option
            indx += 2

            if opt == 'children':
                val = _list_from_layouttuple(tk, val)

            opts[opt] = val

    return res

def _val_or_dict(tk, options, *args):
    """Format options then call Tk command with args and options and return
    the appropriate result.

    If no option is specified, a dict is returned. If an option is
    specified with the None value, the value for that option is returned.
    Otherwise, the function just sets the passed options and the caller
    shouldn't be expecting a return value anyway."""
    options = _format_optdict(options)
    res = tk.call(*(args + options))

    if len(options) % 2: # option specified without a value, return its value
        return res

    return _splitdict(tk, res, conv=_tclobj_to_py)

def _convert_stringval(value):
    """Converts a value to, hopefully, a more appropriate Python object."""
    value = str(value)
    try:
        value = int(value)
    except (ValueError, TypeError):
        pass

    return value

def _to_number(x):
    if isinstance(x, str):
        if '.' in x:
            x = float(x)
        else:
            x = int(x)
    return x

def _tclobj_to_py(val):
    """Return value converted from Tcl object to Python object."""
    if val and hasattr(val, '__len__') and not isinstance(val, str):
        if getattr(val[0], 'typename', None) == 'StateSpec':
            val = _list_from_statespec(val)
        else:
            val = list(map(_convert_stringval, val))

    elif hasattr(val, 'typename'): # some other (single) Tcl object
        val = _convert_stringval(val)

    if isinstance(val, tuple) and len(val) == 0:
        return ''
    return val

def tclobjs_to_py(adict):
    """Returns adict with its values converted from Tcl objects to Python
    objects."""
    for opt, val in adict.items():
        adict[opt] = _tclobj_to_py(val)

    return adict

def setup_master(master=None):
    """If master is not None, itself is returned. If master is None,
    the default master is returned if there is one, otherwise a new
    master is created and returned.

    If it is not allowed to use the default root and master is None,
    RuntimeError is raised."""
    if master is None:
        master = tkinter._get_default_root()
    return master


class Style(object):
    """Manipulate style database."""

    _name = "ttk::style"

    def __init__(self, master=None):
        master = setup_master(master)
        self.master = master
        self.tk = self.master.tk


    def configure(self, style, query_opt=None, **kw):
        """Query or sets the default value of the specified option(s) in
        style.

        Each key in kw is an option and each value is either a string or
        a sequence identifying the value for that option."""
        if query_opt is not None:
            kw[query_opt] = None
        result = _val_or_dict(self.tk, kw, self._name, "configure", style)
        if result or query_opt:
            return result


    def map(self, style, query_opt=None, **kw):
        """Query or sets dynamic values of the specified option(s) in
        style.

        Each key in kw is an option and each value should be a list or a
        tuple (usually) containing statespecs grouped in tuples, or list,
        or something else of your preference. A statespec is compound of
        one or more states and then a value."""
        if query_opt is not None:
            result = self.tk.call(self._name, "map", style, '-%s' % query_opt)
            return _list_from_statespec(self.tk.splitlist(result))

        result = self.tk.call(self._name, "map", style, *_format_mapdict(kw))
        return {k: _list_from_statespec(self.tk.splitlist(v))
                for k, v in _splitdict(self.tk, result).items()}


    def lookup(self, style, option, state=None, default=None):
        """Returns the value specified for option in style.

        If state is specified it is expected to be a sequence of one
        or more states. If the default argument is set, it is used as
        a fallback value in case no specification for option is found."""
        state = ' '.join(state) if state else ''

        return self.tk.call(self._name, "lookup", style, '-%s' % option,
            state, default)


    def layout(self, style, layoutspec=None):
        """Define the widget layout for given style. If layoutspec is
        omitted, return the layout specification for given style.

        layoutspec is expected to be a list or an object different than
        None that evaluates to False if you want to "turn off" that style.
        If it is a list (or tuple, or something else), each item should be
        a tuple where the first item is the layout name and the second item
        should have the format described below:

        LAYOUTS

            A layout can contain the value None, if takes no options, or
            a dict of options specifying how to arrange the element.
            The layout mechanism uses a simplified version of the pack
            geometry manager: given an initial cavity, each element is
            allocated a parcel. Valid options/values are:

                side: whichside
                    Specifies which side of the cavity to place the
                    element; one of top, right, bottom or left. If
                    omitted, the element occupies the entire cavity.

                sticky: nswe
                    Specifies where the element is placed inside its
                    allocated parcel.

                children: [sublayout... ]
                    Specifies a list of elements to place inside the
                    element. Each element is a tuple (or other sequence)
                    where the first item is the layout name, and the other
                    is a LAYOUT."""
        lspec = None
        if layoutspec:
            lspec = _format_layoutlist(layoutspec)[0]
        elif layoutspec is not None: # will disable the layout ({}, '', etc)
            lspec = "null" # could be any other word, but this may make sense
                           # when calling layout(style) later

        return _list_from_layouttuple(self.tk,
            self.tk.call(self._name, "layout", style, lspec))


    def element_create(self, elementname, etype, *args, **kw):
        """Create a new element in the current theme of given etype."""
        *specs, opts = _format_elemcreate(etype, False, *args, **kw)
        self.tk.call(self._name, "element", "create", elementname, etype,
            *specs, *opts)


    def element_names(self):
        """Returns the list of elements defined in the current theme."""
        return tuple(n.lstrip('-') for n in self.tk.splitlist(
            self.tk.call(self._name, "element", "names")))


    def element_options(self, elementname):
        """Return the list of elementname's options."""
