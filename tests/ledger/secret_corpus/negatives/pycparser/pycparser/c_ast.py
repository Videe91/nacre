# -----------------------------------------------------------------
# ** ATTENTION **
# This code was automatically generated from _c_ast.cfg
#
# Do not modify it directly. Modify the configuration file and
# run the generator again.
# ** ** *** ** **
#
# pycparser: c_ast.py
#
# AST Node classes.
#
# Eli Bendersky [https://eli.thegreenplace.net/]
# License: BSD
# -----------------------------------------------------------------


import sys
from typing import Any, ClassVar, IO, Optional


def _repr(obj):
    """
    Get the representation of an object, with dedicated pprint-like format for lists.
    """
    if isinstance(obj, list):
        return "[" + (",\n ".join((_repr(e).replace("\n", "\n ") for e in obj))) + "\n]"
    else:
        return repr(obj)


class Node:
    __slots__ = ()
    """ Abstract base class for AST nodes.
    """
    attr_names: ClassVar[tuple[str, ...]] = ()
    coord: Optional[Any]

    def __repr__(self):
        """Generates a python representation of the current node"""
        result = self.__class__.__name__ + "("

        indent = ""
        separator = ""
        for name in self.__slots__[:-2]:
            result += separator
            result += indent
            result += (
                name
                + "="
                + (
                    _repr(getattr(self, name)).replace(
                        "\n",
                        "\n  " + (" " * (len(name) + len(self.__class__.__name__))),
                    )
                )
            )

            separator = ","
            indent = "\n " + (" " * len(self.__class__.__name__))

        result += indent + ")"

        return result

    def children(self):
        """A sequence of all children that are Nodes"""
        pass

    def show(
        self,
        buf: IO[str] = sys.stdout,
        offset: int = 0,
        attrnames: bool = False,
        showemptyattrs: bool = True,
        nodenames: bool = False,
        showcoord: bool = False,
        _my_node_name: Optional[str] = None,
    ):
        """Pretty print the Node and all its attributes and
        children (recursively) to a buffer.

        buf:
            Open IO buffer into which the Node is printed.

        offset:
            Initial offset (amount of leading spaces)

        attrnames:
            True if you want to see the attribute names in
            name=value pairs. False to only see the values.

        showemptyattrs:
            False if you want to suppress printing empty attributes.

        nodenames:
            True if you want to see the actual node names
            within their parents.

        showcoord:
            Do you want the coordinates of each Node to be
            displayed.
        """
        lead = " " * offset
        if nodenames and _my_node_name is not None:
            buf.write(lead + self.__class__.__name__ + " <" + _my_node_name + ">: ")
        else:
            buf.write(lead + self.__class__.__name__ + ": ")

        if self.attr_names:

            def is_empty(v):
                v is None or (hasattr(v, "__len__") and len(v) == 0)

            nvlist = [
                (n, getattr(self, n))
                for n in self.attr_names
                if showemptyattrs or not is_empty(getattr(self, n))
            ]
            if attrnames:
                attrstr = ", ".join(f"{name}={value}" for name, value in nvlist)
            else:
                attrstr = ", ".join(f"{value}" for _, value in nvlist)
            buf.write(attrstr)

        if showcoord:
            buf.write(f" (at {self.coord})")
        buf.write("\n")

        for child_name, child in self.children():
            child.show(
                buf,
                offset=offset + 2,
                attrnames=attrnames,
                showemptyattrs=showemptyattrs,
                nodenames=nodenames,
                showcoord=showcoord,
                _my_node_name=child_name,
            )


class NodeVisitor:
    """A base NodeVisitor class for visiting c_ast nodes.
    Subclass it and define your own visit_XXX methods, where
    XXX is the class name you want to visit with these
    methods.

    For example:

    class ConstantVisitor(NodeVisitor):
        def __init__(self):
            self.values = []

        def visit_Constant(self, node):
            self.values.append(node.value)

    Creates a list of values of all the constant nodes
    encountered below the given node. To use it:

    cv = ConstantVisitor()
    cv.visit(node)

    Notes:

    *   generic_visit() will be called for AST nodes for which
        no visit_XXX method was defined.
    *   The children of nodes for which a visit_XXX was
        defined will not be visited - if you need this, call
        generic_visit() on the node.
        You can use:
            NodeVisitor.generic_visit(self, node)
    *   Modeled after Python's own AST visiting facilities
        (the ast module of Python 3.0)
    """

    _method_cache = None

    def visit(self, node: Node):
        """Visit a node."""

        if self._method_cache is None:
            self._method_cache = {}

        visitor = self._method_cache.get(node.__class__.__name__, None)
        if visitor is None:
            method = "visit_" + node.__class__.__name__
            visitor = getattr(self, method, self.generic_visit)
            self._method_cache[node.__class__.__name__] = visitor

        return visitor(node)

    def generic_visit(self, node: Node):
        """Called if no explicit visitor function exists for a
        node. Implements preorder visiting of the node.
        """
        for _, c in node.children():
            self.visit(c)


class ArrayDecl(Node):
    __slots__ = ("type", "dim", "dim_quals", "coord", "__weakref__")

    def __init__(self, type, dim, dim_quals, coord=None):
        self.type = type
        self.dim = dim
        self.dim_quals = dim_quals
        self.coord = coord

    def children(self):
        nodelist = []
        if self.type is not None:
            nodelist.append(("type", self.type))
        if self.dim is not None:
            nodelist.append(("dim", self.dim))
        return tuple(nodelist)

    def __iter__(self):
        if self.type is not None:
            yield self.type
        if self.dim is not None:
            yield self.dim

    attr_names = ("dim_quals",)


class ArrayRef(Node):
    __slots__ = ("name", "subscript", "coord", "__weakref__")

    def __init__(self, name, subscript, coord=None):
        self.name = name
        self.subscript = subscript
        self.coord = coord

    def children(self):
        nodelist = []
        if self.name is not None:
            nodelist.append(("name", self.name))
        if self.subscript is not None:
            nodelist.append(("subscript", self.subscript))
        return tuple(nodelist)

    def __iter__(self):
        if self.name is not None:
            yield self.name
        if self.subscript is not None:
            yield self.subscript

    attr_names = ()


class Assignment(Node):
    __slots__ = ("op", "lvalue", "rvalue", "coord", "__weakref__")

    def __init__(self, op, lvalue, rvalue, coord=None):
        self.op = op
        self.lvalue = lvalue
        self.rvalue = rvalue
        self.coord = coord

    def children(self):
        nodelist = []
        if self.lvalue is not None:
            nodelist.append(("lvalue", self.lvalue))
        if self.rvalue is not None:
            nodelist.append(("rvalue", self.rvalue))
        return tuple(nodelist)

    def __iter__(self):
        if self.lvalue is not None:
            yield self.lvalue
        if self.rvalue is not None:
            yield self.rvalue

    attr_names = ("op",)


class Alignas(Node):
    __slots__ = ("alignment", "coord", "__weakref__")

    def __init__(self, alignment, coord=None):
        self.alignment = alignment
        self.coord = coord

    def children(self):
        nodelist = []
        if self.alignment is not None:
            nodelist.append(("alignment", self.alignment))
        return tuple(nodelist)

    def __iter__(self):
        if self.alignment is not None:
            yield self.alignment

    attr_names = ()


class BinaryOp(Node):
    __slots__ = ("op", "left", "right", "coord", "__weakref__")

    def __init__(self, op, left, right, coord=None):
        self.op = op
        self.left = left
        self.right = right
        self.coord = coord

    def children(self):
        nodelist = []
        if self.left is not None:
            nodelist.append(("left", self.left))
        if self.right is not None:
            nodelist.append(("right", self.right))
        return tuple(nodelist)

    def __iter__(self):
        if self.left is not None:
            yield self.left
        if self.right is not None:
            yield self.right

    attr_names = ("op",)


class Break(Node):
    __slots__ = ("coord", "__weakref__")

    def __init__(self, coord=None):
        self.coord = coord

    def children(self):
        return ()

    def __iter__(self):
        return
        yield

    attr_names = ()


class Case(Node):
    __slots__ = ("expr", "stmts", "coord", "__weakref__")

    def __init__(self, expr, stmts, coord=None):
        self.expr = expr
        self.stmts = stmts
        self.coord = coord

    def children(self):
        nodelist = []
        if self.expr is not None:
            nodelist.append(("expr", self.expr))
        for i, child in enumerate(self.stmts or []):
            nodelist.append((f"stmts[{i}]", child))
        return tuple(nodelist)

    def __iter__(self):
        if self.expr is not None:
            yield self.expr
        for child in self.stmts or []:
            yield child

    attr_names = ()


class Cast(Node):
    __slots__ = ("to_type", "expr", "coord", "__weakref__")

    def __init__(self, to_type, expr, coord=None):
        self.to_type = to_type
        self.expr = expr
        self.coord = coord

    def children(self):
        nodelist = []
        if self.to_type is not None:
            nodelist.append(("to_type", self.to_type))
        if self.expr is not None:
            nodelist.append(("expr", self.expr))
        return tuple(nodelist)

    def __iter__(self):
        if self.to_type is not None:
            yield self.to_type
        if self.expr is not None:
            yield self.expr

    attr_names = ()


class Compound(Node):
    __slots__ = ("block_items", "coord", "__weakref__")

    def __init__(self, block_items, coord=None):
        self.block_items = block_items
        self.coord = coord

    def children(self):
        nodelist = []
        for i, child in enumerate(self.block_items or []):
            nodelist.append((f"block_items[{i}]", child))
        return tuple(nodelist)

    def __iter__(self):
        for child in self.block_items or []:
            yield child

    attr_names = ()


class CompoundLiteral(Node):
    __slots__ = ("type", "init", "coord", "__weakref__")

    def __init__(self, type, init, coord=None):
        self.type = type
        self.init = init
        self.coord = coord

    def children(self):
        nodelist = []
        if self.type is not None:
            nodelist.append(("type", self.type))
        if self.init is not None:
            nodelist.append(("init", self.init))
        return tuple(nodelist)

    def __iter__(self):
        if self.type is not None:
            yield self.type
        if self.init is not None:
            yield self.init

    attr_names = ()


class Constant(Node):
    __slots__ = ("type", "value", "coord", "__weakref__")

    def __init__(self, type, value, coord=None):
        self.type = type
        self.value = value
        self.coord = coord

    def children(self):
        nodelist = []
        return tuple(nodelist)

    def __iter__(self):
        return
        yield

    attr_names = (
        "type",
        "value",
    )


class Continue(Node):
    __slots__ = ("coord", "__weakref__")

    def __init__(self, coord=None):
        self.coord = coord

    def children(self):
        return ()

    def __iter__(self):
        return
        yield

    attr_names = ()


class Decl(Node):
    __slots__ = (
        "name",
        "quals",
        "align",
        "storage",
        "funcspec",
        "type",
        "init",
        "bitsize",
        "coord",
        "__weakref__",
    )

    def __init__(
        self, name, quals, align, storage, funcspec, type, init, bitsize, coord=None
    ):
        self.name = name
        self.quals = quals
        self.align = align
        self.storage = storage
        self.funcspec = funcspec
        self.type = type
        self.init = init
        self.bitsize = bitsize
        self.coord = coord

    def children(self):
        nodelist = []
        if self.type is not None:
            nodelist.append(("type", self.type))
        if self.init is not None:
            nodelist.append(("init", self.init))
        if self.bitsize is not None:
            nodelist.append(("bitsize", self.bitsize))
        return tuple(nodelist)

    def __iter__(self):
        if self.type is not None:
            yield self.type
        if self.init is not None:
            yield self.init
        if self.bitsize is not None:
            yield self.bitsize

    attr_names = (
        "name",
        "quals",
        "align",
        "storage",
        "funcspec",
    )


class DeclList(Node):
    __slots__ = ("decls", "coord", "__weakref__")

    def __init__(self, decls, coord=None):
        self.decls = decls
        self.coord = coord

    def children(self):
        nodelist = []
        for i, child in enumerate(self.decls or []):
            nodelist.append((f"decls[{i}]", child))
        return tuple(nodelist)

    def __iter__(self):
        for child in self.decls or []:
            yield child

    attr_names = ()


class Default(Node):
    __slots__ = ("stmts", "coord", "__weakref__")

    def __init__(self, stmts, coord=None):
        self.stmts = stmts
        self.coord = coord

    def children(self):
        nodelist = []
        for i, child in enumerate(self.stmts or []):
            nodelist.append((f"stmts[{i}]", child))
        return tuple(nodelist)

    def __iter__(self):
        for child in self.stmts or []:
            yield child

    attr_names = ()


class DoWhile(Node):
    __slots__ = ("cond", "stmt", "coord", "__weakref__")

    def __init__(self, cond, stmt, coord=None):
        self.cond = cond
        self.stmt = stmt
        self.coord = coord

    def children(self):
        nodelist = []
        if self.cond is not None:
            nodelist.append(("cond", self.cond))
        if self.stmt is not None:
            nodelist.append(("stmt", self.stmt))
        return tuple(nodelist)

    def __iter__(self):
        if self.cond is not None:
            yield self.cond
        if self.stmt is not None:
            yield self.stmt

    attr_names = ()


class EllipsisParam(Node):
    __slots__ = ("coord", "__weakref__")

    def __init__(self, coord=None):
        self.coord = coord

    def children(self):
        return ()

    def __iter__(self):
        return
        yield

    attr_names = ()


class EmptyStatement(Node):
    __slots__ = ("coord", "__weakref__")

    def __init__(self, coord=None):
        self.coord = coord

    def children(self):
        return ()

    def __iter__(self):
        return
        yield

    attr_names = ()


class Enum(Node):
    __slots__ = ("name", "values", "coord", "__weakref__")

    def __init__(self, name, values, coord=None):
        self.name = name
        self.values = values
        self.coord = coord

    def children(self):
        nodelist = []
        if self.values is not None:
            nodelist.append(("values", self.values))
        return tuple(nodelist)

    def __iter__(self):
        if self.values is not None:
            yield self.values

    attr_names = ("name",)


class Enumerator(Node):
    __slots__ = ("name", "value", "coord", "__weakref__")

    def __init__(self, name, value, coord=None):
        self.name = name
        self.value = value
        self.coord = coord

    def children(self):
        nodelist = []
        if self.value is not None:
            nodelist.append(("value", self.value))
        return tuple(nodelist)

