"""
    pygments.lexers.dsls
    ~~~~~~~~~~~~~~~~~~~~

    Lexers for various domain-specific languages.

    :copyright: Copyright 2006-present by the Pygments team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

import re

from pygments.lexer import ExtendedRegexLexer, RegexLexer, bygroups, words, \
    include, default, this, using, combined
from pygments.token import Text, Comment, Operator, Keyword, Name, String, \
    Number, Punctuation, Whitespace

__all__ = ['ProtoBufLexer', 'ZeekLexer', 'PuppetLexer', 'RslLexer',
           'MscgenLexer', 'VGLLexer', 'AlloyLexer', 'PanLexer',
           'CrmshLexer', 'ThriftLexer', 'FlatlineLexer', 'SnowballLexer']


class ProtoBufLexer(RegexLexer):
    """
    Lexer for Protocol Buffer definition files.
    """

    name = 'Protocol Buffer'
    url = 'https://developers.google.com/protocol-buffers/'
    aliases = ['protobuf', 'proto']
    filenames = ['*.proto']
    version_added = '1.4'

    tokens = {
        'root': [
            (r'[ \t]+', Whitespace),
            (r'[,;{}\[\]()<>]', Punctuation),
            (r'/(\\\n)?/(\n|[\s\S]*?[^\\]\n)', Comment.Single),
            (r'/(\\\n)?\*[\s\S]*?\*(\\\n)?/', Comment.Multiline),
            (words((
                'import', 'option', 'optional', 'required', 'repeated',
                'reserved', 'default', 'packed', 'ctype', 'extensions', 'to',
                'max', 'rpc', 'returns', 'oneof', 'syntax'), prefix=r'\b', suffix=r'\b'),
             Keyword),
            (words((
                'int32', 'int64', 'uint32', 'uint64', 'sint32', 'sint64',
                'fixed32', 'fixed64', 'sfixed32', 'sfixed64',
                'float', 'double', 'bool', 'string', 'bytes'), suffix=r'\b'),
             Keyword.Type),
            (r'(true|false)\b', Keyword.Constant),
            (r'(package)(\s+)', bygroups(Keyword.Namespace, Whitespace), 'package'),
            (r'(message|extend)(\s+)',
             bygroups(Keyword.Declaration, Whitespace), 'message'),
            (r'(enum|group|service)(\s+)',
             bygroups(Keyword.Declaration, Whitespace), 'type'),
            (r'\".*?\"', String),
            (r'\'.*?\'', String),
            (r'(\d+\.\d*|\.\d+|\d+)[eE][+-]?\d+[LlUu]*', Number.Float),
            (r'(\d+\.\d*|\.\d+|\d+[fF])[fF]?', Number.Float),
            (r'(\-?(inf|nan))\b', Number.Float),
            (r'0x[0-9a-fA-F]+[LlUu]*', Number.Hex),
            (r'0[0-7]+[LlUu]*', Number.Oct),
            (r'\d+[LlUu]*', Number.Integer),
            (r'[+-=]', Operator),
            (r'([a-zA-Z_][\w.]*)([ \t]*)(=)',
             bygroups(Name.Attribute, Whitespace, Operator)),
            (r'[a-zA-Z_][\w.]*', Name),
        ],
        'package': [
            (r'[a-zA-Z_][\w\.]*', Name.Namespace, '#pop'),
            default('#pop'),
        ],
        'message': [
            (r'[a-zA-Z_]\w*', Name.Class, '#pop'),
            default('#pop'),
        ],
        'type': [
            (r'[a-zA-Z_]\w*', Name, '#pop'),
            default('#pop'),
        ],
    }


class ThriftLexer(RegexLexer):
    """
    For Thrift interface definitions.
    """
    name = 'Thrift'
    url = 'https://thrift.apache.org/'
    aliases = ['thrift']
    filenames = ['*.thrift']
    mimetypes = ['application/x-thrift']
    version_added = '2.1'

    tokens = {
        'root': [
            include('whitespace'),
            include('comments'),
            (r'"', String.Double, combined('stringescape', 'dqs')),
            (r'\'', String.Single, combined('stringescape', 'sqs')),
            (r'(namespace)(\s+)',
                bygroups(Keyword.Namespace, Whitespace), 'namespace'),
            (r'(enum|union|struct|service|exception)(\s+)',
                bygroups(Keyword.Declaration, Whitespace), 'class'),
            (r'((?:(?:[^\W\d]|\$)[\w.\[\]$<>]*\s+)+?)'  # return arguments
             r'((?:[^\W\d]|\$)[\w$]*)'                  # method name
             r'(\s*)(\()',                              # signature start
             bygroups(using(this), Name.Function, Whitespace, Operator)),
            include('keywords'),
            include('numbers'),
            (r'[&=]', Operator),
            (r'[:;,{}()<>\[\]]', Punctuation),
            (r'[a-zA-Z_](\.\w|\w)*', Name),
        ],
        'whitespace': [
            (r'\n', Whitespace),
            (r'\s+', Whitespace),
        ],
        'comments': [
            (r'#.*$', Comment),
            (r'//.*?\n', Comment),
            (r'/\*[\w\W]*?\*/', Comment.Multiline),
        ],
        'stringescape': [
            (r'\\([\\nrt"\'])', String.Escape),
        ],
        'dqs': [
            (r'"', String.Double, '#pop'),
            (r'[^\\"\n]+', String.Double),
        ],
        'sqs': [
            (r"'", String.Single, '#pop'),
            (r'[^\\\'\n]+', String.Single),
        ],
        'namespace': [
            (r'[a-z*](\.\w|\w)*', Name.Namespace, '#pop'),
            default('#pop'),
        ],
        'class': [
            (r'[a-zA-Z_]\w*', Name.Class, '#pop'),
            default('#pop'),
        ],
        'keywords': [
            (r'(async|oneway|extends|throws|required|optional)\b', Keyword),
            (r'(true|false)\b', Keyword.Constant),
            (r'(const|typedef)\b', Keyword.Declaration),
            (words((
                'cpp_namespace', 'cpp_include', 'cpp_type', 'java_package',
                'cocoa_prefix', 'csharp_namespace', 'delphi_namespace',
                'php_namespace', 'py_module', 'perl_package',
                'ruby_namespace', 'smalltalk_category', 'smalltalk_prefix',
                'xsd_all', 'xsd_optional', 'xsd_nillable', 'xsd_namespace',
                'xsd_attrs', 'include'), suffix=r'\b'),
             Keyword.Namespace),
            (words((
                'void', 'bool', 'byte', 'i16', 'i32', 'i64', 'double',
                'string', 'binary', 'map', 'list', 'set', 'slist',
                'senum'), suffix=r'\b'),
             Keyword.Type),
            (words((
                'BEGIN', 'END', '__CLASS__', '__DIR__', '__FILE__',
                '__FUNCTION__', '__LINE__', '__METHOD__', '__NAMESPACE__',
                'abstract', 'alias', 'and', 'args', 'as', 'assert', 'begin',
                'break', 'case', 'catch', 'class', 'clone', 'continue',
                'declare', 'def', 'default', 'del', 'delete', 'do', 'dynamic',
                'elif', 'else', 'elseif', 'elsif', 'end', 'enddeclare',
                'endfor', 'endforeach', 'endif', 'endswitch', 'endwhile',
                'ensure', 'except', 'exec', 'finally', 'float', 'for',
                'foreach', 'function', 'global', 'goto', 'if', 'implements',
                'import', 'in', 'inline', 'instanceof', 'interface', 'is',
                'lambda', 'module', 'native', 'new', 'next', 'nil', 'not',
                'or', 'pass', 'public', 'print', 'private', 'protected',
                'raise', 'redo', 'rescue', 'retry', 'register', 'return',
                'self', 'sizeof', 'static', 'super', 'switch', 'synchronized',
                'then', 'this', 'throw', 'transient', 'try', 'undef',
                'unless', 'unsigned', 'until', 'use', 'var', 'virtual',
                'volatile', 'when', 'while', 'with', 'xor', 'yield'),
                prefix=r'\b', suffix=r'\b'),
             Keyword.Reserved),
        ],
        'numbers': [
            (r'[+-]?(\d+\.\d+([eE][+-]?\d+)?|\.?\d+[eE][+-]?\d+)', Number.Float),
            (r'[+-]?0x[0-9A-Fa-f]+', Number.Hex),
            (r'[+-]?[0-9]+', Number.Integer),
        ],
    }


class ZeekLexer(RegexLexer):
    """
    For Zeek scripts.
    """
    name = 'Zeek'
    url = 'https://www.zeek.org/'
    aliases = ['zeek', 'bro']
    filenames = ['*.zeek', '*.bro']
    version_added = '2.5'

    _hex = r'[0-9a-fA-F]'
    _float = r'((\d*\.?\d+)|(\d+\.?\d*))([eE][-+]?\d+)?'
    _h = r'[A-Za-z0-9][-A-Za-z0-9]*'

    tokens = {
        'root': [
            include('whitespace'),
            include('comments'),
            include('directives'),
            include('attributes'),
            include('types'),
            include('keywords'),
            include('literals'),
            include('operators'),
            include('punctuation'),
            (r'((?:[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))*)(?=\s*\()',
                Name.Function),
            include('identifiers'),
        ],

        'whitespace': [
            (r'\n', Whitespace),
            (r'\s+', Whitespace),
            (r'(\\)(\n)', bygroups(Text, Whitespace)),
        ],

        'comments': [
            (r'#.*$', Comment),
        ],

        'directives': [
            (r'@(load-plugin|load-sigs|load|unload)\b.*$', Comment.Preproc),
            (r'@(DEBUG|DIR|FILENAME|deprecated|if|ifdef|ifndef|else|endif)\b', Comment.Preproc),
            (r'(@prefixes)(\s*)((\+?=).*)$', bygroups(Comment.Preproc,
                Whitespace, Comment.Preproc)),
        ],

        'attributes': [
            (words(('redef', 'priority', 'log', 'optional', 'default', 'add_func',
                    'delete_func', 'expire_func', 'read_expire', 'write_expire',
                    'create_expire', 'synchronized', 'persistent', 'rotate_interval',
                    'rotate_size', 'encrypt', 'raw_output', 'mergeable', 'error_handler',
                    'type_column', 'deprecated'),
                prefix=r'&', suffix=r'\b'),
             Keyword.Pseudo),
        ],

        'types': [
            (words(('any',
                    'enum', 'record', 'set', 'table', 'vector',
                    'function', 'hook', 'event',
                    'addr', 'bool', 'count', 'double', 'file', 'int', 'interval',
                    'pattern', 'port', 'string', 'subnet', 'time'),
                suffix=r'\b'),
             Keyword.Type),

            (r'(opaque)(\s+)(of)(\s+)((?:[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))*)\b',
                bygroups(Keyword.Type, Whitespace, Operator.Word, Whitespace, Keyword.Type)),

            (r'(type)(\s+)((?:[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))*)(\s*)(:)(\s*)\b(record|enum)\b',
                bygroups(Keyword, Whitespace, Name.Class, Whitespace, Operator, Whitespace, Keyword.Type)),

            (r'(type)(\s+)((?:[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))*)(\s*)(:)',
                bygroups(Keyword, Whitespace, Name, Whitespace, Operator)),

            (r'(redef)(\s+)(record|enum)(\s+)((?:[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))*)\b',
                bygroups(Keyword, Whitespace, Keyword.Type, Whitespace, Name.Class)),
        ],

        'keywords': [
            (words(('redef', 'export', 'if', 'else', 'for', 'while',
                    'return', 'break', 'next', 'continue', 'fallthrough',
                    'switch', 'default', 'case',
                    'add', 'delete',
                    'when', 'timeout', 'schedule'),
                suffix=r'\b'),
             Keyword),
            (r'(print)\b', Keyword),
            (r'(global|local|const|option)\b', Keyword.Declaration),
            (r'(module)(\s+)(([A-Za-z_]\w*)(?:::([A-Za-z_]\w*))*)\b',
                bygroups(Keyword.Namespace, Whitespace, Name.Namespace)),
        ],

        'literals': [
            (r'"', String, 'string'),

            # Not the greatest match for patterns, but generally helps
            # disambiguate between start of a pattern and just a division
            # operator.
            (r'/(?=.*/)', String.Regex, 'regex'),

            (r'[TF]\b', Keyword.Constant),

            # Port
            (r'\d{1,5}/(udp|tcp|icmp|unknown)\b', Number),

            # IPv4 Address
            (r'(\d{1,3}.){3}(\d{1,3})\b', Number),

            # IPv6 Address
            (r'\[([0-9a-fA-F]{0,4}:){2,7}([0-9a-fA-F]{0,4})?((\d{1,3}.){3}(\d{1,3}))?\]', Number),

            # Numeric
            (r'0[xX]' + _hex + r'+\b', Number.Hex),
            (_float + r'\s*(day|hr|min|sec|msec|usec)s?\b', Number.Float),
            (_float + r'\b', Number.Float),
            (r'(\d+)\b', Number.Integer),

            # Hostnames
            (_h + r'(\.' + _h + r')+', String),
        ],

        'operators': [
            (r'[!%*/+<=>~|&^-]', Operator),
            (r'([-+=&|]{2}|[+=!><-]=)', Operator),
            (r'(in|as|is|of)\b', Operator.Word),
            (r'\??\$', Operator),
        ],

        'punctuation': [
            (r'[{}()\[\],;.]', Punctuation),
            # The "ternary if", which uses '?' and ':', could instead be
            # treated as an Operator, but colons are more frequently used to
            # separate field/identifier names from their types, so the (often)
            # less-prominent Punctuation is used even with '?' for consistency.
            (r'[?:]', Punctuation),
        ],

        'identifiers': [
            (r'([a-zA-Z_]\w*)(::)', bygroups(Name, Punctuation)),
            (r'[a-zA-Z_]\w*', Name)
        ],

        'string': [
            (r'\\.', String.Escape),
            (r'%-?[0-9]*(\.[0-9]+)?[DTd-gsx]', String.Escape),
            (r'"', String, '#pop'),
            (r'.', String),
        ],

        'regex': [
            (r'\\.', String.Escape),
            (r'/', String.Regex, '#pop'),
            (r'.', String.Regex),
        ],
    }


BroLexer = ZeekLexer


class PuppetLexer(RegexLexer):
    """
    For Puppet configuration DSL.
    """
    name = 'Puppet'
    url = 'https://puppet.com/'
    aliases = ['puppet']
    filenames = ['*.pp']
    version_added = '1.6'

    tokens = {
        'root': [
            include('comments'),
            include('keywords'),
            include('names'),
            include('numbers'),
            include('operators'),
            include('strings'),

            (r'[]{}:(),;[]', Punctuation),
            (r'\s+', Whitespace),
        ],

        'comments': [
            (r'(\s*)(#.*)$', bygroups(Whitespace, Comment)),
            (r'/(\\\n)?[*][\s\S]*?[*](\\\n)?/', Comment.Multiline),
        ],

        'operators': [
            (r'(=>|\?|<|>|=|\+|-|/|\*|~|!|\|)', Operator),
            (r'(in|and|or|not)\b', Operator.Word),
        ],

        'names': [
            (r'[a-zA-Z_]\w*', Name.Attribute),
            (r'(\$\S+)(\[)(\S+)(\])', bygroups(Name.Variable, Punctuation,
                                               String, Punctuation)),
            (r'\$\S+', Name.Variable),
        ],

        'numbers': [
            # Copypasta from the Python lexer
            (r'(\d+\.\d*|\d*\.\d+)([eE][+-]?[0-9]+)?j?', Number.Float),
            (r'\d+[eE][+-]?[0-9]+j?', Number.Float),
            (r'0[0-7]+j?', Number.Oct),
            (r'0[xX][a-fA-F0-9]+', Number.Hex),
            (r'\d+L', Number.Integer.Long),
            (r'\d+j?', Number.Integer)
        ],

        'keywords': [
            # Left out 'group' and 'require'
            # Since they're often used as attributes
            (words((
                'absent', 'alert', 'alias', 'audit', 'augeas', 'before', 'case',
                'check', 'class', 'computer', 'configured', 'contained',
                'create_resources', 'crit', 'cron', 'debug', 'default',
                'define', 'defined', 'directory', 'else', 'elsif', 'emerg',
                'err', 'exec', 'extlookup', 'fail', 'false', 'file',
                'filebucket', 'fqdn_rand', 'generate', 'host', 'if', 'import',
                'include', 'info', 'inherits', 'inline_template', 'installed',
                'interface', 'k5login', 'latest', 'link', 'loglevel',
                'macauthorization', 'mailalias', 'maillist', 'mcx', 'md5',
                'mount', 'mounted', 'nagios_command', 'nagios_contact',
                'nagios_contactgroup', 'nagios_host', 'nagios_hostdependency',
                'nagios_hostescalation', 'nagios_hostextinfo', 'nagios_hostgroup',
                'nagios_service', 'nagios_servicedependency', 'nagios_serviceescalation',
                'nagios_serviceextinfo', 'nagios_servicegroup', 'nagios_timeperiod',
                'node', 'noop', 'notice', 'notify', 'package', 'present', 'purged',
                'realize', 'regsubst', 'resources', 'role', 'router', 'running',
                'schedule', 'scheduled_task', 'search', 'selboolean', 'selmodule',
                'service', 'sha1', 'shellquote', 'split', 'sprintf',
                'ssh_authorized_key', 'sshkey', 'stage', 'stopped', 'subscribe',
