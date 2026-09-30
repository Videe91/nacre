# Author: Steven J. Bethard <steven.bethard@gmail.com>.
# New maintainer as of 29 August 2019:  Raymond Hettinger <raymond.hettinger@gmail.com>

"""Command-line parsing library

This module is an optparse-inspired command-line parsing library that:

    - handles both optional and positional arguments
    - produces highly informative usage messages
    - supports parsers that dispatch to sub-parsers

The following is a simple usage example that sums integers from the
command-line and writes the result to a file::

    parser = argparse.ArgumentParser(
        description='sum the integers at the command line')
    parser.add_argument(
        'integers', metavar='int', nargs='+', type=int,
        help='an integer to be summed')
    parser.add_argument(
        '--log',
        help='the file where the sum should be written')
    args = parser.parse_args()
    with (open(args.log, 'w') if args.log is not None
          else contextlib.nullcontext(sys.stdout)) as log:
        log.write('%s' % sum(args.integers))

The module contains the following public classes:

    - ArgumentParser -- The main entry point for command-line parsing. As the
        example above shows, the add_argument() method is used to populate
        the parser with actions for optional and positional arguments. Then
        the parse_args() method is invoked to convert the args at the
        command-line into an object with attributes.

    - ArgumentError -- The exception raised by ArgumentParser objects when
        there are errors with the parser's actions. Errors raised while
        parsing the command-line are caught by ArgumentParser and emitted
        as command-line messages.

    - FileType -- A factory for defining types of files to be created. As the
        example above shows, instances of FileType are typically passed as
        the type= argument of add_argument() calls. Deprecated since
        Python 3.14.

    - Action -- The base class for parser actions. Typically actions are
        selected by passing strings like 'store_true' or 'append_const' to
        the action= argument of add_argument(). However, for greater
        customization of ArgumentParser actions, subclasses of Action may
        be defined and passed as the action= argument.

    - HelpFormatter, RawDescriptionHelpFormatter, RawTextHelpFormatter,
        ArgumentDefaultsHelpFormatter -- Formatter classes which
        may be passed as the formatter_class= argument to the
        ArgumentParser constructor. HelpFormatter is the default,
        RawDescriptionHelpFormatter and RawTextHelpFormatter tell the parser
        not to change the formatting for help text, and
        ArgumentDefaultsHelpFormatter adds information about argument defaults
        to the help.

All other classes in this module are considered implementation details.
(Also note that HelpFormatter and RawDescriptionHelpFormatter are only
considered public as object names -- the API of the formatter objects is
still considered an implementation detail.)
"""

__version__ = '1.1'
__all__ = [
    'ArgumentParser',
    'ArgumentError',
    'ArgumentTypeError',
    'BooleanOptionalAction',
    'FileType',
    'HelpFormatter',
    'ArgumentDefaultsHelpFormatter',
    'RawDescriptionHelpFormatter',
    'RawTextHelpFormatter',
    'MetavarTypeHelpFormatter',
    'Namespace',
    'Action',
    'ONE_OR_MORE',
    'OPTIONAL',
    'PARSER',
    'REMAINDER',
    'SUPPRESS',
    'ZERO_OR_MORE',
]


import os as _os
import re as _re
import sys as _sys

from gettext import gettext as _, ngettext

SUPPRESS = '==SUPPRESS=='

OPTIONAL = '?'
ZERO_OR_MORE = '*'
ONE_OR_MORE = '+'
PARSER = 'A...'
REMAINDER = '...'
_UNRECOGNIZED_ARGS_ATTR = '_unrecognized_args'

# =============================
# Utility functions and classes
# =============================

class _AttributeHolder(object):
    """Abstract base class that provides __repr__.

    The __repr__ method returns a string in the format::
        ClassName(attr=name, attr=name, ...)
    The attributes are determined either by a class-level attribute,
    '_kwarg_names', or by inspecting the instance __dict__.
    """

    def __repr__(self):
        type_name = type(self).__name__
        arg_strings = []
        star_args = {}
        for arg in self._get_args():
            arg_strings.append(repr(arg))
        for name, value in self._get_kwargs():
            if name.isidentifier():
                arg_strings.append('%s=%r' % (name, value))
            else:
                star_args[name] = value
        if star_args:
            arg_strings.append('**%s' % repr(star_args))
        return '%s(%s)' % (type_name, ', '.join(arg_strings))

    def _get_kwargs(self):
        return list(self.__dict__.items())

    def _get_args(self):
        return []


def _copy_items(items):
    if items is None:
        return []
    # The copy module is used only in the 'append' and 'append_const'
    # actions, and it is needed only when the default value isn't a list.
    # Delay its import for speeding up the common case.
    if type(items) is list:
        return items[:]
    import copy
    return copy.copy(items)


# ===============
# Formatting Help
# ===============


class HelpFormatter(object):
    """Formatter for generating usage messages and argument help strings.

    Only the name of this class is considered a public API. All the methods
    provided by the class are considered an implementation detail.
    """

    def __init__(
        self,
        prog,
        indent_increment=2,
        max_help_position=24,
        width=None,
        color=True,
    ):
        # default setting for width
        if width is None:
            import shutil
            width = shutil.get_terminal_size().columns
            width -= 2

        self._set_color(color)
        self._prog = prog
        self._indent_increment = indent_increment
        self._max_help_position = min(max_help_position,
                                      max(width - 20, indent_increment * 2))
        self._width = width

        self._current_indent = 0
        self._level = 0
        self._action_max_length = 0

        self._root_section = self._Section(self, None)
        self._current_section = self._root_section

        self._whitespace_matcher = _re.compile(r'\s+', _re.ASCII)
        self._long_break_matcher = _re.compile(r'\n\n\n+')

    def _set_color(self, color):
        from _colorize import can_colorize, decolor, get_theme

        if color and can_colorize():
            self._theme = get_theme(force_color=True).argparse
            self._decolor = decolor
        else:
            self._theme = get_theme(force_no_color=True).argparse
            self._decolor = lambda text: text

    # ===============================
    # Section and indentation methods
    # ===============================

    def _indent(self):
        self._current_indent += self._indent_increment
        self._level += 1

    def _dedent(self):
        self._current_indent -= self._indent_increment
        assert self._current_indent >= 0, 'Indent decreased below 0.'
        self._level -= 1

    class _Section(object):

        def __init__(self, formatter, parent, heading=None):
            self.formatter = formatter
            self.parent = parent
            self.heading = heading
            self.items = []

        def format_help(self):
            # format the indented section
            if self.parent is not None:
                self.formatter._indent()
            join = self.formatter._join_parts
            item_help = join([func(*args) for func, args in self.items])
            if self.parent is not None:
                self.formatter._dedent()

            # return nothing if the section was empty
            if not item_help:
                return ''

            # add the heading if the section was non-empty
            if self.heading is not SUPPRESS and self.heading is not None:
                current_indent = self.formatter._current_indent
                heading_text = _('%(heading)s:') % dict(heading=self.heading)
                t = self.formatter._theme
                heading = (
                    f'{" " * current_indent}'
                    f'{t.heading}{heading_text}{t.reset}\n'
                )
            else:
                heading = ''

            # join the section-initial newline, the heading and the help
            return join(['\n', heading, item_help, '\n'])

    def _add_item(self, func, args):
        self._current_section.items.append((func, args))

    # ========================
    # Message building methods
    # ========================

    def start_section(self, heading):
        self._indent()
        section = self._Section(self, self._current_section, heading)
        self._add_item(section.format_help, [])
        self._current_section = section

    def end_section(self):
        self._current_section = self._current_section.parent
        self._dedent()

    def add_text(self, text):
        if text is not SUPPRESS and text is not None:
            self._add_item(self._format_text, [text])

    def add_usage(self, usage, actions, groups, prefix=None):
        if usage is not SUPPRESS:
            args = usage, actions, groups, prefix
            self._add_item(self._format_usage, args)

    def add_argument(self, action):
        if action.help is not SUPPRESS:

            # find all invocations
            get_invocation = lambda x: self._decolor(self._format_action_invocation(x))
            invocation_lengths = [len(get_invocation(action)) + self._current_indent]
            for subaction in self._iter_indented_subactions(action):
                invocation_lengths.append(len(get_invocation(subaction)) + self._current_indent)

            # update the maximum item length
            action_length = max(invocation_lengths)
            self._action_max_length = max(self._action_max_length,
                                          action_length)

            # add the item to the list
            self._add_item(self._format_action, [action])

    def add_arguments(self, actions):
        for action in actions:
            self.add_argument(action)

    # =======================
    # Help-formatting methods
    # =======================

    def format_help(self):
        help = self._root_section.format_help()
        if help:
            help = self._long_break_matcher.sub('\n\n', help)
            help = help.strip('\n') + '\n'
        return help

    def _join_parts(self, part_strings):
        return ''.join([part
                        for part in part_strings
                        if part and part is not SUPPRESS])

    def _format_usage(self, usage, actions, groups, prefix):
        t = self._theme

        if prefix is None:
            prefix = _('usage: ')

        # if usage is specified, use that
        if usage is not None:
            usage = (
                t.prog_extra
                + usage
                % {"prog": f"{t.prog}{self._prog}{t.reset}{t.prog_extra}"}
                + t.reset
            )

        # if no optionals or positionals are available, usage is just prog
        elif usage is None and not actions:
            usage = f"{t.prog}{self._prog}{t.reset}"

        # if optionals and positionals are available, calculate usage
        elif usage is None:
            prog = '%(prog)s' % dict(prog=self._prog)

            parts, pos_start = self._get_actions_usage_parts(actions, groups)
            # build full usage string
            usage = ' '.join(filter(None, [prog, *parts]))

            # wrap the usage parts if it's too long
            text_width = self._width - self._current_indent
            if len(prefix) + len(self._decolor(usage)) > text_width:

                # break usage into wrappable parts
                opt_parts = parts[:pos_start]
                pos_parts = parts[pos_start:]

                # helper for wrapping lines
                def get_lines(parts, indent, prefix=None):
                    lines = []
                    line = []
                    indent_length = len(indent)
                    if prefix is not None:
                        line_len = len(prefix) - 1
                    else:
                        line_len = indent_length - 1
                    for part in parts:
                        part_len = len(self._decolor(part))
                        if line_len + 1 + part_len > text_width and line:
                            lines.append(indent + ' '.join(line))
                            line = []
                            line_len = indent_length - 1
                        line.append(part)
                        line_len += part_len + 1
                    if line:
                        lines.append(indent + ' '.join(line))
                    if prefix is not None:
                        lines[0] = lines[0][indent_length:]
                    return lines

                # if prog is short, follow it with optionals or positionals
                prog_len = len(self._decolor(prog))
                if len(prefix) + prog_len <= 0.75 * text_width:
                    indent = ' ' * (len(prefix) + prog_len + 1)
                    if opt_parts:
                        lines = get_lines([prog] + opt_parts, indent, prefix)
                        lines.extend(get_lines(pos_parts, indent))
                    elif pos_parts:
                        lines = get_lines([prog] + pos_parts, indent, prefix)
                    else:
                        lines = [prog]

                # if prog is long, put it on its own line
                else:
                    indent = ' ' * len(prefix)
                    parts = opt_parts + pos_parts
                    lines = get_lines(parts, indent)
                    if len(lines) > 1:
                        lines = []
                        lines.extend(get_lines(opt_parts, indent))
                        lines.extend(get_lines(pos_parts, indent))
                    lines = [prog] + lines

                # join lines into usage
                usage = '\n'.join(lines)

            usage = usage.removeprefix(prog)
            usage = f"{t.prog}{prog}{t.reset}{usage}"

        # prefix with 'usage:'
        return f'{t.usage}{prefix}{t.reset}{usage}\n\n'

    def _is_long_option(self, string):
        return len(string) > 2

    def _get_actions_usage_parts(self, actions, groups):
        """Get usage parts with split index for optionals/positionals.

        Returns (parts, pos_start) where pos_start is the index in parts
        where positionals begin.
        This preserves mutually exclusive group formatting across the
        optionals/positionals boundary (gh-75949).
        """
        actions = [action for action in actions if action.help is not SUPPRESS]
        # group actions by mutually exclusive groups
        action_groups = dict.fromkeys(actions)
        for group in groups:
            for action in group._group_actions:
                if action in action_groups:
                    action_groups[action] = group
        # positional arguments keep their position
        positionals = []
        for action in actions:
            if not action.option_strings:
                group = action_groups.pop(action)
                if group:
                    group_actions = [
                        action2 for action2 in group._group_actions
                        if action2.option_strings and
                           action_groups.pop(action2, None)
                    ] + [action]
                    positionals.append((group.required, group_actions))
                else:
                    positionals.append((None, [action]))
        # the remaining optional arguments are sorted by the position of
        # the first option in the group
        optionals = []
        for action in actions:
            if action.option_strings and action in action_groups:
                group = action_groups.pop(action)
                if group:
                    group_actions = [action] + [
                        action2 for action2 in group._group_actions
                        if action2.option_strings and
                           action_groups.pop(action2, None)
                    ]
