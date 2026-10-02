"""This module includes classes and functions designed specifically for use with the mypy plugin."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from configparser import ConfigParser
from typing import Any, Callable

# To avoid import error (https://github.com/python/mypy/issues/17726):
import mypy.types  # noqa: F401
from mypy.errorcodes import ErrorCode
from mypy.expandtype import expand_type, expand_type_by_instance
from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    ARG_OPT,
    ARG_POS,
    ARG_STAR2,
    INVARIANT,
    MDEF,
    Argument,
    AssignmentStmt,
    Block,
    CallExpr,
    ClassDef,
    Context,
    Decorator,
    DictExpr,
    EllipsisExpr,
    Expression,
    FuncDef,
    IfStmt,
    JsonDict,
    MemberExpr,
    NameExpr,
    PassStmt,
    PlaceholderNode,
    RefExpr,
    Statement,
    StrExpr,
    SymbolTableNode,
    TempNode,
    TypeAlias,
    TypeInfo,
    Var,
)
from mypy.options import Options
from mypy.plugin import (
    CheckerPluginInterface,
    ClassDefContext,
    DynamicClassDefContext,
    MethodContext,
    Plugin,
    ReportConfigContext,
    SemanticAnalyzerPluginInterface,
)
from mypy.plugins.common import (
    deserialize_and_fixup_type,
)
from mypy.semanal import set_callable_name
from mypy.server.trigger import make_wildcard_trigger
from mypy.state import state
from mypy.type_visitor import TypeTranslator
from mypy.typeops import map_type_from_supertype
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    NoneType,
    Type,
    TypeOfAny,
    TypeType,
    TypeVarType,
    UnionType,
    get_proper_type,
)
from mypy.typevars import fill_typevars
from mypy.util import get_unique_redefinition_name
from mypy.version import __version__ as mypy_version

from pydantic._internal import _fields
from pydantic.version import parse_mypy_version

CONFIGFILE_KEY = 'pydantic-mypy'
METADATA_KEY = 'pydantic-mypy-metadata'
BASEMODEL_FULLNAME = 'pydantic.main.BaseModel'
CREATE_MODEL_FULLNAME = 'pydantic.main.create_model'
BASESETTINGS_FULLNAME = 'pydantic_settings.main.BaseSettings'
ROOT_MODEL_FULLNAME = 'pydantic.root_model.RootModel'
MODEL_METACLASS_FULLNAME = 'pydantic._internal._model_construction.ModelMetaclass'
FIELD_FULLNAME = 'pydantic.fields.Field'
DATACLASS_FULLNAME = 'pydantic.dataclasses.dataclass'
MODEL_VALIDATOR_FULLNAME = 'pydantic.functional_validators.model_validator'
DECORATOR_FULLNAMES = {
    'pydantic.functional_validators.field_validator',
    'pydantic.functional_validators.model_validator',
    'pydantic.functional_serializers.serializer',
    'pydantic.functional_serializers.model_serializer',
    'pydantic.deprecated.class_validators.validator',
    'pydantic.deprecated.class_validators.root_validator',
}
IMPLICIT_CLASSMETHOD_DECORATOR_FULLNAMES = DECORATOR_FULLNAMES - {'pydantic.functional_serializers.model_serializer'}


MYPY_VERSION_TUPLE = parse_mypy_version(mypy_version)
BUILTINS_NAME = 'builtins'

# Increment version if plugin changes and mypy caches should be invalidated
__version__ = 2


def plugin(version: str) -> type[Plugin]:
    """`version` is the mypy version string.

    We might want to use this to print a warning if the mypy version being used is
    newer, or especially older, than we expect (or need).

    Args:
        version: The mypy version string.

    Return:
        The Pydantic mypy plugin type.
    """
    return PydanticPlugin


class PydanticPlugin(Plugin):
    """The Pydantic mypy plugin."""

    def __init__(self, options: Options) -> None:
        self.plugin_config = PydanticPluginConfig(options)
        self._plugin_data = self.plugin_config.to_data()
        super().__init__(options)

    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        """Update Pydantic model class."""
        sym = self.lookup_fully_qualified(fullname)
        if sym and isinstance(sym.node, TypeInfo):  # pragma: no branch
            # No branching may occur if the mypy cache has not been cleared
            if sym.node.has_base(BASEMODEL_FULLNAME):
                return self._pydantic_model_class_maker_callback
        return None

    def get_metaclass_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        """Update Pydantic `ModelMetaclass` definition."""
        if fullname == MODEL_METACLASS_FULLNAME:
            return self._pydantic_model_metaclass_marker_callback
        return None

    def get_method_hook(self, fullname: str) -> Callable[[MethodContext], Type] | None:
        """Adjust return type of `from_orm` method call."""
        if fullname.endswith('.from_orm'):
            return from_attributes_callback
        return None

    def get_dynamic_class_hook(self, fullname: str) -> Callable[[DynamicClassDefContext], None] | None:
        """Recognize `create_model()` calls as dynamic BaseModel subclasses."""
        if fullname == CREATE_MODEL_FULLNAME:
            return self._pydantic_create_model_callback
        return None

    def report_config_data(self, ctx: ReportConfigContext) -> dict[str, Any]:
        """Return all plugin config data.

        Used by mypy to determine if cache needs to be discarded.
        """
        return self._plugin_data

    def _pydantic_model_class_maker_callback(self, ctx: ClassDefContext) -> None:
        transformer = PydanticModelTransformer(ctx.cls, ctx.reason, ctx.api, self.plugin_config)
        transformer.transform()

    def _pydantic_model_metaclass_marker_callback(self, ctx: ClassDefContext) -> None:
        """Reset dataclass_transform_spec attribute of ModelMetaclass.

        Let the plugin handle it. This behavior can be disabled
        if 'debug_dataclass_transform' is set to True', for testing purposes.
        """
        if self.plugin_config.debug_dataclass_transform:
            return
        info_metaclass = ctx.cls.info.declared_metaclass
        assert info_metaclass, "callback not passed from 'get_metaclass_hook'"
        if getattr(info_metaclass.type, 'dataclass_transform_spec', None):
            info_metaclass.type.dataclass_transform_spec = None

    def _pydantic_create_model_callback(self, ctx: DynamicClassDefContext) -> None:
        """Make variables assigned from `create_model()` usable as types by mypy."""
        # Determine the base class from __base__ argument if provided
        base_fullname = BASEMODEL_FULLNAME
        for arg_name, arg_expr in zip(ctx.call.arg_names, ctx.call.args):
            if arg_name == '__base__' and isinstance(arg_expr, RefExpr) and arg_expr.node is not None:
                if isinstance(arg_expr.node, TypeInfo):
                    base_fullname = arg_expr.node.fullname
                elif isinstance(arg_expr.node, Var) and isinstance(arg_expr.node.type, Instance):
                    base_fullname = arg_expr.node.type.type.fullname

        base_sym = ctx.api.lookup_fully_qualified_or_none(base_fullname)
        if base_sym is None or not isinstance(base_sym.node, TypeInfo):
            # Fall back to BaseModel
            base_sym = ctx.api.lookup_fully_qualified_or_none(BASEMODEL_FULLNAME)
            if base_sym is None or not isinstance(base_sym.node, TypeInfo):
                return

        base_info = base_sym.node
        base_instance = fill_typevars(base_info)
        assert isinstance(base_instance, Instance)

        info = ctx.api.basic_new_typeinfo(ctx.name, base_instance, ctx.call.line)
        info.metaclass_type = base_info.metaclass_type

        ctx.api.add_symbol_table_node(ctx.name, SymbolTableNode(MDEF, info))


class PydanticPluginConfig:
    """A Pydantic mypy plugin config holder.

    Attributes:
        init_forbid_extra: Whether to add a `**kwargs` at the end of the generated `__init__` signature.
        init_typed: Whether to annotate fields in the generated `__init__`.
        warn_required_dynamic_aliases: Whether to raise required dynamic aliases error.
        debug_dataclass_transform: Whether to not reset `dataclass_transform_spec` attribute
            of `ModelMetaclass` for testing purposes.
    """

    __slots__ = (
        'init_forbid_extra',
        'init_typed',
        'warn_required_dynamic_aliases',
        'debug_dataclass_transform',
    )
    init_forbid_extra: bool
    init_typed: bool
    warn_required_dynamic_aliases: bool
    debug_dataclass_transform: bool  # undocumented

    def __init__(self, options: Options) -> None:
        if options.config_file is None:  # pragma: no cover
            return

        toml_config = parse_toml(options.config_file)
        if toml_config is not None:
            config = toml_config.get('tool', {}).get('pydantic-mypy', {})
            for key in self.__slots__:
                setting = config.get(key, False)
                if not isinstance(setting, bool):
                    raise ValueError(f'Configuration value must be a boolean for key: {key}')
                setattr(self, key, setting)
        else:
            plugin_config = ConfigParser()
            plugin_config.read(options.config_file)
            for key in self.__slots__:
                setting = plugin_config.getboolean(CONFIGFILE_KEY, key, fallback=False)
                setattr(self, key, setting)

    def to_data(self) -> dict[str, Any]:
        """Returns a dict of config names to their values."""
        return {key: getattr(self, key) for key in self.__slots__}


def from_attributes_callback(ctx: MethodContext) -> Type:
    """Raise an error if from_attributes is not enabled."""
    model_type: Instance
    ctx_type = ctx.type
    if isinstance(ctx_type, TypeType):
        ctx_type = ctx_type.item
    if isinstance(ctx_type, CallableType) and isinstance(ctx_type.ret_type, Instance):
        model_type = ctx_type.ret_type  # called on the class
    elif isinstance(ctx_type, Instance):
        model_type = ctx_type  # called on an instance (unusual, but still valid)
    else:  # pragma: no cover
        detail = f'ctx.type: {ctx_type} (of type {ctx_type.__class__.__name__})'
        error_unexpected_behavior(detail, ctx.api, ctx.context)
        return ctx.default_return_type
    pydantic_metadata = model_type.type.metadata.get(METADATA_KEY)
    if pydantic_metadata is None:
        return ctx.default_return_type
    if not model_type.type.has_base(BASEMODEL_FULLNAME):
        # not a Pydantic v2 model
        return ctx.default_return_type
    from_attributes = pydantic_metadata.get('config', {}).get('from_attributes')
    if from_attributes is not True:
        error_from_attributes(model_type.type.name, ctx.api, ctx.context)
    return ctx.default_return_type


class PydanticModelField:
    """Based on mypy.plugins.dataclasses.DataclassAttribute."""

    def __init__(
        self,
        name: str,
        alias: str | None,
        is_frozen: bool,
        has_dynamic_alias: bool,
        has_default: bool,
        strict: bool | None,
        line: int,
        column: int,
        type: Type | None,
        info: TypeInfo,
    ):
        self.name = name
        self.alias = alias
        self.is_frozen = is_frozen
        self.has_dynamic_alias = has_dynamic_alias
        self.has_default = has_default
        self.strict = strict
        self.line = line
        self.column = column
        self.type = type
        self.info = info

    def to_argument(
        self,
        current_info: TypeInfo,
        typed: bool,
        model_strict: bool,
        force_optional: bool,
        use_alias: bool,
        api: SemanticAnalyzerPluginInterface,
        force_typevars_invariant: bool,
        is_root_model_root: bool,
    ) -> Argument:
        """Based on mypy.plugins.dataclasses.DataclassAttribute.to_argument."""
        variable = self.to_var(current_info, api, use_alias, force_typevars_invariant)

        strict = model_strict if self.strict is None else self.strict
        if typed or strict:
            type_annotation = self.expand_type(current_info, api, include_root_type=True)
        else:
            type_annotation = AnyType(TypeOfAny.explicit)

        return Argument(
            variable=variable,
            type_annotation=type_annotation,
            initializer=None,
            kind=ARG_OPT
            if is_root_model_root
            else (ARG_NAMED_OPT if force_optional or self.has_default else ARG_NAMED),
        )

    def expand_type(
        self,
        current_info: TypeInfo,
        api: SemanticAnalyzerPluginInterface,
        force_typevars_invariant: bool = False,
        include_root_type: bool = False,
    ) -> Type | None:
        """Based on mypy.plugins.dataclasses.DataclassAttribute.expand_type."""
        if force_typevars_invariant:
            # In some cases, mypy will emit an error "Cannot use a covariant type variable as a parameter"
            # To prevent that, we add an option to replace typevars with invariant ones while building certain
            # method signatures (in particular, `__init__`). There may be a better way to do this, if this causes
            # us problems in the future, we should look into why the dataclasses plugin doesn't have this issue.
            if isinstance(self.type, TypeVarType):
                modified_type = self.type.copy_modified()
                modified_type.variance = INVARIANT
                self.type = modified_type

        if self.type is not None and self.info.self_type is not None:
            # In general, it is not safe to call `expand_type()` during semantic analysis,
            # however this plugin is called very late, so all types should be fully ready.
            # Also, it is tricky to avoid eager expansion of Self types here (e.g. because
            # we serialize attributes).
            with state.strict_optional_set(api.options.strict_optional):
                filled_with_typevars = fill_typevars(current_info)
                # Cannot be TupleType as current_info represents a Pydantic model:
                assert isinstance(filled_with_typevars, Instance)
                if force_typevars_invariant:
                    for arg in filled_with_typevars.args:
                        if isinstance(arg, TypeVarType):
                            arg.variance = INVARIANT

                expanded_type = expand_type(self.type, {self.info.self_type.id: filled_with_typevars})
                if include_root_type and isinstance(expanded_type, Instance) and is_root_model(expanded_type.type):
                    # When a root model is used as a field, Pydantic allows both an instance of the root model
                    # as well as instances of the `root` field type:
                    root_type = expanded_type.type['root'].type
                    if root_type is None:
                        # Happens if the hint for 'root' has unsolved forward references
                        return expanded_type
                    expanded_root_type = expand_type_by_instance(root_type, expanded_type)
                    expanded_type = UnionType([expanded_type, expanded_root_type])
                return expanded_type
        return self.type

    def to_var(
        self,
        current_info: TypeInfo,
        api: SemanticAnalyzerPluginInterface,
        use_alias: bool,
        force_typevars_invariant: bool = False,
    ) -> Var:
        """Based on mypy.plugins.dataclasses.DataclassAttribute.to_var."""
        if use_alias and self.alias is not None:
            name = self.alias
        else:
            name = self.name

        return Var(name, self.expand_type(current_info, api, force_typevars_invariant))

    def serialize(self) -> JsonDict:
        """Based on mypy.plugins.dataclasses.DataclassAttribute.serialize."""
        assert self.type
        return {
            'name': self.name,
            'alias': self.alias,
            'is_frozen': self.is_frozen,
            'has_dynamic_alias': self.has_dynamic_alias,
            'has_default': self.has_default,
            'strict': self.strict,
            'line': self.line,
            'column': self.column,
            'type': self.type.serialize(),
        }

    @classmethod
    def deserialize(cls, info: TypeInfo, data: JsonDict, api: SemanticAnalyzerPluginInterface) -> PydanticModelField:
        """Based on mypy.plugins.dataclasses.DataclassAttribute.deserialize."""
        data = data.copy()
