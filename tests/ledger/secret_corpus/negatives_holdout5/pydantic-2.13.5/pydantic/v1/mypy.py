import sys
from configparser import ConfigParser
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type as TypingType, Union

from mypy.errorcodes import ErrorCode
from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    ARG_OPT,
    ARG_POS,
    ARG_STAR2,
    MDEF,
    Argument,
    AssignmentStmt,
    Block,
    CallExpr,
    ClassDef,
    Context,
    Decorator,
    EllipsisExpr,
    FuncBase,
    FuncDef,
    JsonDict,
    MemberExpr,
    NameExpr,
    PassStmt,
    PlaceholderNode,
    RefExpr,
    StrExpr,
    SymbolNode,
    SymbolTableNode,
    TempNode,
    TypeInfo,
    TypeVarExpr,
    Var,
)
from mypy.options import Options
from mypy.plugin import (
    CheckerPluginInterface,
    ClassDefContext,
    FunctionContext,
    MethodContext,
    Plugin,
    ReportConfigContext,
    SemanticAnalyzerPluginInterface,
)
from mypy.plugins import dataclasses
from mypy.semanal import set_callable_name  # type: ignore
from mypy.server.trigger import make_wildcard_trigger
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    NoneType,
    Overloaded,
    ProperType,
    Type,
    TypeOfAny,
    TypeType,
    TypeVarId,
    TypeVarType,
    UnionType,
    get_proper_type,
)
from mypy.typevars import fill_typevars
from mypy.util import get_unique_redefinition_name
from mypy.version import __version__ as mypy_version

from pydantic.v1.utils import is_valid_field

try:
    from mypy.types import TypeVarDef  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    # Backward-compatible with TypeVarDef from Mypy 0.910.
    from mypy.types import TypeVarType as TypeVarDef

CONFIGFILE_KEY = 'pydantic-mypy'
METADATA_KEY = 'pydantic-mypy-metadata'
_NAMESPACE = __name__[:-5]  # 'pydantic' in 1.10.X, 'pydantic.v1' in v2.X
BASEMODEL_FULLNAME = f'{_NAMESPACE}.main.BaseModel'
BASESETTINGS_FULLNAME = f'{_NAMESPACE}.env_settings.BaseSettings'
MODEL_METACLASS_FULLNAME = f'{_NAMESPACE}.main.ModelMetaclass'
FIELD_FULLNAME = f'{_NAMESPACE}.fields.Field'
DATACLASS_FULLNAME = f'{_NAMESPACE}.dataclasses.dataclass'


def parse_mypy_version(version: str) -> Tuple[int, ...]:
    return tuple(map(int, version.partition('+')[0].split('.')))


MYPY_VERSION_TUPLE = parse_mypy_version(mypy_version)
BUILTINS_NAME = 'builtins' if MYPY_VERSION_TUPLE >= (0, 930) else '__builtins__'

# Increment version if plugin changes and mypy caches should be invalidated
__version__ = 2


def plugin(version: str) -> 'TypingType[Plugin]':
    """
    `version` is the mypy version string

    We might want to use this to print a warning if the mypy version being used is
    newer, or especially older, than we expect (or need).
    """
    return PydanticPlugin


class PydanticPlugin(Plugin):
    def __init__(self, options: Options) -> None:
        self.plugin_config = PydanticPluginConfig(options)
        self._plugin_data = self.plugin_config.to_data()
        super().__init__(options)

    def get_base_class_hook(self, fullname: str) -> 'Optional[Callable[[ClassDefContext], None]]':
        sym = self.lookup_fully_qualified(fullname)
        if sym and isinstance(sym.node, TypeInfo):  # pragma: no branch
            # No branching may occur if the mypy cache has not been cleared
            if any(get_fullname(base) == BASEMODEL_FULLNAME for base in sym.node.mro):
                return self._pydantic_model_class_maker_callback
        return None

    def get_metaclass_hook(self, fullname: str) -> Optional[Callable[[ClassDefContext], None]]:
        if fullname == MODEL_METACLASS_FULLNAME:
            return self._pydantic_model_metaclass_marker_callback
        return None

    def get_function_hook(self, fullname: str) -> 'Optional[Callable[[FunctionContext], Type]]':
        sym = self.lookup_fully_qualified(fullname)
        if sym and sym.fullname == FIELD_FULLNAME:
            return self._pydantic_field_callback
        return None

    def get_method_hook(self, fullname: str) -> Optional[Callable[[MethodContext], Type]]:
        if fullname.endswith('.from_orm'):
            return from_orm_callback
        return None

    def get_class_decorator_hook(self, fullname: str) -> Optional[Callable[[ClassDefContext], None]]:
        """Mark pydantic.dataclasses as dataclass.

        Mypy version 1.1.1 added support for `@dataclass_transform` decorator.
        """
        if fullname == DATACLASS_FULLNAME and MYPY_VERSION_TUPLE < (1, 1):
            return dataclasses.dataclass_class_maker_callback  # type: ignore[return-value]
        return None

    def report_config_data(self, ctx: ReportConfigContext) -> Dict[str, Any]:
        """Return all plugin config data.

        Used by mypy to determine if cache needs to be discarded.
        """
        return self._plugin_data

    def _pydantic_model_class_maker_callback(self, ctx: ClassDefContext) -> None:
        transformer = PydanticModelTransformer(ctx, self.plugin_config)
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
            info_metaclass.type.dataclass_transform_spec = None  # type: ignore[attr-defined]

    def _pydantic_field_callback(self, ctx: FunctionContext) -> 'Type':
        """
        Extract the type of the `default` argument from the Field function, and use it as the return type.

        In particular:
        * Check whether the default and default_factory argument is specified.
        * Output an error if both are specified.
        * Retrieve the type of the argument which is specified, and use it as return type for the function.
        """
        default_any_type = ctx.default_return_type

        assert ctx.callee_arg_names[0] == 'default', '"default" is no longer first argument in Field()'
        assert ctx.callee_arg_names[1] == 'default_factory', '"default_factory" is no longer second argument in Field()'
        default_args = ctx.args[0]
        default_factory_args = ctx.args[1]

        if default_args and default_factory_args:
            error_default_and_default_factory_specified(ctx.api, ctx.context)
            return default_any_type

        if default_args:
            default_type = ctx.arg_types[0][0]
            default_arg = default_args[0]

            # Fallback to default Any type if the field is required
            if not isinstance(default_arg, EllipsisExpr):
                return default_type

        elif default_factory_args:
            default_factory_type = ctx.arg_types[1][0]

            # Functions which use `ParamSpec` can be overloaded, exposing the callable's types as a parameter
            # Pydantic calls the default factory without any argument, so we retrieve the first item
            if isinstance(default_factory_type, Overloaded):
                if MYPY_VERSION_TUPLE > (0, 910):
                    default_factory_type = default_factory_type.items[0]
                else:
                    # Mypy0.910 exposes the items of overloaded types in a function
                    default_factory_type = default_factory_type.items()[0]  # type: ignore[operator]

            if isinstance(default_factory_type, CallableType):
                ret_type = get_proper_type(default_factory_type.ret_type)
                if (
                    isinstance(ret_type, Instance)
                    and ret_type.args
                    and all(isinstance(arg, TypeVarType) for arg in ret_type.args)
                ):
                    # Looks like the default factory is a type like `list` or `dict`, replace all args with `Any`
                    ret_type = ret_type.copy_modified(args=[default_any_type] * len(ret_type.args))
                return ret_type

        return default_any_type


class PydanticPluginConfig:
    __slots__ = (
        'init_forbid_extra',
        'init_typed',
        'warn_required_dynamic_aliases',
        'warn_untyped_fields',
        'debug_dataclass_transform',
    )
    init_forbid_extra: bool
    init_typed: bool
    warn_required_dynamic_aliases: bool
    warn_untyped_fields: bool
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

    def to_data(self) -> Dict[str, Any]:
        return {key: getattr(self, key) for key in self.__slots__}


def from_orm_callback(ctx: MethodContext) -> Type:
    """
    Raise an error if orm_mode is not enabled
    """
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
    orm_mode = pydantic_metadata.get('config', {}).get('orm_mode')
    if orm_mode is not True:
        error_from_orm(get_name(model_type.type), ctx.api, ctx.context)
    return ctx.default_return_type


class PydanticModelTransformer:
    tracked_config_fields: Set[str] = {
        'extra',
        'allow_mutation',
        'frozen',
        'orm_mode',
        'allow_population_by_field_name',
        'alias_generator',
    }

    def __init__(self, ctx: ClassDefContext, plugin_config: PydanticPluginConfig) -> None:
        self._ctx = ctx
        self.plugin_config = plugin_config

    def transform(self) -> None:
        """
        Configures the BaseModel subclass according to the plugin settings.

        In particular:
        * determines the model config and fields,
        * adds a fields-aware signature for the initializer and construct methods
        * freezes the class if allow_mutation = False or frozen = True
        * stores the fields, config, and if the class is settings in the mypy metadata for access by subclasses
        """
        ctx = self._ctx
        info = ctx.cls.info

        self.adjust_validator_signatures()
        config = self.collect_config()
        fields = self.collect_fields(config)
        is_settings = any(get_fullname(base) == BASESETTINGS_FULLNAME for base in info.mro[:-1])
        self.add_initializer(fields, config, is_settings)
        self.add_construct_method(fields)
        self.set_frozen(fields, frozen=config.allow_mutation is False or config.frozen is True)
        info.metadata[METADATA_KEY] = {
            'fields': {field.name: field.serialize() for field in fields},
            'config': config.set_values_dict(),
        }

    def adjust_validator_signatures(self) -> None:
        """When we decorate a function `f` with `pydantic.validator(...), mypy sees
        `f` as a regular method taking a `self` instance, even though pydantic
        internally wraps `f` with `classmethod` if necessary.

        Teach mypy this by marking any function whose outermost decorator is a
        `validator()` call as a classmethod.
        """
        for name, sym in self._ctx.cls.info.names.items():
            if isinstance(sym.node, Decorator):
                first_dec = sym.node.original_decorators[0]
                if (
                    isinstance(first_dec, CallExpr)
                    and isinstance(first_dec.callee, NameExpr)
                    and first_dec.callee.fullname == f'{_NAMESPACE}.class_validators.validator'
                ):
                    sym.node.func.is_class = True

    def collect_config(self) -> 'ModelConfigData':
        """
        Collects the values of the config attributes that are used by the plugin, accounting for parent classes.
        """
        ctx = self._ctx
        cls = ctx.cls
        config = ModelConfigData()
        for stmt in cls.defs.body:
            if not isinstance(stmt, ClassDef):
                continue
            if stmt.name == 'Config':
                for substmt in stmt.defs.body:
                    if not isinstance(substmt, AssignmentStmt):
                        continue
                    config.update(self.get_config_update(substmt))
                if (
                    config.has_alias_generator
                    and not config.allow_population_by_field_name
                    and self.plugin_config.warn_required_dynamic_aliases
                ):
                    error_required_dynamic_aliases(ctx.api, stmt)
        for info in cls.info.mro[1:]:  # 0 is the current class
            if METADATA_KEY not in info.metadata:
                continue

            # Each class depends on the set of fields in its ancestors
            ctx.api.add_plugin_dependency(make_wildcard_trigger(get_fullname(info)))
            for name, value in info.metadata[METADATA_KEY]['config'].items():
                config.setdefault(name, value)
        return config

    def collect_fields(self, model_config: 'ModelConfigData') -> List['PydanticModelField']:
        """
        Collects the fields for the model, accounting for parent classes
        """
        # First, collect fields belonging to the current class.
        ctx = self._ctx
        cls = self._ctx.cls
        fields = []  # type: List[PydanticModelField]
        known_fields = set()  # type: Set[str]
        for stmt in cls.defs.body:
            if not isinstance(stmt, AssignmentStmt):  # `and stmt.new_syntax` to require annotation
                continue

            lhs = stmt.lvalues[0]
            if not isinstance(lhs, NameExpr) or not is_valid_field(lhs.name):
                continue

            if not stmt.new_syntax and self.plugin_config.warn_untyped_fields:
                error_untyped_fields(ctx.api, stmt)

            # if lhs.name == '__config__':  # BaseConfig not well handled; I'm not sure why yet
            #     continue

            sym = cls.info.names.get(lhs.name)
            if sym is None:  # pragma: no cover
                # This is likely due to a star import (see the dataclasses plugin for a more detailed explanation)
                # This is the same logic used in the dataclasses plugin
                continue

            node = sym.node
            if isinstance(node, PlaceholderNode):  # pragma: no cover
                # See the PlaceholderNode docstring for more detail about how this can occur
                # Basically, it is an edge case when dealing with complex import logic
                # This is the same logic used in the dataclasses plugin
                continue
            if not isinstance(node, Var):  # pragma: no cover
                # Don't know if this edge case still happens with the `is_valid_field` check above
                # but better safe than sorry
                continue

            # x: ClassVar[int] is ignored by dataclasses.
            if node.is_classvar:
