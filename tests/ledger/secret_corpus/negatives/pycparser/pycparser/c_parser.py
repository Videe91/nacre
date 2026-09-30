# ------------------------------------------------------------------------------
# pycparser: c_parser.py
#
# Recursive-descent parser for the C language.
#
# Eli Bendersky [https://eli.thegreenplace.net/]
# License: BSD
# ------------------------------------------------------------------------------
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Literal,
    NoReturn,
    Optional,
    Tuple,
    TypedDict,
    cast,
)

from . import c_ast
from .c_lexer import CLexer, _Token
from .ast_transforms import fix_switch_cases, fix_atomic_specifiers


@dataclass
class Coord:
    """Coordinates of a syntactic element. Consists of:
    - File name
    - Line number
    - Column number
    """

    file: str
    line: int
    column: Optional[int] = None

    def __str__(self) -> str:
        text = f"{self.file}:{self.line}"
        if self.column:
            text += f":{self.column}"
        return text


class ParseError(Exception):
    pass


class CParser:
    """Recursive-descent C parser.

    Usage:
        parser = CParser()
        ast = parser.parse(text, filename)

    The `lexer` parameter lets you inject a lexer class (defaults to CLexer).
    The parameters after `lexer` are accepted for backward compatibility with
    the old PLY-based parser and are otherwise unused.
    """

    def __init__(
        self,
        lex_optimize: bool = True,
        lexer: type[CLexer] = CLexer,
        lextab: str = "pycparser.lextab",
        yacc_optimize: bool = True,
        yacctab: str = "pycparser.yacctab",
        yacc_debug: bool = False,
        taboutputdir: str = "",
    ) -> None:
        self.clex: CLexer = lexer(
            error_func=self._lex_error_func,
            on_lbrace_func=self._lex_on_lbrace_func,
            on_rbrace_func=self._lex_on_rbrace_func,
            type_lookup_func=self._lex_type_lookup_func,
        )

        # Stack of scopes for keeping track of symbols. _scope_stack[-1] is
        # the current (topmost) scope. Each scope is a dictionary that
        # specifies whether a name is a type. If _scope_stack[n][name] is
        # True, 'name' is currently a type in the scope. If it's False,
        # 'name' is used in the scope but not as a type (for instance, if we
        # saw: int name;
        # If 'name' is not a key in _scope_stack[n] then 'name' was not defined
        # in this scope at all.
        self._scope_stack: List[Dict[str, bool]] = [dict()]
        self._tokens: _TokenStream = _TokenStream(self.clex)

    def parse(
        self, text: str, filename: str = "", debug: bool = False
    ) -> c_ast.FileAST:
        """Parses C code and returns an AST.

        text:
            A string containing the C source code

        filename:
            Name of the file being parsed (for meaningful
            error messages)

        debug:
            Deprecated debug flag (unused); for backwards compatibility.
        """
        self._scope_stack = [dict()]
        self.clex.input(text, filename)
        self._tokens = _TokenStream(self.clex)

        ast = self._parse_translation_unit_or_empty()
        tok = self._peek()
        if tok is not None:
            self._parse_error(f"before: {tok.value}", self._tok_coord(tok))
        return ast

    # ------------------------------------------------------------------
    # Scope and declaration helpers
    # ------------------------------------------------------------------
    def _coord(self, lineno: int, column: Optional[int] = None) -> Coord:
        return Coord(file=self.clex.filename, line=lineno, column=column)

    def _parse_error(self, msg: str, coord: Coord | str | None) -> NoReturn:
        raise ParseError(f"{coord}: {msg}")

    def _push_scope(self) -> None:
        self._scope_stack.append(dict())

    def _pop_scope(self) -> None:
        assert len(self._scope_stack) > 1
        self._scope_stack.pop()

    def _add_typedef_name(self, name: str, coord: Optional[Coord]) -> None:
        """Add a new typedef name (ie a TYPEID) to the current scope"""
        if not self._scope_stack[-1].get(name, True):
            self._parse_error(
                f"Typedef {name!r} previously declared as non-typedef in this scope",
                coord,
            )
        self._scope_stack[-1][name] = True

    def _add_identifier(self, name: str, coord: Optional[Coord]) -> None:
        """Add a new object, function, or enum member name (ie an ID) to the
        current scope
        """
        if self._scope_stack[-1].get(name, False):
            self._parse_error(
                f"Non-typedef {name!r} previously declared as typedef in this scope",
                coord,
            )
        self._scope_stack[-1][name] = False

    def _is_type_in_scope(self, name: str) -> bool:
        """Is *name* a typedef-name in the current scope?"""
        for scope in reversed(self._scope_stack):
            # If name is an identifier in this scope it shadows typedefs in
            # higher scopes.
            in_scope = scope.get(name)
            if in_scope is not None:
                return in_scope
        return False

    def _lex_error_func(self, msg: str, line: int, column: int) -> None:
        self._parse_error(msg, self._coord(line, column))

    def _lex_on_lbrace_func(self) -> None:
        self._push_scope()

    def _lex_on_rbrace_func(self) -> None:
        self._pop_scope()

    def _lex_type_lookup_func(self, name: str) -> bool:
        """Looks up types that were previously defined with
        typedef.
        Passed to the lexer for recognizing identifiers that
        are types.
        """
        return self._is_type_in_scope(name)

    # To understand what's going on here, read sections A.8.5 and
    # A.8.6 of K&R2 very carefully.
    #
    # A C type consists of a basic type declaration, with a list
    # of modifiers. For example:
    #
    # int *c[5];
    #
    # The basic declaration here is 'int c', and the pointer and
    # the array are the modifiers.
    #
    # Basic declarations are represented by TypeDecl (from module c_ast) and the
    # modifiers are FuncDecl, PtrDecl and ArrayDecl.
    #
    # The standard states that whenever a new modifier is parsed, it should be
    # added to the end of the list of modifiers. For example:
    #
    # K&R2 A.8.6.2: Array Declarators
    #
    # In a declaration T D where D has the form
    #   D1 [constant-expression-opt]
    # and the type of the identifier in the declaration T D1 is
    # "type-modifier T", the type of the
    # identifier of D is "type-modifier array of T"
    #
    # This is what this method does. The declarator it receives
    # can be a list of declarators ending with TypeDecl. It
    # tacks the modifier to the end of this list, just before
    # the TypeDecl.
    #
    # Additionally, the modifier may be a list itself. This is
    # useful for pointers, that can come as a chain from the rule
    # p_pointer. In this case, the whole modifier list is spliced
    # into the new location.
    def _type_modify_decl(self, decl: Any, modifier: Any) -> c_ast.Node:
        """Tacks a type modifier on a declarator, and returns
        the modified declarator.

        Note: the declarator and modifier may be modified
        """
        modifier_head = modifier
        modifier_tail = modifier

        # The modifier may be a nested list. Reach its tail.
        while modifier_tail.type:
            modifier_tail = modifier_tail.type

        # If the decl is a basic type, just tack the modifier onto it.
        if isinstance(decl, c_ast.TypeDecl):
            modifier_tail.type = decl
            return modifier
        else:
            # Otherwise, the decl is a list of modifiers. Reach
            # its tail and splice the modifier onto the tail,
            # pointing to the underlying basic type.
            decl_tail = decl
            while not isinstance(decl_tail.type, c_ast.TypeDecl):
                decl_tail = decl_tail.type

            modifier_tail.type = decl_tail.type
            decl_tail.type = modifier_head
            return decl

    # Due to the order in which declarators are constructed,
    # they have to be fixed in order to look like a normal AST.
    #
    # When a declaration arrives from syntax construction, it has
    # these problems:
    # * The innermost TypeDecl has no type (because the basic
    #   type is only known at the uppermost declaration level)
    # * The declaration has no variable name, since that is saved
    #   in the innermost TypeDecl
    # * The typename of the declaration is a list of type
    #   specifiers, and not a node. Here, basic identifier types
    #   should be separated from more complex types like enums
    #   and structs.
    #
    # This method fixes these problems.
    def _fix_decl_name_type(
        self,
        decl: c_ast.Decl | c_ast.Typedef | c_ast.Typename,
        typename: List[Any],
    ) -> c_ast.Decl | c_ast.Typedef | c_ast.Typename:
        """Fixes a declaration. Modifies decl."""
        # Reach the underlying basic type
        typ = decl
        while not isinstance(typ, c_ast.TypeDecl):
            typ = typ.type

        decl.name = typ.declname
        typ.quals = decl.quals[:]

        # The typename is a list of types. If any type in this
        # list isn't an IdentifierType, it must be the only
        # type in the list (it's illegal to declare "int enum ..")
        # If all the types are basic, they're collected in the
        # IdentifierType holder.
        for tn in typename:
            if not isinstance(tn, c_ast.IdentifierType):
                if len(typename) > 1:
                    self._parse_error("Invalid multiple types specified", tn.coord)
                else:
                    typ.type = tn
                    return decl

        if not typename:
            # Functions default to returning int
            if not isinstance(decl.type, c_ast.FuncDecl):
                self._parse_error("Missing type in declaration", decl.coord)
            typ.type = c_ast.IdentifierType(["int"], coord=decl.coord)
        else:
            # At this point, we know that typename is a list of IdentifierType
            # nodes. Concatenate all the names into a single list.
            typ.type = c_ast.IdentifierType(
                [name for id in typename for name in id.names], coord=typename[0].coord
            )
        return decl

    def _add_declaration_specifier(
        self,
        declspec: Optional["_DeclSpec"],
        newspec: Any,
        kind: "_DeclSpecKind",
        append: bool = False,
    ) -> "_DeclSpec":
        """See _DeclSpec for the specifier dictionary layout."""
        if declspec is None:
            spec: _DeclSpec = dict(
                qual=[], storage=[], type=[], function=[], alignment=[]
            )
        else:
            spec = declspec

        if append:
            spec[kind].append(newspec)
        else:
            spec[kind].insert(0, newspec)

        return spec

    def _build_declarations(
        self,
        spec: "_DeclSpec",
        decls: List["_DeclInfo"],
        typedef_namespace: bool = False,
    ) -> List[c_ast.Node]:
        """Builds a list of declarations all sharing the given specifiers.
        If typedef_namespace is true, each declared name is added
        to the "typedef namespace", which also includes objects,
        functions, and enum constants.
        """
        is_typedef = "typedef" in spec["storage"]
        declarations = []

        # Bit-fields are allowed to be unnamed.
        if decls[0].get("bitsize") is None:
            # When redeclaring typedef names as identifiers in inner scopes, a
            # problem can occur where the identifier gets grouped into
            # spec['type'], leaving decl as None.  This can only occur for the
            # first declarator.
            if decls[0]["decl"] is None:
                if (
                    len(spec["type"]) < 2
                    or len(spec["type"][-1].names) != 1
                    or not self._is_type_in_scope(spec["type"][-1].names[0])
                ):
                    coord = "?"
                    for t in spec["type"]:
                        if hasattr(t, "coord"):
                            coord = t.coord
                            break
                    self._parse_error("Invalid declaration", coord)

                # Make this look as if it came from "direct_declarator:ID"
                decls[0]["decl"] = c_ast.TypeDecl(
                    declname=spec["type"][-1].names[0],
                    type=None,
                    quals=None,
                    align=spec["alignment"],
                    coord=spec["type"][-1].coord,
                )
                # Remove the "new" type's name from the end of spec['type']
                del spec["type"][-1]
            # A similar problem can occur where the declaration ends up
            # looking like an abstract declarator.  Give it a name if this is
            # the case.
            elif not isinstance(
                decls[0]["decl"],
                (c_ast.Enum, c_ast.Struct, c_ast.Union, c_ast.IdentifierType),
            ):
                decls_0_tail = cast(Any, decls[0]["decl"])
                while not isinstance(decls_0_tail, c_ast.TypeDecl):
                    decls_0_tail = decls_0_tail.type
                if decls_0_tail.declname is None:
                    decls_0_tail.declname = spec["type"][-1].names[0]
                    del spec["type"][-1]

        for decl in decls:
            assert decl["decl"] is not None
            if is_typedef:
                declaration = c_ast.Typedef(
                    name=None,
                    quals=spec["qual"],
                    storage=spec["storage"],
                    type=decl["decl"],
                    coord=decl["decl"].coord,
                )
            else:
                declaration = c_ast.Decl(
                    name=None,
                    quals=spec["qual"],
                    align=spec["alignment"],
                    storage=spec["storage"],
                    funcspec=spec["function"],
                    type=decl["decl"],
                    init=decl.get("init"),
                    bitsize=decl.get("bitsize"),
                    coord=decl["decl"].coord,
                )

            if isinstance(
                declaration.type,
                (c_ast.Enum, c_ast.Struct, c_ast.Union, c_ast.IdentifierType),
            ):
                fixed_decl = declaration
            else:
                fixed_decl = self._fix_decl_name_type(declaration, spec["type"])

            # Add the type name defined by typedef to a
            # symbol table (for usage in the lexer)
            if typedef_namespace:
                if is_typedef:
                    self._add_typedef_name(fixed_decl.name, fixed_decl.coord)
                else:
                    self._add_identifier(fixed_decl.name, fixed_decl.coord)

            fixed_decl = fix_atomic_specifiers(
                cast(c_ast.Decl | c_ast.Typedef, fixed_decl)
            )
            declarations.append(fixed_decl)

        return declarations

    def _build_function_definition(
        self,
        spec: "_DeclSpec",
        decl: c_ast.Node,
        param_decls: Optional[List[c_ast.Node]],
        body: c_ast.Node,
    ) -> c_ast.Node:
        """Builds a function definition."""
        if "typedef" in spec["storage"]:
            self._parse_error("Invalid typedef", decl.coord)

        declaration = self._build_declarations(
            spec=spec,
            decls=[dict(decl=decl, init=None, bitsize=None)],
            typedef_namespace=True,
        )[0]

        return c_ast.FuncDef(
            decl=declaration, param_decls=param_decls, body=body, coord=decl.coord
        )

    def _select_struct_union_class(self, token: str) -> type:
        """Given a token (either STRUCT or UNION), selects the
        appropriate AST class.
        """
        if token == "struct":
            return c_ast.Struct
        else:
            return c_ast.Union

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------
