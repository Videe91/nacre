"""
    pygments.lexers.jvm
    ~~~~~~~~~~~~~~~~~~~

    Pygments lexers for JVM languages.

    :copyright: Copyright 2006-present by the Pygments team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re

from pygments.lexer import Lexer, RegexLexer, include, bygroups, using, \
    this, combined, default, words
from pygments.token import Text, Comment, Operator, Keyword, Name, String, \
    Number, Punctuation, Whitespace
from pygments.util import shebang_matches
from pygments import unistring as uni

__all__ = ['JavaLexer', 'ScalaLexer', 'GosuLexer', 'GosuTemplateLexer',
           'GroovyLexer', 'IokeLexer', 'ClojureLexer', 'ClojureScriptLexer',
           'KotlinLexer', 'XtendLexer', 'AspectJLexer', 'CeylonLexer',
           'PigLexer', 'GoloLexer', 'JasminLexer', 'SarlLexer']


class JavaLexer(RegexLexer):
    """
    For Java source code.
    """

    name = 'Java'
    url = 'https://www.oracle.com/technetwork/java/'
    aliases = ['java']
    filenames = ['*.java']
    mimetypes = ['text/x-java']
    version_added = ''

    flags = re.MULTILINE | re.DOTALL

    tokens = {
        'root': [
            (r'(^\s*)((?:(?:public|private|protected|static|strictfp)(?:\s+))*)(record)\b',
             bygroups(Whitespace, using(this), Keyword.Declaration), 'class'),
            (r'[^\S\n]+', Whitespace),
            (r'(//.*?)(\n)', bygroups(Comment.Single, Whitespace)),
            (r'/\*.*?\*/', Comment.Multiline),
            # keywords: go before method names to avoid lexing "throw new XYZ"
            # as a method signature
            (r'(assert|break|case|catch|continue|default|do|else|finally|for|'
             r'if|goto|instanceof|new|return|switch|this|throw|try|while)\b',
             Keyword),
            # method names
            (r'((?:(?:[^\W\d]|\$)[\w.\[\]$<>?]*\s+)+?)'  # return arguments
             r'((?:[^\W\d]|\$)[\w$]*)'                  # method name
             r'(\s*)(\()',                              # signature start
             bygroups(using(this), Name.Function, Whitespace, Punctuation)),
            (r'@[^\W\d][\w.]*', Name.Decorator),
            (r'(abstract|const|enum|exports|extends|final|implements|native|non-sealed|'
             r'open|opens|permits|private|protected|provides|public|requires|sealed|static|strictfp|'
             r'super|synchronized|throws|to|transient|transitive|uses|volatile|with|yield)\b', Keyword.Declaration),
            (r'(boolean|byte|char|double|float|int|long|short|void)\b',
             Keyword.Type),
            (r'(package)(\s+)', bygroups(Keyword.Namespace, Whitespace), 'import'),
            (r'(true|false|null)\b', Keyword.Constant),
            (r'(class|interface)\b', Keyword.Declaration, 'class'),
            (r'(module)\b', Keyword.Declaration, 'module'),
            (r'(var)(\s+)', bygroups(Keyword.Declaration, Whitespace), 'var'),
            (r'(import(?:\s+(?:static|module))?)(\s+)', bygroups(Keyword.Namespace, Whitespace),
             'import'),
            (r'"""\n', String, 'multiline_string'),
            (r'"', String, 'string'),
            (r"'\\.'|'[^\\]'|'\\u[0-9a-fA-F]{4}'", String.Char),
            (r'(\.)((?:[^\W\d]|\$)[\w$]*)', bygroups(Punctuation,
                                                     Name.Attribute)),
            (r'^(\s*)(default)(:)', bygroups(Whitespace, Keyword, Punctuation)),
            (r'^(\s*)((?:[^\W\d]|\$)[\w$]*)(:)', bygroups(Whitespace, Name.Label,
                                                          Punctuation)),
            (r'([^\W\d]|\$)[\w$]*', Name),
            (r'([0-9][0-9_]*\.([0-9][0-9_]*)?|'
             r'\.[0-9][0-9_]*)'
             r'([eE][+\-]?[0-9][0-9_]*)?[fFdD]?|'
             r'[0-9][eE][+\-]?[0-9][0-9_]*[fFdD]?|'
             r'[0-9]([eE][+\-]?[0-9][0-9_]*)?[fFdD]|'
             r'0[xX]([0-9a-fA-F][0-9a-fA-F_]*\.?|'
             r'([0-9a-fA-F][0-9a-fA-F_]*)?\.[0-9a-fA-F][0-9a-fA-F_]*)'
             r'[pP][+\-]?[0-9][0-9_]*[fFdD]?', Number.Float),
            (r'0[xX][0-9a-fA-F][0-9a-fA-F_]*[lL]?', Number.Hex),
            (r'0[bB][01][01_]*[lL]?', Number.Bin),
            (r'0[0-7_]+[lL]?', Number.Oct),
            (r'0|[1-9][0-9_]*[lL]?', Number.Integer),
            (r'[~^*!%&\[\]<>|+=/?-]', Operator),
            (r'[{}();:.,]', Punctuation),
            (r'\n', Whitespace)
        ],
        'class': [
            (r'\s+', Text),
            (r'([^\W\d]|\$)[\w$]*', Name.Class, '#pop')
        ],
        'module': [
            (r'\s+', Text),
            (r'([^\W\d]|\$)[\w$]*', Name.Class, '#pop')
        ],
        'var': [
            (r'([^\W\d]|\$)[\w$]*', Name, '#pop')
        ],
        'import': [
            (r'[\w.]+\*?', Name.Namespace, '#pop')
        ],
        'multiline_string': [
            (r'"""', String, '#pop'),
            (r'"', String),
            include('string')
        ],
        'string': [
            (r'[^\\"]+', String),
            (r'\\\\', String),  # Escaped backslash
            (r'\\"', String),  # Escaped quote
            (r'\\', String),  # Bare backslash
            (r'"', String, '#pop'),  # Closing quote
        ],
    }


class AspectJLexer(JavaLexer):
    """
    For AspectJ source code.
    """

    name = 'AspectJ'
    url = 'http://www.eclipse.org/aspectj/'
    aliases = ['aspectj']
    filenames = ['*.aj']
    mimetypes = ['text/x-aspectj']
    version_added = '1.6'

    aj_keywords = {
        'aspect', 'pointcut', 'privileged', 'call', 'execution',
        'initialization', 'preinitialization', 'handler', 'get', 'set',
        'staticinitialization', 'target', 'args', 'within', 'withincode',
        'cflow', 'cflowbelow', 'annotation', 'before', 'after', 'around',
        'proceed', 'throwing', 'returning', 'adviceexecution', 'declare',
        'parents', 'warning', 'error', 'soft', 'precedence', 'thisJoinPoint',
        'thisJoinPointStaticPart', 'thisEnclosingJoinPointStaticPart',
        'issingleton', 'perthis', 'pertarget', 'percflow', 'percflowbelow',
        'pertypewithin', 'lock', 'unlock', 'thisAspectInstance'
    }
    aj_inter_type = {'parents:', 'warning:', 'error:', 'soft:', 'precedence:'}
    aj_inter_type_annotation = {'@type', '@method', '@constructor', '@field'}

    def get_tokens_unprocessed(self, text):
        for index, token, value in JavaLexer.get_tokens_unprocessed(self, text):
            if token is Name and value in self.aj_keywords:
                yield index, Keyword, value
            elif token is Name.Label and value in self.aj_inter_type:
                yield index, Keyword, value[:-1]
                yield index, Operator, value[-1]
            elif token is Name.Decorator and value in self.aj_inter_type_annotation:
                yield index, Keyword, value
            else:
                yield index, token, value


class ScalaLexer(RegexLexer):
    """
    For Scala source code.
    """

    name = 'Scala'
    url = 'http://www.scala-lang.org'
    aliases = ['scala']
    filenames = ['*.scala']
    mimetypes = ['text/x-scala']
    version_added = ''

    flags = re.MULTILINE | re.DOTALL

    opchar = '[!#%&*\\-\\/:?@^' + uni.combine('Sm', 'So') + ']'
    letter = '[_\\$' + uni.combine('Ll', 'Lu', 'Lo', 'Nl', 'Lt') + ']'
    upperLetter = '[' + uni.combine('Lu', 'Lt') + ']'
    letterOrDigit = f'(?:{letter}|[0-9])'
    letterOrDigitNoDollarSign = '(?:{}|[0-9])'.format(letter.replace('\\$', ''))
    alphaId = f'{letter}+'
    simpleInterpolatedVariable  = f'{letter}{letterOrDigitNoDollarSign}*'
    idrest = f'{letter}{letterOrDigit}*(?:(?<=_){opchar}+)?'
    idUpper = f'{upperLetter}{letterOrDigit}*(?:(?<=_){opchar}+)?'
    plainid = f'(?:{idrest}|{opchar}+)'
    backQuotedId = r'`[^`]+`'
    anyId = rf'(?:{plainid}|{backQuotedId})'
    notStartOfComment = r'(?!//|/\*)'
    endOfLineMaybeWithComment = r'(?=\s*(//|$))'

    keywords = (
        'new', 'return', 'throw', 'classOf', 'isInstanceOf', 'asInstanceOf',
        'else', 'if', 'then', 'do', 'while', 'for', 'yield', 'match', 'case',
        'catch', 'finally', 'try'
    )

    operators = (
        '<%', '=:=', '<:<', '<%<', '>:', '<:', '=', '==', '!=', '<=', '>=',
        '<>', '<', '>', '<-', '←', '->', '→', '=>', '⇒', '?', '@', '|', '-',
        '+', '*', '%', '~', '\\'
    )

    storage_modifiers = (
        'private', 'protected', 'synchronized', '@volatile', 'abstract',
        'final', 'lazy', 'sealed', 'implicit', 'override', '@transient',
        '@native'
    )

    tokens = {
        'root': [
            include('whitespace'),
            include('comments'),
            include('script-header'),
            include('imports'),
            include('exports'),
            include('storage-modifiers'),
            include('annotations'),
            include('using'),
            include('declarations'),
            include('inheritance'),
            include('extension'),
            include('end'),
            include('constants'),
            include('strings'),
            include('symbols'),
            include('singleton-type'),
            include('inline'),
            include('quoted'),
            include('keywords'),
            include('operators'),
            include('punctuation'),
            include('names'),
        ],

        # Includes:
        'whitespace': [
            (r'\s+', Whitespace),
        ],
        'comments': [
            (r'//.*?\n', Comment.Single),
            (r'/\*', Comment.Multiline, 'comment'),
        ],
        'script-header': [
            (r'^#!([^\n]*)$', Comment.Hashbang),
        ],
        'imports': [
            (r'\b(import)(\s+)', bygroups(Keyword, Whitespace), 'import-path'),
        ],
        'exports': [
            (r'\b(export)(\s+)(given)(\s+)',
                bygroups(Keyword, Whitespace, Keyword, Whitespace), 'export-path'),
            (r'\b(export)(\s+)', bygroups(Keyword, Whitespace), 'export-path'),
        ],
        'storage-modifiers': [
            (words(storage_modifiers, prefix=r'\b', suffix=r'\b'), Keyword),
            # Only highlight soft modifiers if they are eventually followed by
            # the correct keyword. Note that soft modifiers can be followed by a
            # sequence of regular modifiers; [a-z\s]* skips those, and we just
            # check that the soft modifier is applied to a supported statement.
            (r'\b(transparent|opaque|infix|open|inline)\b(?=[a-z\s]*\b'
             r'(def|val|var|given|type|class|trait|object|enum)\b)', Keyword),
        ],
        'annotations': [
            (rf'@{idrest}', Name.Decorator),
        ],
        'using': [
            # using is a soft keyword, can only be used in the first position of
            # a parameter or argument list.
            (r'(\()(\s*)(using)(\s)', bygroups(Punctuation, Whitespace, Keyword, Whitespace)),
        ],
        'declarations': [
            (rf'\b(def)\b(\s*){notStartOfComment}({anyId})?',
             bygroups(Keyword, Whitespace, Name.Function)),
            (rf'\b(trait)\b(\s*){notStartOfComment}({anyId})?',
                bygroups(Keyword, Whitespace, Name.Class)),
            (rf'\b(?:(case)(\s+))?(class|object|enum)\b(\s*){notStartOfComment}({anyId})?',
                bygroups(Keyword, Whitespace, Keyword, Whitespace, Name.Class)),
            (rf'(?<!\.)\b(type)\b(\s*){notStartOfComment}({anyId})?',
                bygroups(Keyword, Whitespace, Name.Class)),
            (r'\b(val|var)\b', Keyword.Declaration),
            (rf'\b(package)(\s+)(object)\b(\s*){notStartOfComment}({anyId})?',
                bygroups(Keyword, Whitespace, Keyword, Whitespace, Name.Namespace)),
            (r'\b(package)(\s+)', bygroups(Keyword, Whitespace), 'package'),
            (rf'\b(given)\b(\s*)({idUpper})',
                bygroups(Keyword, Whitespace, Name.Class)),
            (rf'\b(given)\b(\s*)({anyId})?',
                bygroups(Keyword, Whitespace, Name)),
        ],
        'inheritance': [
            (r'\b(extends|with|derives)\b(\s*)'
             rf'({idUpper}|{backQuotedId}|(?=\([^\)]+=>)|(?={plainid})|(?="))?',
                bygroups(Keyword, Whitespace, Name.Class)),
        ],
        'extension': [
            (r'\b(extension)(\s+)(?=[\[\(])', bygroups(Keyword, Whitespace)),
        ],
        'end': [
            # end is a soft keyword, should only be highlighted in certain cases
            (r'\b(end)(\s+)(if|while|for|match|new|extension|val|var)\b',
                bygroups(Keyword, Whitespace, Keyword)),
            (rf'\b(end)(\s+)({idUpper}){endOfLineMaybeWithComment}',
                bygroups(Keyword, Whitespace, Name.Class)),
            (rf'\b(end)(\s+)({backQuotedId}|{plainid})?{endOfLineMaybeWithComment}',
                bygroups(Keyword, Whitespace, Name.Namespace)),
        ],
        'punctuation': [
            (r'[{}()\[\];,.]', Punctuation),
            (r'(?<!:):(?!:)', Punctuation),
        ],
        'keywords': [
            (words(keywords, prefix=r'\b', suffix=r'\b'), Keyword),
        ],
        'operators': [
            (rf'({opchar}{{2,}})(\s+)', bygroups(Operator, Whitespace)),
            (r'/(?![/*])', Operator),
            (words(operators), Operator),
            (rf'(?<!{opchar})(!|&&|\|\|)(?!{opchar})', Operator),
        ],
        'constants': [
            (r'\b(this|super)\b', Name.Builtin.Pseudo),
            (r'(true|false|null)\b', Keyword.Constant),
            (r'0[xX][0-9a-fA-F_]*', Number.Hex),
            (r'([0-9][0-9_]*\.[0-9][0-9_]*|\.[0-9][0-9_]*)'
             r'([eE][+-]?[0-9][0-9_]*)?[fFdD]?', Number.Float),
            (r'[0-9]+([eE][+-]?[0-9]+)?[fFdD]', Number.Float),
            (r'[0-9]+([eE][+-]?[0-9]+)[fFdD]?', Number.Float),
            (r'[0-9]+[lL]', Number.Integer.Long),
            (r'[0-9]+', Number.Integer),
            (r'""".*?"""(?!")', String),
            (r'"(\\\\|\\"|[^"])*"', String),
            (r"(')(\\.)(')", bygroups(String.Char, String.Escape, String.Char)),
            (r"'[^\\]'|'\\u[0-9a-fA-F]{4}'", String.Char),
        ],
        "strings": [
            (r'[fs]"""', String, 'interpolated-string-triple'),
            (r'[fs]"', String, 'interpolated-string'),
            (r'raw"(\\\\|\\"|[^"])*"', String),
        ],
        'symbols': [
            (rf"('{plainid})(?!')", String.Symbol),
        ],
        'singleton-type': [
            (r'(\.)(type)\b', bygroups(Punctuation, Keyword)),
        ],
        'inline': [
            # inline is a soft modifier, only highlighted if followed by if,
            # match or parameters.
            (rf'\b(inline)(?=\s+({plainid}|{backQuotedId})\s*:)',
                Keyword),
            (r'\b(inline)\b(?=(?:.(?!\b(?:val|def|given)\b))*\b(if|match)\b)',
                Keyword),
        ],
        'quoted': [
            # '{...} or ${...}
            (r"['$]\{(?!')", Punctuation),
            # '[...]
            (r"'\[(?!')", Punctuation),
        ],
        'names': [
            (idUpper, Name.Class),
            (anyId, Name),
        ],

        # States
        'comment': [
            (r'[^/*]+', Comment.Multiline),
            (r'/\*', Comment.Multiline, '#push'),
            (r'\*/', Comment.Multiline, '#pop'),
            (r'[*/]', Comment.Multiline),
        ],
        'import-path': [
            (r'(?<=[\n;:])', Text, '#pop'),
            include('comments'),
            (r'\b(given)\b', Keyword),
            include('qualified-name'),
            (r'\{', Punctuation, 'import-path-curly-brace'),
        ],
        'import-path-curly-brace': [
            include('whitespace'),
            include('comments'),
            (r'\b(given)\b', Keyword),
            (r'=>', Operator),
            (r'\}', Punctuation, '#pop'),
            (r',', Punctuation),
            (r'[\[\]]', Punctuation),
            include('qualified-name'),
        ],
        'export-path': [
            (r'(?<=[\n;:])', Text, '#pop'),
            include('comments'),
            include('qualified-name'),
            (r'\{', Punctuation, 'export-path-curly-brace'),
        ],
        'export-path-curly-brace': [
            include('whitespace'),
            include('comments'),
            (r'=>', Operator),
            (r'\}', Punctuation, '#pop'),
            (r',', Punctuation),
            include('qualified-name'),
        ],
        'package': [
            (r'(?<=[\n;])', Text, '#pop'),
