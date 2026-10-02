"""This module contains related classes and functions for validation."""

from __future__ import annotations as _annotations

import dataclasses
import sys
import warnings
from functools import partialmethod
from typing import TYPE_CHECKING, Annotated, Any, Callable, Literal, TypeVar, Union, cast, overload

from pydantic_core import PydanticUndefined, core_schema
from typing_extensions import Self, TypeAlias

from ._internal import _decorators, _generics, _internal_dataclass
from .annotated_handlers import GetCoreSchemaHandler
from .errors import PydanticUserError
from .version import version_short
from .warnings import ArbitraryTypeWarning, PydanticDeprecatedSince212

if sys.version_info < (3, 11):
    from typing_extensions import Protocol
else:
    from typing import Protocol

_inspect_validator = _decorators.inspect_validator


@dataclasses.dataclass(frozen=True, **_internal_dataclass.slots_true)
class AfterValidator:
    """!!! abstract "Usage Documentation"
        [field *after* validators](../concepts/validators.md#field-after-validator)

    A metadata class that indicates that a validation should be applied **after** the inner validation logic.

    Attributes:
        func: The validator function.

    Example:
        ```python
        from typing import Annotated

        from pydantic import AfterValidator, BaseModel, ValidationError

        MyInt = Annotated[int, AfterValidator(lambda v: v + 1)]

        class Model(BaseModel):
            a: MyInt

        print(Model(a=1).a)
        #> 2

        try:
            Model(a='a')
        except ValidationError as e:
            print(e.json(indent=2))
            '''
            [
              {
                "type": "int_parsing",
                "loc": [
                  "a"
                ],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "input": "a",
                "url": "https://errors.pydantic.dev/2/v/int_parsing"
              }
            ]
            '''
        ```
    """

    func: core_schema.NoInfoValidatorFunction | core_schema.WithInfoValidatorFunction

    def __get_pydantic_core_schema__(self, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        schema = handler(source_type)
        info_arg = _inspect_validator(self.func, mode='after', type='field')
        if info_arg:
            func = cast(core_schema.WithInfoValidatorFunction, self.func)
            return core_schema.with_info_after_validator_function(func, schema=schema)
        else:
            func = cast(core_schema.NoInfoValidatorFunction, self.func)
            return core_schema.no_info_after_validator_function(func, schema=schema)

    @classmethod
    def _from_decorator(cls, decorator: _decorators.Decorator[_decorators.FieldValidatorDecoratorInfo]) -> Self:
        return cls(func=decorator.func)


@dataclasses.dataclass(frozen=True, **_internal_dataclass.slots_true)
class BeforeValidator:
    """!!! abstract "Usage Documentation"
        [field *before* validators](../concepts/validators.md#field-before-validator)

    A metadata class that indicates that a validation should be applied **before** the inner validation logic.

    Attributes:
        func: The validator function.
        json_schema_input_type: The input type used to generate the appropriate
            JSON Schema (in validation mode). The actual input type is `Any`.

    Example:
        ```python
        from typing import Annotated

        from pydantic import BaseModel, BeforeValidator

        MyInt = Annotated[int, BeforeValidator(lambda v: v + 1)]

        class Model(BaseModel):
            a: MyInt

        print(Model(a=1).a)
        #> 2

        try:
            Model(a='a')
        except TypeError as e:
            print(e)
            #> can only concatenate str (not "int") to str
        ```
    """

    func: core_schema.NoInfoValidatorFunction | core_schema.WithInfoValidatorFunction
    json_schema_input_type: Any = PydanticUndefined

    def __get_pydantic_core_schema__(self, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        schema = handler(source_type)
        input_schema = (
            None
            if self.json_schema_input_type is PydanticUndefined
            else handler.generate_schema(self.json_schema_input_type)
        )

        info_arg = _inspect_validator(self.func, mode='before', type='field')
        if info_arg:
            func = cast(core_schema.WithInfoValidatorFunction, self.func)
            return core_schema.with_info_before_validator_function(
                func,
                schema=schema,
                json_schema_input_schema=input_schema,
            )
        else:
            func = cast(core_schema.NoInfoValidatorFunction, self.func)
            return core_schema.no_info_before_validator_function(
                func, schema=schema, json_schema_input_schema=input_schema
            )

    @classmethod
    def _from_decorator(cls, decorator: _decorators.Decorator[_decorators.FieldValidatorDecoratorInfo]) -> Self:
        return cls(
            func=decorator.func,
            json_schema_input_type=decorator.info.json_schema_input_type,
        )


@dataclasses.dataclass(frozen=True, **_internal_dataclass.slots_true)
class PlainValidator:
    """!!! abstract "Usage Documentation"
        [field *plain* validators](../concepts/validators.md#field-plain-validator)

    A metadata class that indicates that a validation should be applied **instead** of the inner validation logic.

    !!! note
        Before v2.9, `PlainValidator` wasn't always compatible with JSON Schema generation for `mode='validation'`.
        You can now use the `json_schema_input_type` argument to specify the input type of the function
        to be used in the JSON schema when `mode='validation'` (the default). See the example below for more details.

    Attributes:
        func: The validator function.
        json_schema_input_type: The input type used to generate the appropriate
            JSON Schema (in validation mode). The actual input type is `Any`.

    Example:
        ```python
        from typing import Annotated, Union

        from pydantic import BaseModel, PlainValidator

        def validate(v: object) -> int:
            if not isinstance(v, (int, str)):
                raise ValueError(f'Expected int or str, got {type(v)}')

            return int(v) + 1

        MyInt = Annotated[
            int,
            PlainValidator(validate, json_schema_input_type=Union[str, int]),  # (1)!
        ]

        class Model(BaseModel):
            a: MyInt

        print(Model(a='1').a)
        #> 2

        print(Model(a=1).a)
        #> 2
        ```

        1. In this example, we've specified the `json_schema_input_type` as `Union[str, int]` which indicates to the JSON schema
        generator that in validation mode, the input type for the `a` field can be either a [`str`][] or an [`int`][].
    """

    func: core_schema.NoInfoValidatorFunction | core_schema.WithInfoValidatorFunction
    json_schema_input_type: Any = Any

    def __get_pydantic_core_schema__(self, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        # Note that for some valid uses of PlainValidator, it is not possible to generate a core schema for the
        # source_type, so calling `handler(source_type)` will error, which prevents us from generating a proper
        # serialization schema. To work around this for use cases that will not involve serialization, we simply
        # catch any PydanticSchemaGenerationError that may be raised while attempting to build the serialization schema
        # and abort any attempts to handle special serialization.
        from pydantic import PydanticSchemaGenerationError

        try:
            schema = handler(source_type)
            # TODO if `schema['serialization']` is one of `'include-exclude-dict/sequence',
            # schema validation will fail. That's why we use 'type ignore' comments below.
            serialization = schema.get(
                'serialization',
                core_schema.wrap_serializer_function_ser_schema(
                    function=lambda v, h: h(v),
                    schema=schema,
                    return_schema=handler.generate_schema(source_type),
                ),
            )
        except PydanticSchemaGenerationError:
            serialization = None

        input_schema = handler.generate_schema(self.json_schema_input_type)

        info_arg = _inspect_validator(self.func, mode='plain', type='field')
        if info_arg:
            func = cast(core_schema.WithInfoValidatorFunction, self.func)
            return core_schema.with_info_plain_validator_function(
                func,
                serialization=serialization,  # pyright: ignore[reportArgumentType]
                json_schema_input_schema=input_schema,
            )
        else:
            func = cast(core_schema.NoInfoValidatorFunction, self.func)
            return core_schema.no_info_plain_validator_function(
                func,
                serialization=serialization,  # pyright: ignore[reportArgumentType]
                json_schema_input_schema=input_schema,
            )

    @classmethod
    def _from_decorator(cls, decorator: _decorators.Decorator[_decorators.FieldValidatorDecoratorInfo]) -> Self:
        return cls(
            func=decorator.func,
            json_schema_input_type=decorator.info.json_schema_input_type,
        )


@dataclasses.dataclass(frozen=True, **_internal_dataclass.slots_true)
class WrapValidator:
    """!!! abstract "Usage Documentation"
        [field *wrap* validators](../concepts/validators.md#field-wrap-validator)

    A metadata class that indicates that a validation should be applied **around** the inner validation logic.

    Attributes:
        func: The validator function.
        json_schema_input_type: The input type used to generate the appropriate
            JSON Schema (in validation mode). The actual input type is `Any`.

    ```python
    from datetime import datetime
    from typing import Annotated

    from pydantic import BaseModel, ValidationError, WrapValidator

    def validate_timestamp(v, handler):
        if v == 'now':
            # we don't want to bother with further validation, just return the new value
            return datetime.now()
        try:
            return handler(v)
        except ValidationError:
            # validation failed, in this case we want to return a default value
            return datetime(2000, 1, 1)

    MyTimestamp = Annotated[datetime, WrapValidator(validate_timestamp)]

    class Model(BaseModel):
        a: MyTimestamp

    print(Model(a='now').a)
    #> 2032-01-02 03:04:05.000006
    print(Model(a='invalid').a)
    #> 2000-01-01 00:00:00
    ```
    """

    func: core_schema.NoInfoWrapValidatorFunction | core_schema.WithInfoWrapValidatorFunction
    json_schema_input_type: Any = PydanticUndefined

    def __get_pydantic_core_schema__(self, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        schema = handler(source_type)
        input_schema = (
            None
            if self.json_schema_input_type is PydanticUndefined
            else handler.generate_schema(self.json_schema_input_type)
        )

        info_arg = _inspect_validator(self.func, mode='wrap', type='field')
        if info_arg:
            func = cast(core_schema.WithInfoWrapValidatorFunction, self.func)
            return core_schema.with_info_wrap_validator_function(
                func,
                schema=schema,
                json_schema_input_schema=input_schema,
            )
        else:
            func = cast(core_schema.NoInfoWrapValidatorFunction, self.func)
            return core_schema.no_info_wrap_validator_function(
                func,
                schema=schema,
                json_schema_input_schema=input_schema,
            )

    @classmethod
    def _from_decorator(cls, decorator: _decorators.Decorator[_decorators.FieldValidatorDecoratorInfo]) -> Self:
        return cls(
            func=decorator.func,
            json_schema_input_type=decorator.info.json_schema_input_type,
        )


if TYPE_CHECKING:

    class _OnlyValueValidatorClsMethod(Protocol):
        def __call__(self, cls: Any, value: Any, /) -> Any: ...

    class _V2ValidatorClsMethod(Protocol):
        def __call__(self, cls: Any, value: Any, info: core_schema.ValidationInfo[Any], /) -> Any: ...

    class _OnlyValueWrapValidatorClsMethod(Protocol):
        def __call__(self, cls: Any, value: Any, handler: core_schema.ValidatorFunctionWrapHandler, /) -> Any: ...

    class _V2WrapValidatorClsMethod(Protocol):
        def __call__(
            self,
            cls: Any,
            value: Any,
            handler: core_schema.ValidatorFunctionWrapHandler,
            info: core_schema.ValidationInfo[Any],
            /,
        ) -> Any: ...

    _V2Validator = Union[
        _V2ValidatorClsMethod,
        core_schema.WithInfoValidatorFunction,
        _OnlyValueValidatorClsMethod,
        core_schema.NoInfoValidatorFunction,
    ]

    _V2WrapValidator = Union[
        _V2WrapValidatorClsMethod,
        core_schema.WithInfoWrapValidatorFunction,
        _OnlyValueWrapValidatorClsMethod,
        core_schema.NoInfoWrapValidatorFunction,
    ]

    _PartialClsOrStaticMethod: TypeAlias = Union[classmethod[Any, Any, Any], staticmethod[Any, Any], partialmethod[Any]]

    _V2BeforeAfterOrPlainValidatorType = TypeVar(
        '_V2BeforeAfterOrPlainValidatorType',
        bound=Union[_V2Validator, _PartialClsOrStaticMethod],
    )
    _V2WrapValidatorType = TypeVar('_V2WrapValidatorType', bound=Union[_V2WrapValidator, _PartialClsOrStaticMethod])

FieldValidatorModes: TypeAlias = Literal['before', 'after', 'wrap', 'plain']


@overload
def field_validator(
    field: str,
    /,
    *fields: str,
    mode: Literal['wrap'],
    check_fields: bool | None = ...,
    json_schema_input_type: Any = ...,
) -> Callable[[_V2WrapValidatorType], _V2WrapValidatorType]: ...


@overload
def field_validator(
    field: str,
    /,
    *fields: str,
    mode: Literal['before', 'plain'],
    check_fields: bool | None = ...,
    json_schema_input_type: Any = ...,
) -> Callable[[_V2BeforeAfterOrPlainValidatorType], _V2BeforeAfterOrPlainValidatorType]: ...


@overload
def field_validator(
    field: str,
    /,
    *fields: str,
    mode: Literal['after'] = ...,
    check_fields: bool | None = ...,
) -> Callable[[_V2BeforeAfterOrPlainValidatorType], _V2BeforeAfterOrPlainValidatorType]: ...


def field_validator(  # noqa: D417
    field: str,
    /,
    *fields: str,
    mode: FieldValidatorModes = 'after',
    check_fields: bool | None = None,
    json_schema_input_type: Any = PydanticUndefined,
) -> Callable[[Any], Any]:
    """!!! abstract "Usage Documentation"
        [field validators](../concepts/validators.md#field-validators)

    Decorate methods on the class indicating that they should be used to validate fields.

    Example usage:
    ```python
    from typing import Any

    from pydantic import (
        BaseModel,
        ValidationError,
        field_validator,
    )

    class Model(BaseModel):
        a: str

        @field_validator('a')
        @classmethod
        def ensure_foobar(cls, v: Any):
            if 'foobar' not in v:
                raise ValueError('"foobar" not found in a')
            return v

    print(repr(Model(a='this is foobar good')))
    #> Model(a='this is foobar good')

    try:
        Model(a='snap')
    except ValidationError as exc_info:
        print(exc_info)
        '''
        1 validation error for Model
        a
          Value error, "foobar" not found in a [type=value_error, input_value='snap', input_type=str]
        '''
    ```

    For more in depth examples, see [Field Validators](../concepts/validators.md#field-validators).

    Args:
        *fields: The field names the validator should apply to.
        mode: Specifies whether to validate the fields before or after validation.
        check_fields: Whether to check that the fields actually exist on the model.
        json_schema_input_type: The input type of the function. This is only used to generate
