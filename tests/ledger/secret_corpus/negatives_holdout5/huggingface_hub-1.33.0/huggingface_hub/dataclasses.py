import collections.abc
import inspect
import types
from collections.abc import Callable
from dataclasses import MISSING, Field, field, fields, make_dataclass
from functools import lru_cache, wraps
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Literal,
    Type,
    TypeVar,
    Union,
    get_args,
    get_origin,
    overload,
)


try:
    # Python 3.11+
    from typing import NotRequired, Required  # type: ignore
except ImportError:
    try:
        # In case typing_extensions is installed
        from typing_extensions import NotRequired, Required  # type: ignore
    except ImportError:
        # Fallback: create dummy types that will never match
        Required = type("Required", (), {})  # type: ignore
        NotRequired = type("NotRequired", (), {})  # type: ignore

from .errors import (
    StrictDataclassClassValidationError,
    StrictDataclassDefinitionError,
    StrictDataclassFieldValidationError,
)


Validator_T = Callable[[Any], None]
T = TypeVar("T")
TypedDictType = TypeVar("TypedDictType", bound=dict[str, Any])

_TYPED_DICT_DEFAULT_VALUE = object()  # used as default value in TypedDict fields (to distinguish from None)


# The overload decorator helps type checkers understand the different return types
@overload
def strict(cls: Type[T]) -> Type[T]: ...


@overload
def strict(*, accept_kwargs: bool = False) -> Callable[[Type[T]], Type[T]]: ...


def strict(cls: Type[T] | None = None, *, accept_kwargs: bool = False) -> Type[T] | Callable[[Type[T]], Type[T]]:
    """
    Decorator to add strict validation to a dataclass.

    This decorator must be used on top of `@dataclass` to ensure IDEs and static typing tools
    recognize the class as a dataclass.

    Can be used with or without arguments:
    - `@strict`
    - `@strict(accept_kwargs=True)`

    Args:
        cls:
            The class to convert to a strict dataclass.
        accept_kwargs (`bool`, *optional*):
            If True, allows arbitrary keyword arguments in `__init__`. Defaults to False.

    Returns:
        The enhanced dataclass with strict validation on field assignment.

    Example:
    ```py
    >>> from dataclasses import dataclass
    >>> from huggingface_hub.dataclasses import as_validated_field, strict, validated_field

    >>> @as_validated_field
    >>> def positive_int(value: int):
    ...     if not value >= 0:
    ...         raise ValueError(f"Value must be positive, got {value}")

    >>> @strict(accept_kwargs=True)
    ... @dataclass
    ... class User:
    ...     name: str
    ...     age: int = positive_int(default=10)

    # Initialize
    >>> User(name="John")
    User(name='John', age=10)

    # Extra kwargs are accepted
    >>> User(name="John", age=30, lastname="Doe")
    User(name='John', age=30, *lastname='Doe')

    # Invalid type => raises
    >>> User(name="John", age="30")
    huggingface_hub.errors.StrictDataclassFieldValidationError: Validation error for field 'age':
        TypeError: Field 'age' expected int, got str (value: '30')

    # Invalid value => raises
    >>> User(name="John", age=-1)
    huggingface_hub.errors.StrictDataclassFieldValidationError: Validation error for field 'age':
        ValueError: Value must be positive, got -1
    ```
    """

    def wrap(cls: Type[T]) -> Type[T]:
        if not hasattr(cls, "__dataclass_fields__"):
            raise StrictDataclassDefinitionError(
                f"Class '{cls.__name__}' must be a dataclass before applying @strict."
            )

        # List and store validators
        field_validators: dict[str, list[Validator_T]] = {}
        for f in fields(cls):  # type: ignore
            validators = []
            validators.append(_create_type_validator(f))
            custom_validator = f.metadata.get("validator")
            if custom_validator is not None:
                if not isinstance(custom_validator, list):
                    custom_validator = [custom_validator]
                for validator in custom_validator:
                    if not _is_validator(validator):
                        raise StrictDataclassDefinitionError(
                            f"Invalid validator for field '{f.name}': {validator}. Must be a callable taking a single argument."
                        )
                validators.extend(custom_validator)
            field_validators[f.name] = validators
        cls.__validators__ = field_validators  # type: ignore

        # Override __setattr__ to validate fields on assignment
        original_setattr = cls.__setattr__

        def __strict_setattr__(self: Any, name: str, value: Any) -> None:
            """Custom __setattr__ method for strict dataclasses."""
            # Run all validators
            for validator in self.__validators__.get(name, []):
                try:
                    validator(value)
                except (ValueError, TypeError) as e:
                    raise StrictDataclassFieldValidationError(field=name, cause=e) from e

            # If validation passed, set the attribute
            original_setattr(self, name, value)

        cls.__setattr__ = __strict_setattr__  # type: ignore

        if accept_kwargs:
            # (optional) Override __init__ to accept arbitrary keyword arguments
            original_init = cls.__init__

            @wraps(original_init)
            def __init__(self, *args, **kwargs: Any) -> None:
                # Extract only the fields that are part of the dataclass
                dataclass_fields = {f.name for f in fields(cls)}  # type: ignore
                standard_kwargs = {k: v for k, v in kwargs.items() if k in dataclass_fields}

                # User shouldn't define custom `__init__` when `accepts_kwargs`, and instead
                # are advised to move field manipulation to `__post_init__` (e.g., derive new field from existing ones)
                # We need to call bare `__init__` here without `__post_init__` but the``original_init`` would call
                # post-init right away with no kwargs.
                if len(args) > 0:
                    raise ValueError(
                        f"When `accept_kwargs=True`, {cls.__name__} accepts only keyword arguments, "
                        f"but found `{len(args)}` positional args."
                    )

                for f in fields(cls):  # type: ignore
                    if f.name in standard_kwargs:
                        setattr(self, f.name, standard_kwargs[f.name])
                    elif f.default is not MISSING:
                        setattr(self, f.name, f.default)
                    elif f.default_factory is not MISSING:
                        setattr(self, f.name, f.default_factory())
                    else:
                        raise TypeError(f"Missing required field - '{f.name}'")

                # Pass any additional kwargs to `__post_init__` and let the object
                # decide whether to set the attr or use for different purposes (e.g. BC checks)
                additional_kwargs = {}
                for name, value in kwargs.items():
                    if name not in dataclass_fields:
                        additional_kwargs[name] = value

                self.__post_init__(**additional_kwargs)

            cls.__init__ = __init__  # type: ignore

            # Define a default __post_init__ if not defined
            if not hasattr(cls, "__post_init__"):

                def __post_init__(self, **kwargs: Any) -> None:
                    """Default __post_init__ to accept additional kwargs."""
                    for name, value in kwargs.items():
                        setattr(self, name, value)

                cls.__post_init__ = __post_init__  # type: ignore

            # (optional) Override __repr__ to include additional kwargs
            original_repr = cls.__repr__

            @wraps(original_repr)
            def __repr__(self) -> str:
                # Call the original __repr__ to get the standard fields
                standard_repr = original_repr(self)

                # Get additional kwargs
                additional_kwargs = [
                    # add a '*' in front of additional kwargs to let the user know they are not part of the dataclass
                    f"*{k}={v!r}"
                    for k, v in self.__dict__.items()
                    if k not in cls.__dataclass_fields__  # type: ignore [attr-defined]
                ]
                additional_repr = ", ".join(additional_kwargs)

                # Combine both representations
                return f"{standard_repr[:-1]}, {additional_repr})" if additional_kwargs else standard_repr

            if cls.__dataclass_params__.repr is True:  # type: ignore [attr-defined]
                cls.__repr__ = __repr__  # type: ignore

        # List all public methods starting with `validate_` => class validators.
        class_validators = []

        for name in dir(cls):
            if not name.startswith("validate_"):
                continue
            method = getattr(cls, name)
            if not callable(method):
                continue
            if len(inspect.signature(method).parameters) != 1:
                raise StrictDataclassDefinitionError(
                    f"Class '{cls.__name__}' has a class validator '{name}' that takes more than one argument."
                    " Class validators must take only 'self' as an argument. Methods starting with 'validate_'"
                    " are considered to be class validators."
                )
            class_validators.append(method)

        cls.__class_validators__ = class_validators  # type: ignore

        # Add `validate` method to the class, but first check if it already exists
        def validate(self: T) -> None:
            """Run class validators on the instance."""
            for validator in cls.__class_validators__:  # type: ignore [attr-defined]
                try:
                    validator(self)
                except (ValueError, TypeError) as e:
                    raise StrictDataclassClassValidationError(validator=validator.__name__, cause=e) from e

        # Hack to be able to raise if `.validate()` already exists except if it was created by this decorator on a parent class
        # (in which case we just override it)
        validate.__is_defined_by_strict_decorator__ = True  # type: ignore [attr-defined]

        if hasattr(cls, "validate"):
            if not getattr(cls.validate, "__is_defined_by_strict_decorator__", False):  # type: ignore [attr-defined]
                raise StrictDataclassDefinitionError(
                    f"Class '{cls.__name__}' already implements a method called 'validate'."
                    " This method name is reserved when using the @strict decorator on a dataclass."
                    " If you want to keep your own method, please rename it."
                )

        cls.validate = validate  # type: ignore

        # Run class validators after initialization
        initial_init = cls.__init__

        @wraps(initial_init)
        def init_with_validate(self, *args, **kwargs) -> None:
            """Run class validators after initialization."""
            initial_init(self, *args, **kwargs)  # type: ignore [call-arg]
            cls.validate(self)  # type: ignore [attr-defined]

        setattr(cls, "__init__", init_with_validate)

        return cls

    # Return wrapped class or the decorator itself
    return wrap(cls) if cls is not None else wrap


def validate_typed_dict(schema: type[TypedDictType], data: dict) -> None:
    """
    Validate that a dictionary conforms to the types defined in a TypedDict class.

    Under the hood, the typed dict is converted to a strict dataclass and validated using the `@strict` decorator.

    Args:
        schema (`type[TypedDictType]`):
            The TypedDict class defining the expected structure and types.
        data (`dict`):
            The dictionary to validate.

    Raises:
        `StrictDataclassFieldValidationError`:
            If any field in the dictionary does not conform to the expected type.

    Example:
    ```py
    >>> from typing import Annotated, TypedDict
    >>> from huggingface_hub.dataclasses import validate_typed_dict

    >>> def positive_int(value: int):
    ...     if not value >= 0:
    ...         raise ValueError(f"Value must be positive, got {value}")

    >>> class User(TypedDict):
    ...     name: str
    ...     age: Annotated[int, positive_int]

    >>> # Valid data
    >>> validate_typed_dict(User, {"name": "John", "age": 30})

    >>> # Invalid type for age
    >>> validate_typed_dict(User, {"name": "John", "age": "30"})
    huggingface_hub.errors.StrictDataclassFieldValidationError: Validation error for field 'age':
        TypeError: Field 'age' expected int, got str (value: '30')

    >>> # Invalid value for age
    >>> validate_typed_dict(User, {"name": "John", "age": -1})
    huggingface_hub.errors.StrictDataclassFieldValidationError: Validation error for field 'age':
        ValueError: Value must be positive, got -1
    ```
    """
    # Convert typed dict to dataclass
    strict_cls = _build_strict_cls_from_typed_dict(schema)

    # Validate the data by instantiating the strict dataclass
    strict_cls(**data)  # will raise if validation fails


@lru_cache
def _build_strict_cls_from_typed_dict(schema: type[TypedDictType]) -> Type:
    # Extract type hints from the TypedDict class
    type_hints = _get_typed_dict_annotations(schema)

    # If the TypedDict is not total, wrap fields as NotRequired (unless explicitly Required or NotRequired)
    if not getattr(schema, "__total__", True):
        for key, value in type_hints.items():
            origin = get_origin(value)

            if origin is Annotated:
                base, *meta = get_args(value)
                if not _is_required_or_notrequired(base):
                    base = NotRequired[base]
                type_hints[key] = Annotated[tuple([base] + list(meta))]  # type: ignore
            elif not _is_required_or_notrequired(value):
                type_hints[key] = NotRequired[value]

    # Convert type hints to dataclass fields
    fields = []
    for key, value in type_hints.items():
        if get_origin(value) is Annotated:
            base, *meta = get_args(value)
            fields.append((key, base, field(default=_TYPED_DICT_DEFAULT_VALUE, metadata={"validator": meta[0]})))
        else:
            fields.append((key, value, field(default=_TYPED_DICT_DEFAULT_VALUE)))

    # Create a strict dataclass from the TypedDict fields
    return strict(make_dataclass(schema.__name__, fields))


def _get_typed_dict_annotations(schema: type[TypedDictType]) -> dict[str, Any]:
    """Extract type annotations from a TypedDict class."""
    try:
        # Available in Python 3.14+
        import annotationlib

        return annotationlib.get_annotations(schema)
    except ImportError:
        return {
            # We do not use `get_type_hints` here to avoid evaluating ForwardRefs (which might fail).
            # ForwardRefs are not validated by @strict anyway.
            name: value if value is not None else type(None)
            for name, value in schema.__dict__.get("__annotations__", {}).items()
        }


def validated_field(
    validator: list[Validator_T] | Validator_T,
    default: Any = MISSING,
    default_factory: Any = MISSING,
    init: bool = True,
    repr: bool = True,
    hash: bool | None = None,
    compare: bool = True,
    metadata: dict | None = None,
    **kwargs: Any,
) -> Any:
    """
    Create a dataclass field with a custom validator.

    Useful to apply several checks to a field. If only applying one rule, check out the [`as_validated_field`] decorator.

    Args:
        validator (`Callable` or `list[Callable]`):
            A method that takes a value as input and raises ValueError/TypeError if the value is invalid.
            Can be a list of validators to apply multiple checks.
        **kwargs:
            Additional arguments to pass to `dataclasses.field()`.

    Returns:
        A field with the validator attached in metadata
    """
    if not isinstance(validator, list):
        validator = [validator]
    if metadata is None:
        metadata = {}
    metadata["validator"] = validator
    return field(  # type: ignore
