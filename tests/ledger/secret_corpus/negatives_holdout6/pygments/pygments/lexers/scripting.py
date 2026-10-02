"""
    pygments.lexers.scripting
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Lexer for scripting and embedded languages.

    :copyright: Copyright 2006-present by the Pygments team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re

from pygments.lexer import RegexLexer, include, bygroups, default, combined, \
    words
from pygments.token import Text, Comment, Operator, Keyword, Name, String, \
    Number, Punctuation, Error, Whitespace, Other
from pygments.util import get_bool_opt, get_list_opt

__all__ = ['LuaLexer', 'LuauLexer', 'MoonScriptLexer', 'ChaiscriptLexer', 'LSLLexer',
           'AppleScriptLexer', 'RexxLexer', 'MOOCodeLexer', 'HybrisLexer',
           'EasytrieveLexer', 'JclLexer', 'MiniScriptLexer']


def all_lua_builtins():
    from pygments.lexers._lua_builtins import MODULES
    return [w for values in MODULES.values() for w in values]

class LuaLexer(RegexLexer):
    """
    For Lua source code.

    Additional options accepted:

    `func_name_highlighting`
        If given and ``True``, highlight builtin function names
        (default: ``True``).
    `disabled_modules`
        If given, must be a list of module names whose function names
        should not be highlighted. By default, all modules are highlighted.

        To get a list of allowed modules have a look into the
        `_lua_builtins` module:

        .. sourcecode:: pycon

            >>> from pygments.lexers._lua_builtins import MODULES
            >>> MODULES.keys()
            ['string', 'coroutine', 'modules', 'io', 'basic', ...]
    """

    name = 'Lua'
    url = 'https://www.lua.org/'
    aliases = ['lua']
    filenames = ['*.lua', '*.wlua']
    mimetypes = ['text/x-lua', 'application/x-lua']
    version_added = ''

    _comment_multiline = r'(?:--\[(?P<level>=*)\[[\w\W]*?\](?P=level)\])'
    _comment_single = r'(?:--.*$)'
    _space = r'(?:\s+(?!\s))'
    _s = rf'(?:{_comment_multiline}|{_comment_single}|{_space})'
    # A lookahead-safe version of _s that avoids catastrophic backtracking.
    # The _comment_multiline pattern contains [\w\W]*? which, when used
    # inside a lookahead with a * quantifier, causes exponential blowup.
    # This version skips only whitespace; comments between an identifier
    # and a following [.:] or ( are rare enough to sacrifice.
    _s_la = r'\s'
    _name = r'(?:[^\W\d]\w*)'

    tokens = {
        'root': [
            # Lua allows a file to start with a shebang.
            (r'#!.*', Comment.Preproc),
            default('base'),
        ],
        'ws': [
            (_comment_multiline, Comment.Multiline),
            (_comment_single, Comment.Single),
            (_space, Whitespace),
        ],
        'base': [
            include('ws'),

            (r'(?i)0x[\da-f]*(\.[\da-f]*)?(p[+-]?\d+)?', Number.Hex),
            (r'(?i)(\d*\.\d+|\d+\.\d*)(e[+-]?\d+)?', Number.Float),
            (r'(?i)\d+e[+-]?\d+', Number.Float),
            (r'\d+', Number.Integer),

            # multiline strings
            (r'(?s)\[(=*)\[.*?\]\1\]', String),

            (r'::', Punctuation, 'label'),
            (r'\.{3}', Punctuation),
            (r'[+\-*%^&|#]|//?|>>|<<|\.\.|[=~<>]=?', Operator),
            (r'[\[\]{}().,:;]+', Punctuation),
            (r'(and|or|not)\b', Operator.Word),

            (words([
                'break', 'do', 'else', 'elseif', 'end', 'for', 'if', 'in',
                'repeat', 'return', 'then', 'until', 'while'
            ], suffix=r'\b'), Keyword.Reserved),
            (r'goto\b', Keyword.Reserved, 'goto'),
            (r'local\b', Keyword.Declaration),
            (r'(true|false|nil)\b', Keyword.Constant),

            (r'function\b', Keyword.Reserved, 'funcname'),

            (words(all_lua_builtins(), suffix=r'\b'), Name.Builtin),
            (fr'[A-Za-z_]\w*(?={_s_la}*\()', Name.Function),
            (fr'[A-Za-z_]\w*(?={_s_la}*[.:])', Name.Variable, 'varname'),
            (fr'[A-Za-z_]\w*(?={_s_la}*<.+?>)', Name.Variable, 'varname'),
            (r'[A-Za-z_]\w*', Name.Variable),

            ("'", String.Single, combined('stringescape', 'sqs')),
            ('"', String.Double, combined('stringescape', 'dqs'))
        ],

        'varname': [
            include('ws'),
            (r'\.\.', Operator, '#pop'),
            (r'[.:]', Punctuation),
            (r'<', Punctuation, 'attribute'),
            (rf'{_name}(?={_s_la}*[.:])', Name.Property),
            (rf'{_name}(?={_s_la}*\()', Name.Function, '#pop'),
            (_name, Name.Property, '#pop'),
        ],

        'funcname': [
            include('ws'),
            (r'[.:]', Punctuation),
            (rf'{_name}(?={_s_la}*[.:])', Name.Class),
            (_name, Name.Function, '#pop'),
            # inline function
            (r'\(', Punctuation, '#pop'),
        ],

        'goto': [
            include('ws'),
            (_name, Name.Label, '#pop'),
        ],

        'label': [
            include('ws'),
            (r'::', Punctuation, '#pop'),
            (_name, Name.Label),
        ],

        'attribute': [
            include('ws'),
            (r'>', Punctuation, '#pop:2'),
            (_name, Name.Attribute),
        ],

        'stringescape': [
            (r'\\([abfnrtv\\"\']|[\r\n]{1,2}|z\s*|x[0-9a-fA-F]{2}|\d{1,3}|'
             r'u\{[0-9a-fA-F]+\})', String.Escape),
        ],

        'sqs': [
            (r"'", String.Single, '#pop'),
            (r"[^\\']+", String.Single),
        ],

        'dqs': [
            (r'"', String.Double, '#pop'),
            (r'[^\\"]+', String.Double),
        ]
    }

    def __init__(self, **options):
        self.func_name_highlighting = get_bool_opt(
            options, 'func_name_highlighting', True)
        self.disabled_modules = get_list_opt(options, 'disabled_modules', [])

        self._functions = set()
        if self.func_name_highlighting:
            from pygments.lexers._lua_builtins import MODULES
            for mod, func in MODULES.items():
                if mod not in self.disabled_modules:
                    self._functions.update(func)
        RegexLexer.__init__(self, **options)

    def get_tokens_unprocessed(self, text):
        for index, token, value in \
                RegexLexer.get_tokens_unprocessed(self, text):
            if token is Name.Builtin and value not in self._functions:
                if '.' in value:
                    a, b = value.split('.')
                    yield index, Name, a
                    yield index + len(a), Punctuation, '.'
                    yield index + len(a) + 1, Name, b
                else:
                    yield index, Name, value
                continue
            yield index, token, value

def _luau_make_expression(should_pop, _s, _s_la):
    temp_list = [
        (r'0[xX][\da-fA-F_]*', Number.Hex, '#pop'),
        (r'0[bB][\d_]*', Number.Bin, '#pop'),
        (r'\.?\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?[\d_]+)?', Number.Float, '#pop'),

        (words((
            'true', 'false', 'nil'
        ), suffix=r'\b'), Keyword.Constant, '#pop'),

        (r'\[(=*)\[[.\n]*?\]\1\]', String, '#pop'),

        (r'(\.)([a-zA-Z_]\w*)(?=%s*[({"\'])', bygroups(Punctuation, Name.Function), '#pop'),
        (r'(\.)([a-zA-Z_]\w*)', bygroups(Punctuation, Name.Variable), '#pop'),

        (rf'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*(?={_s_la}*[({{"\'])', Name.Other, '#pop'),
        (r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*', Name, '#pop'),
    ]
    if should_pop:
        return temp_list
    return [entry[:2] for entry in temp_list]

def _luau_make_expression_special(should_pop):
    temp_list = [
        (r'\{', Punctuation, ('#pop', 'closing_brace_base', 'expression')),
        (r'\(', Punctuation, ('#pop', 'closing_parenthesis_base', 'expression')),

        (r'::?', Punctuation, ('#pop', 'type_end', 'type_start')),

        (r"'", String.Single, ('#pop', 'string_single')),
        (r'"', String.Double, ('#pop', 'string_double')),
        (r'`', String.Backtick, ('#pop', 'string_interpolated')),
    ]
    if should_pop:
        return temp_list
    return [(entry[0], entry[1], entry[2][1:]) for entry in temp_list]

class LuauLexer(RegexLexer):
    """
    For Luau source code.

    Additional options accepted:

    `include_luau_builtins`
        If given and ``True``, automatically highlight Luau builtins
        (default: ``True``).
    `include_roblox_builtins`
        If given and ``True``, automatically highlight Roblox-specific builtins
        (default: ``False``).
    `additional_builtins`
        If given, must be a list of additional builtins to highlight.
    `disabled_builtins`
        If given, must be a list of builtins that will not be highlighted.
    """

    name = 'Luau'
    url = 'https://luau-lang.org/'
    aliases = ['luau']
    filenames = ['*.luau']
    version_added = '2.18'

    _comment_multiline = r'(?:--\[(?P<level>=*)\[[\w\W]*?\](?P=level)\])'
    _comment_single = r'(?:--.*$)'
    _s = r'(?:{}|{}|{})'.format(_comment_multiline, _comment_single, r'\s+')
    # Lookahead-safe version — avoids catastrophic backtracking from
    # [\w\W]*? inside _comment_multiline when combined with * quantifier.
    _s_la = r'\s'

    tokens = {
        'root': [
            (r'#!.*', Comment.Hashbang, 'base'),
            default('base'),
        ],

        'ws': [
            (_comment_multiline, Comment.Multiline),
            (_comment_single, Comment.Single),
            (r'\s+', Whitespace),
        ],

        'base': [
            include('ws'),

            *_luau_make_expression_special(False),
            (r'\.\.\.', Punctuation),

            (rf'type\b(?={_s}+[a-zA-Z_])', Keyword.Reserved, 'type_declaration'),
            (rf'export\b(?={_s}+[a-zA-Z_])', Keyword.Reserved),

            (r'(?:\.\.|//|[+\-*\/%^<>=])=?', Operator, 'expression'),
            (r'~=', Operator, 'expression'),

            (words((
                'and', 'or', 'not'
            ), suffix=r'\b'), Operator.Word, 'expression'),

            (words((
                'elseif', 'for', 'if', 'in', 'repeat', 'return', 'until',
                'while'), suffix=r'\b'), Keyword.Reserved, 'expression'),
            (r'local\b', Keyword.Declaration, 'expression'),

            (r'function\b', Keyword.Reserved, ('expression', 'func_name')),

            (r'[\])};]+', Punctuation),

            include('expression_static'),
            *_luau_make_expression(False, _s, _s_la),

            (r'[\[.,]', Punctuation, 'expression'),
        ],
        'expression_static': [
            (words((
                'break', 'continue', 'do', 'else', 'elseif', 'end', 'for',
                'if', 'in', 'repeat', 'return', 'then', 'until', 'while'),
                suffix=r'\b'), Keyword.Reserved),
        ],
        'expression': [
            include('ws'),

            (r'if\b', Keyword.Reserved, ('ternary', 'expression')),

            (r'local\b', Keyword.Declaration),
            *_luau_make_expression_special(True),
            (r'\.\.\.', Punctuation, '#pop'),

            (r'function\b', Keyword.Reserved, 'func_name'),

            include('expression_static'),
            *_luau_make_expression(True, _s, _s_la),

            default('#pop'),
        ],
        'ternary': [
            include('ws'),

            (r'else\b', Keyword.Reserved, '#pop'),
            (words((
                'then', 'elseif',
            ), suffix=r'\b'), Operator.Reserved, 'expression'),

            default('#pop'),
        ],

        'closing_brace_pop': [
            (r'\}', Punctuation, '#pop'),
        ],
        'closing_parenthesis_pop': [
            (r'\)', Punctuation, '#pop'),
        ],
        'closing_gt_pop': [
            (r'>', Punctuation, '#pop'),
        ],

        'closing_parenthesis_base': [
            include('closing_parenthesis_pop'),
            include('base'),
        ],
        'closing_parenthesis_type': [
            include('closing_parenthesis_pop'),
            include('type'),
        ],
        'closing_brace_base': [
            include('closing_brace_pop'),
            include('base'),
        ],
        'closing_brace_type': [
            include('closing_brace_pop'),
            include('type'),
        ],
        'closing_gt_type': [
            include('closing_gt_pop'),
            include('type'),
        ],

        'string_escape': [
            (r'\\z\s*', String.Escape),
            (r'\\(?:[abfnrtvz\\"\'`\{\n])|[\r\n]{1,2}|x[\da-fA-F]{2}|\d{1,3}|'
             r'u\{\}[\da-fA-F]*\}', String.Escape),
        ],
        'string_single': [
            include('string_escape'),

            (r"'", String.Single, "#pop"),
            (r"[^\\']+", String.Single),
        ],
        'string_double': [
            include('string_escape'),

            (r'"', String.Double, "#pop"),
            (r'[^\\"]+', String.Double),
        ],
        'string_interpolated': [
            include('string_escape'),

            (r'\{', Punctuation, ('closing_brace_base', 'expression')),

            (r'`', String.Backtick, "#pop"),
            (r'[^\\`\{]+', String.Backtick),
        ],

        'func_name': [
            include('ws'),

            (r'[.:]', Punctuation),
            (rf'[a-zA-Z_]\w*(?={_s_la}*[.:])', Name.Class),
            (r'[a-zA-Z_]\w*', Name.Function),

            (r'<', Punctuation, 'closing_gt_type'),

            (r'\(', Punctuation, '#pop'),
        ],

        'type': [
            include('ws'),

            (r'\(', Punctuation, 'closing_parenthesis_type'),
            (r'\{', Punctuation, 'closing_brace_type'),
            (r'<', Punctuation, 'closing_gt_type'),

            (r"'", String.Single, 'string_single'),
            (r'"', String.Double, 'string_double'),

            (r'[|&\.,\[\]:=]+', Punctuation),
            (r'->', Punctuation),

            (r'typeof\(', Name.Builtin, ('closing_parenthesis_base',
                                         'expression')),
            (r'[a-zA-Z_]\w*', Name.Class),
        ],
        'type_start': [
            include('ws'),

            (r'\(', Punctuation, ('#pop', 'closing_parenthesis_type')),
            (r'\{', Punctuation, ('#pop', 'closing_brace_type')),
            (r'<', Punctuation, ('#pop', 'closing_gt_type')),

            (r"'", String.Single, ('#pop', 'string_single')),
            (r'"', String.Double, ('#pop', 'string_double')),

            (r'typeof\(', Name.Builtin, ('#pop', 'closing_parenthesis_base',
                                         'expression')),
            (r'[a-zA-Z_]\w*', Name.Class, '#pop'),
        ],
        'type_end': [
            include('ws'),

            (r'[|&\.]', Punctuation, 'type_start'),
            (r'->', Punctuation, 'type_start'),

            (r'<', Punctuation, 'closing_gt_type'),

            default('#pop'),
        ],
        'type_declaration': [
            include('ws'),

            (r'[a-zA-Z_]\w*', Name.Class),
            (r'<', Punctuation, 'closing_gt_type'),

            (r'=', Punctuation, ('#pop', 'type_end', 'type_start')),
        ],
    }

    def __init__(self, **options):
        self.include_luau_builtins = get_bool_opt(
            options, 'include_luau_builtins', True)
        self.include_roblox_builtins = get_bool_opt(
            options, 'include_roblox_builtins', False)
        self.additional_builtins = get_list_opt(options, 'additional_builtins', [])
        self.disabled_builtins = get_list_opt(options, 'disabled_builtins', [])

        self._builtins = set(self.additional_builtins)
        if self.include_luau_builtins:
            from pygments.lexers._luau_builtins import LUAU_BUILTINS
            self._builtins.update(LUAU_BUILTINS)
        if self.include_roblox_builtins:
            from pygments.lexers._luau_builtins import ROBLOX_BUILTINS
            self._builtins.update(ROBLOX_BUILTINS)
        if self.additional_builtins:
            self._builtins.update(self.additional_builtins)
        self._builtins.difference_update(self.disabled_builtins)

        RegexLexer.__init__(self, **options)

    def get_tokens_unprocessed(self, text):
        for index, token, value in \
                RegexLexer.get_tokens_unprocessed(self, text):
            if token is Name or token is Name.Other:
                split_value = value.split('.')
                complete_value = []
                new_index = index
