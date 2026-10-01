# This module contains ``ast.unparse()``, defined here
# to improve the import time for the ``ast`` module.
import sys
from _ast import *
from ast import NodeVisitor
from contextlib import contextmanager, nullcontext
from enum import IntEnum, auto, _simple_enum

# Large float and imaginary literals get turned into infinities in the AST.
# We unparse those infinities to INFSTR.
_INFSTR = "1e" + repr(sys.float_info.max_10_exp + 1)

@_simple_enum(IntEnum)
class _Precedence:
    """Precedence table that originated from python grammar."""

    NAMED_EXPR = auto()      # <target> := <expr1>
    TUPLE = auto()           # <expr1>, <expr2>
    YIELD = auto()           # 'yield', 'yield from'
    TEST = auto()            # 'if'-'else', 'lambda'
    OR = auto()              # 'or'
    AND = auto()             # 'and'
    NOT = auto()             # 'not'
    CMP = auto()             # '<', '>', '==', '>=', '<=', '!=',
                             # 'in', 'not in', 'is', 'is not'
    EXPR = auto()
    BOR = EXPR               # '|'
    BXOR = auto()            # '^'
    BAND = auto()            # '&'
    SHIFT = auto()           # '<<', '>>'
    ARITH = auto()           # '+', '-'
    TERM = auto()            # '*', '@', '/', '%', '//'
    FACTOR = auto()          # unary '+', '-', '~'
    POWER = auto()           # '**'
    AWAIT = auto()           # 'await'
    ATOM = auto()

    def next(self):
        try:
            return self.__class__(self + 1)
        except ValueError:
            return self


_SINGLE_QUOTES = ("'", '"')
_MULTI_QUOTES = ('"""', "'''")
_ALL_QUOTES = (*_SINGLE_QUOTES, *_MULTI_QUOTES)

class Unparser(NodeVisitor):
    """Methods in this class recursively traverse an AST and
    output source code for the abstract syntax; original formatting
    is disregarded."""

    def __init__(self):
        self._source = []
        self._precedences = {}
        self._type_ignores = {}
        self._indent = 0
        self._in_try_star = False
        self._in_interactive = False

    def interleave(self, inter, f, seq):
        """Call f on each item in seq, calling inter() in between."""
        seq = iter(seq)
        try:
            f(next(seq))
        except StopIteration:
            pass
        else:
            for x in seq:
                inter()
                f(x)

    def items_view(self, traverser, items):
        """Traverse and separate the given *items* with a comma and append it to
        the buffer. If *items* is a single item sequence, a trailing comma
        will be added."""
        if len(items) == 1:
            traverser(items[0])
            self.write(",")
        else:
            self.interleave(lambda: self.write(", "), traverser, items)

    def maybe_newline(self):
        """Adds a newline if it isn't the start of generated source"""
        if self._source:
            self.write("\n")

    def maybe_semicolon(self):
        """Adds a "; " delimiter if it isn't the start of generated source"""
        if self._source:
            self.write("; ")

    def fill(self, text="", *, allow_semicolon=True):
        """Indent a piece of text and append it, according to the current
        indentation level, or only delineate with semicolon if applicable"""
        if self._in_interactive and not self._indent and allow_semicolon:
            self.maybe_semicolon()
            self.write(text)
        else:
            self.maybe_newline()
            self.write("    " * self._indent + text)

    def write(self, *text):
        """Add new source parts"""
        self._source.extend(text)

    @contextmanager
    def buffered(self, buffer = None):
        if buffer is None:
            buffer = []

        original_source = self._source
        self._source = buffer
        yield buffer
        self._source = original_source

    @contextmanager
    def block(self, *, extra = None):
        """A context manager for preparing the source for blocks. It adds
        the character':', increases the indentation on enter and decreases
        the indentation on exit. If *extra* is given, it will be directly
        appended after the colon character.
        """
        self.write(":")
        if extra:
            self.write(extra)
        self._indent += 1
        yield
        self._indent -= 1

    @contextmanager
    def delimit(self, start, end):
        """A context manager for preparing the source for expressions. It adds
        *start* to the buffer and enters, after exit it adds *end*."""

        self.write(start)
        yield
        self.write(end)

    def delimit_if(self, start, end, condition):
        if condition:
            return self.delimit(start, end)
        else:
            return nullcontext()

    def require_parens(self, precedence, node):
        """Shortcut to adding precedence related parens"""
        return self.delimit_if("(", ")", self.get_precedence(node) > precedence)

    def get_precedence(self, node):
        return self._precedences.get(node, _Precedence.TEST)

    def set_precedence(self, precedence, *nodes):
        for node in nodes:
            self._precedences[node] = precedence

    def get_raw_docstring(self, node):
        """If a docstring node is found in the body of the *node* parameter,
        return that docstring node, None otherwise.

        Logic mirrored from ``_PyAST_GetDocString``."""
        if not isinstance(
            node, (AsyncFunctionDef, FunctionDef, ClassDef, Module)
        ) or len(node.body) < 1:
            return None
        node = node.body[0]
        if not isinstance(node, Expr):
            return None
        node = node.value
        if isinstance(node, Constant) and isinstance(node.value, str):
            return node

    def get_type_comment(self, node):
        comment = self._type_ignores.get(node.lineno) or node.type_comment
        if comment is not None:
            return f" # type: {comment}"

    def traverse(self, node):
        if isinstance(node, list):
            for item in node:
                self.traverse(item)
        else:
            super().visit(node)

    # Note: as visit() resets the output text, do NOT rely on
    # NodeVisitor.generic_visit to handle any nodes (as it calls back in to
    # the subclass visit() method, which resets self._source to an empty list)
    def visit(self, node):
        """Outputs a source code string that, if converted back to an ast
        (using ast.parse) will generate an AST equivalent to *node*"""
        self._source = []
        self.traverse(node)
        return "".join(self._source)

    def _write_docstring_and_traverse_body(self, node):
        if (docstring := self.get_raw_docstring(node)):
            self._write_docstring(docstring)
            self.traverse(node.body[1:])
        else:
            self.traverse(node.body)

    def visit_Module(self, node):
        self._type_ignores = {
            ignore.lineno: f"ignore{ignore.tag}"
            for ignore in node.type_ignores
        }
        try:
            self._write_docstring_and_traverse_body(node)
        finally:
            self._type_ignores.clear()

    def visit_Interactive(self, node):
        self._in_interactive = True
        try:
            self._write_docstring_and_traverse_body(node)
        finally:
            self._in_interactive = False

    def visit_FunctionType(self, node):
        with self.delimit("(", ")"):
            self.interleave(
                lambda: self.write(", "), self.traverse, node.argtypes
            )

        self.write(" -> ")
        self.traverse(node.returns)

    def visit_Expr(self, node):
        self.fill()
        self.set_precedence(_Precedence.YIELD, node.value)
        self.traverse(node.value)

    def visit_NamedExpr(self, node):
        with self.require_parens(_Precedence.NAMED_EXPR, node):
            self.set_precedence(_Precedence.ATOM, node.target, node.value)
            self.traverse(node.target)
            self.write(" := ")
            self.traverse(node.value)

    def visit_Import(self, node):
        self.fill("import ")
        self.interleave(lambda: self.write(", "), self.traverse, node.names)

    def visit_ImportFrom(self, node):
        self.fill("from ")
        self.write("." * (node.level or 0))
        if node.module:
            self.write(node.module)
        self.write(" import ")
        self.interleave(lambda: self.write(", "), self.traverse, node.names)

    def visit_Assign(self, node):
        self.fill()
        for target in node.targets:
            self.set_precedence(_Precedence.TUPLE, target)
            self.traverse(target)
            self.write(" = ")
        self.traverse(node.value)
        if type_comment := self.get_type_comment(node):
            self.write(type_comment)

    def visit_AugAssign(self, node):
        self.fill()
        self.traverse(node.target)
        self.write(" " + self.binop[node.op.__class__.__name__] + "= ")
        self.traverse(node.value)

    def visit_AnnAssign(self, node):
        self.fill()
        with self.delimit_if("(", ")", not node.simple and isinstance(node.target, Name)):
            self.traverse(node.target)
        self.write(": ")
        self.traverse(node.annotation)
        if node.value:
            self.write(" = ")
            self.traverse(node.value)

    def visit_Return(self, node):
        self.fill("return")
        if node.value:
            self.write(" ")
            self.traverse(node.value)

    def visit_Pass(self, node):
        self.fill("pass")

    def visit_Break(self, node):
        self.fill("break")

    def visit_Continue(self, node):
        self.fill("continue")

    def visit_Delete(self, node):
        self.fill("del ")
        self.interleave(lambda: self.write(", "), self.traverse, node.targets)

    def visit_Assert(self, node):
        self.fill("assert ")
        self.traverse(node.test)
        if node.msg:
            self.write(", ")
            self.traverse(node.msg)

    def visit_Global(self, node):
        self.fill("global ")
        self.interleave(lambda: self.write(", "), self.write, node.names)

    def visit_Nonlocal(self, node):
        self.fill("nonlocal ")
        self.interleave(lambda: self.write(", "), self.write, node.names)

    def visit_Await(self, node):
        with self.require_parens(_Precedence.AWAIT, node):
            self.write("await")
            if node.value:
                self.write(" ")
                self.set_precedence(_Precedence.ATOM, node.value)
                self.traverse(node.value)

    def visit_Yield(self, node):
        with self.require_parens(_Precedence.YIELD, node):
            self.write("yield")
            if node.value:
                self.write(" ")
                self.set_precedence(_Precedence.ATOM, node.value)
                self.traverse(node.value)

    def visit_YieldFrom(self, node):
        with self.require_parens(_Precedence.YIELD, node):
            self.write("yield from ")
            if not node.value:
                raise ValueError("Node can't be used without a value attribute.")
            self.set_precedence(_Precedence.ATOM, node.value)
            self.traverse(node.value)

    def visit_Raise(self, node):
        self.fill("raise")
        if not node.exc:
            if node.cause:
                raise ValueError(f"Node can't use cause without an exception.")
            return
        self.write(" ")
        self.traverse(node.exc)
        if node.cause:
            self.write(" from ")
            self.traverse(node.cause)

    def do_visit_try(self, node):
        self.fill("try", allow_semicolon=False)
        with self.block():
            self.traverse(node.body)
        for ex in node.handlers:
            self.traverse(ex)
        if node.orelse:
            self.fill("else", allow_semicolon=False)
            with self.block():
                self.traverse(node.orelse)
        if node.finalbody:
            self.fill("finally", allow_semicolon=False)
            with self.block():
                self.traverse(node.finalbody)

    def visit_Try(self, node):
        prev_in_try_star = self._in_try_star
        try:
            self._in_try_star = False
            self.do_visit_try(node)
        finally:
            self._in_try_star = prev_in_try_star

    def visit_TryStar(self, node):
        prev_in_try_star = self._in_try_star
        try:
            self._in_try_star = True
            self.do_visit_try(node)
        finally:
            self._in_try_star = prev_in_try_star

    def visit_ExceptHandler(self, node):
        self.fill("except*" if self._in_try_star else "except", allow_semicolon=False)
        if node.type:
            self.write(" ")
            self.traverse(node.type)
        if node.name:
            self.write(" as ")
            self.write(node.name)
        with self.block():
            self.traverse(node.body)

    def visit_ClassDef(self, node):
        self.maybe_newline()
        for deco in node.decorator_list:
            self.fill("@", allow_semicolon=False)
            self.traverse(deco)
        self.fill("class " + node.name, allow_semicolon=False)
        if hasattr(node, "type_params"):
            self._type_params_helper(node.type_params)
        with self.delimit_if("(", ")", condition = node.bases or node.keywords):
            comma = False
            for e in node.bases:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)
            for e in node.keywords:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)

        with self.block():
            self._write_docstring_and_traverse_body(node)

    def visit_FunctionDef(self, node):
        self._function_helper(node, "def")

    def visit_AsyncFunctionDef(self, node):
        self._function_helper(node, "async def")

    def _function_helper(self, node, fill_suffix):
        self.maybe_newline()
        for deco in node.decorator_list:
            self.fill("@", allow_semicolon=False)
            self.traverse(deco)
        def_str = fill_suffix + " " + node.name
        self.fill(def_str, allow_semicolon=False)
        if hasattr(node, "type_params"):
            self._type_params_helper(node.type_params)
        with self.delimit("(", ")"):
            self.traverse(node.args)
        if node.returns:
            self.write(" -> ")
            self.traverse(node.returns)
        with self.block(extra=self.get_type_comment(node)):
            self._write_docstring_and_traverse_body(node)

    def _type_params_helper(self, type_params):
        if type_params is not None and len(type_params) > 0:
            with self.delimit("[", "]"):
                self.interleave(lambda: self.write(", "), self.traverse, type_params)

    def visit_TypeVar(self, node):
        self.write(node.name)
        if node.bound:
            self.write(": ")
            self.traverse(node.bound)
        if node.default_value:
            self.write(" = ")
            self.traverse(node.default_value)

    def visit_TypeVarTuple(self, node):
        self.write("*" + node.name)
        if node.default_value:
            self.write(" = ")
            self.traverse(node.default_value)

    def visit_ParamSpec(self, node):
        self.write("**" + node.name)
        if node.default_value:
            self.write(" = ")
            self.traverse(node.default_value)

    def visit_TypeAlias(self, node):
        self.fill("type ")
        self.traverse(node.name)
        self._type_params_helper(node.type_params)
        self.write(" = ")
        self.traverse(node.value)

    def visit_For(self, node):
        self._for_helper("for ", node)

    def visit_AsyncFor(self, node):
        self._for_helper("async for ", node)

    def _for_helper(self, fill, node):
        self.fill(fill, allow_semicolon=False)
        self.set_precedence(_Precedence.TUPLE, node.target)
        self.traverse(node.target)
        self.write(" in ")
        self.traverse(node.iter)
        with self.block(extra=self.get_type_comment(node)):
            self.traverse(node.body)
        if node.orelse:
            self.fill("else", allow_semicolon=False)
            with self.block():
