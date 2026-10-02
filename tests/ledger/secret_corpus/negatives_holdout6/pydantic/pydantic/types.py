"""The types module contains custom types used by pydantic."""

from __future__ import annotations as _annotations

import base64
import dataclasses as _dataclasses
import re
from collections.abc import Hashable, Iterator
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from re import Pattern
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    Literal,
    TypeVar,
    Union,
    cast,
)
from uuid import UUID

import annotated_types
from annotated_types import BaseMetadata, MaxLen, MinLen
from pydantic_core import CoreSchema, PydanticCustomError, SchemaSerializer, core_schema
from typing_extensions import Protocol, TypeAlias, TypeAliasType, deprecated, get_args, get_origin

from ._internal import _fields, _internal_dataclass, _utils, _validators
from ._migration import getattr_migration
from .annotated_handlers import GetCoreSchemaHandler, GetJsonSchemaHandler
from .errors import PydanticUserError
from .json_schema import JsonSchemaValue
from .warnings import PydanticDeprecatedSince20

if TYPE_CHECKING:
    from ._internal._core_metadata import CoreMetadata

__all__ = (
    'Strict',
    'StrictStr',
    'SocketPath',
    'conbytes',
    'conlist',
    'conset',
    'confrozenset',
    'constr',
    'ImportString',
    'conint',
    'PositiveInt',
    'NegativeInt',
    'NonNegativeInt',
    'NonPositiveInt',
    'confloat',
    'PositiveFloat',
    'NegativeFloat',
    'NonNegativeFloat',
    'NonPositiveFloat',
    'FiniteFloat',
    'condecimal',
    'UUID1',
    'UUID3',
    'UUID4',
    'UUID5',
    'UUID6',
    'UUID7',
    'UUID8',
    'FilePath',
    'DirectoryPath',
    'NewPath',
    'Json',
    'Secret',
    'SecretStr',
    'SecretBytes',
    'StrictBool',
    'StrictBytes',
    'StrictInt',
    'StrictFloat',
    'PaymentCardNumber',
    'ByteSize',
    'PastDate',
    'FutureDate',
    'PastDatetime',
    'FutureDatetime',
    'condate',
    'AwareDatetime',
    'NaiveDatetime',
    'AllowInfNan',
    'EncoderProtocol',
    'EncodedBytes',
    'EncodedStr',
    'Base64Encoder',
    'Base64Bytes',
    'Base64Str',
    'Base64UrlBytes',
    'Base64UrlStr',
    'GetPydanticSchema',
    'StringConstraints',
    'Tag',
    'Discriminator',
    'JsonValue',
    'OnErrorOmit',
    'FailFast',
)


T = TypeVar('T')


@_dataclasses.dataclass
class Strict(_fields.PydanticMetadata, BaseMetadata):
    """!!! abstract "Usage Documentation"
        [Strict Mode with `Annotated` `Strict`](../concepts/strict_mode.md#strict-mode-with-annotated-strict)

    A field metadata class to indicate that a field should be validated in strict mode.
    Use this class as an annotation via [`Annotated`](https://docs.python.org/3/library/typing.html#typing.Annotated), as seen below.

    Attributes:
        strict: Whether to validate the field in strict mode.

    Example:
        ```python
        from typing import Annotated

        from pydantic.types import Strict

        StrictBool = Annotated[bool, Strict()]
        ```
    """

    strict: bool = True

    def __hash__(self) -> int:
        return hash(self.strict)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ BOOLEAN TYPES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

StrictBool = Annotated[bool, Strict()]
"""A boolean that must be either ``True`` or ``False``."""

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ INTEGER TYPES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def conint(
    *,
    strict: bool | None = None,
    gt: int | None = None,
    ge: int | None = None,
    lt: int | None = None,
    le: int | None = None,
    multiple_of: int | None = None,
) -> type[int]:
    """
    !!! warning "Discouraged"
        This function is **discouraged** in favor of using
        [`Annotated`](https://docs.python.org/3/library/typing.html#typing.Annotated) with
        [`Field`][pydantic.fields.Field] instead.

        This function will be **deprecated** in Pydantic 3.0.

        The reason is that `conint` returns a type, which doesn't play well with static analysis tools.

        === ":x: Don't do this"
            ```python
            from pydantic import BaseModel, conint

            class Foo(BaseModel):
                bar: conint(strict=True, gt=0)
            ```

        === ":white_check_mark: Do this"
            ```python
            from typing import Annotated

            from pydantic import BaseModel, Field

            class Foo(BaseModel):
                bar: Annotated[int, Field(strict=True, gt=0)]
            ```

    A wrapper around `int` that allows for additional constraints.

    Args:
        strict: Whether to validate the integer in strict mode. Defaults to `None`.
        gt: The value must be greater than this.
        ge: The value must be greater than or equal to this.
        lt: The value must be less than this.
        le: The value must be less than or equal to this.
        multiple_of: The value must be a multiple of this.

    Returns:
        The wrapped integer type.

    ```python
    from pydantic import BaseModel, ValidationError, conint

    class ConstrainedExample(BaseModel):
        constrained_int: conint(gt=1)

    m = ConstrainedExample(constrained_int=2)
    print(repr(m))
    #> ConstrainedExample(constrained_int=2)

    try:
        ConstrainedExample(constrained_int=0)
    except ValidationError as e:
        print(e.errors())
        '''
        [
            {
                'type': 'greater_than',
                'loc': ('constrained_int',),
                'msg': 'Input should be greater than 1',
                'input': 0,
                'ctx': {'gt': 1},
                'url': 'https://errors.pydantic.dev/2/v/greater_than',
            }
        ]
        '''
    ```

    """  # noqa: D212
    return Annotated[  # pyright: ignore[reportReturnType]
        int,
        Strict(strict) if strict is not None else None,
        annotated_types.Interval(gt=gt, ge=ge, lt=lt, le=le),
        annotated_types.MultipleOf(multiple_of) if multiple_of is not None else None,
    ]


PositiveInt = Annotated[int, annotated_types.Gt(0)]
"""An integer that must be greater than zero.

```python
from pydantic import BaseModel, PositiveInt, ValidationError

class Model(BaseModel):
    positive_int: PositiveInt

m = Model(positive_int=1)
print(repr(m))
#> Model(positive_int=1)

try:
    Model(positive_int=-1)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'greater_than',
            'loc': ('positive_int',),
            'msg': 'Input should be greater than 0',
            'input': -1,
            'ctx': {'gt': 0},
            'url': 'https://errors.pydantic.dev/2/v/greater_than',
        }
    ]
    '''
```
"""
NegativeInt = Annotated[int, annotated_types.Lt(0)]
"""An integer that must be less than zero.

```python
from pydantic import BaseModel, NegativeInt, ValidationError

class Model(BaseModel):
    negative_int: NegativeInt

m = Model(negative_int=-1)
print(repr(m))
#> Model(negative_int=-1)

try:
    Model(negative_int=1)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'less_than',
            'loc': ('negative_int',),
            'msg': 'Input should be less than 0',
            'input': 1,
            'ctx': {'lt': 0},
            'url': 'https://errors.pydantic.dev/2/v/less_than',
        }
    ]
    '''
```
"""
NonPositiveInt = Annotated[int, annotated_types.Le(0)]
"""An integer that must be less than or equal to zero.

```python
from pydantic import BaseModel, NonPositiveInt, ValidationError

class Model(BaseModel):
    non_positive_int: NonPositiveInt

m = Model(non_positive_int=0)
print(repr(m))
#> Model(non_positive_int=0)

try:
    Model(non_positive_int=1)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'less_than_equal',
            'loc': ('non_positive_int',),
            'msg': 'Input should be less than or equal to 0',
            'input': 1,
            'ctx': {'le': 0},
            'url': 'https://errors.pydantic.dev/2/v/less_than_equal',
        }
    ]
    '''
```
"""
NonNegativeInt = Annotated[int, annotated_types.Ge(0)]
"""An integer that must be greater than or equal to zero.

```python
from pydantic import BaseModel, NonNegativeInt, ValidationError

class Model(BaseModel):
    non_negative_int: NonNegativeInt

m = Model(non_negative_int=0)
print(repr(m))
#> Model(non_negative_int=0)

try:
    Model(non_negative_int=-1)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'greater_than_equal',
            'loc': ('non_negative_int',),
            'msg': 'Input should be greater than or equal to 0',
            'input': -1,
            'ctx': {'ge': 0},
            'url': 'https://errors.pydantic.dev/2/v/greater_than_equal',
        }
    ]
    '''
```
"""
StrictInt = Annotated[int, Strict()]
"""An integer that must be validated in strict mode.

```python
from pydantic import BaseModel, StrictInt, ValidationError

class StrictIntModel(BaseModel):
    strict_int: StrictInt

try:
    StrictIntModel(strict_int=3.14159)
except ValidationError as e:
    print(e)
    '''
    1 validation error for StrictIntModel
    strict_int
      Input should be a valid integer [type=int_type, input_value=3.14159, input_type=float]
    '''
```
"""

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ FLOAT TYPES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


@_dataclasses.dataclass
class AllowInfNan(_fields.PydanticMetadata):
    """A field metadata class to indicate that a field should allow `-inf`, `inf`, and `nan`.

    Use this class as an annotation via [`Annotated`](https://docs.python.org/3/library/typing.html#typing.Annotated), as seen below.

    Attributes:
        allow_inf_nan: Whether to allow `-inf`, `inf`, and `nan`. Defaults to `True`.

    Example:
        ```python
        from typing import Annotated

        from pydantic.types import AllowInfNan

        LaxFloat = Annotated[float, AllowInfNan()]
        ```
    """

    allow_inf_nan: bool = True

    def __hash__(self) -> int:
        return hash(self.allow_inf_nan)


def confloat(
    *,
    strict: bool | None = None,
    gt: float | None = None,
    ge: float | None = None,
    lt: float | None = None,
    le: float | None = None,
    multiple_of: float | None = None,
    allow_inf_nan: bool | None = None,
) -> type[float]:
    """
    !!! warning "Discouraged"
        This function is **discouraged** in favor of using
        [`Annotated`](https://docs.python.org/3/library/typing.html#typing.Annotated) with
        [`Field`][pydantic.fields.Field] instead.

        This function will be **deprecated** in Pydantic 3.0.

        The reason is that `confloat` returns a type, which doesn't play well with static analysis tools.

        === ":x: Don't do this"
            ```python
            from pydantic import BaseModel, confloat

            class Foo(BaseModel):
                bar: confloat(strict=True, gt=0)
            ```

        === ":white_check_mark: Do this"
            ```python
            from typing import Annotated

            from pydantic import BaseModel, Field

            class Foo(BaseModel):
                bar: Annotated[float, Field(strict=True, gt=0)]
            ```

    A wrapper around `float` that allows for additional constraints.

    Args:
        strict: Whether to validate the float in strict mode.
        gt: The value must be greater than this.
        ge: The value must be greater than or equal to this.
        lt: The value must be less than this.
        le: The value must be less than or equal to this.
        multiple_of: The value must be a multiple of this.
        allow_inf_nan: Whether to allow `-inf`, `inf`, and `nan`.

    Returns:
        The wrapped float type.

    ```python
    from pydantic import BaseModel, ValidationError, confloat

    class ConstrainedExample(BaseModel):
        constrained_float: confloat(gt=1.0)

    m = ConstrainedExample(constrained_float=1.1)
    print(repr(m))
    #> ConstrainedExample(constrained_float=1.1)

    try:
        ConstrainedExample(constrained_float=0.9)
    except ValidationError as e:
        print(e.errors())
        '''
        [
            {
                'type': 'greater_than',
                'loc': ('constrained_float',),
                'msg': 'Input should be greater than 1',
                'input': 0.9,
                'ctx': {'gt': 1.0},
                'url': 'https://errors.pydantic.dev/2/v/greater_than',
            }
        ]
        '''
    ```
    """  # noqa: D212
    return Annotated[  # pyright: ignore[reportReturnType]
        float,
        Strict(strict) if strict is not None else None,
        annotated_types.Interval(gt=gt, ge=ge, lt=lt, le=le),
        annotated_types.MultipleOf(multiple_of) if multiple_of is not None else None,
        AllowInfNan(allow_inf_nan) if allow_inf_nan is not None else None,
    ]


PositiveFloat = Annotated[float, annotated_types.Gt(0)]
"""A float that must be greater than zero.

```python
from pydantic import BaseModel, PositiveFloat, ValidationError

class Model(BaseModel):
    positive_float: PositiveFloat

m = Model(positive_float=1.0)
print(repr(m))
#> Model(positive_float=1.0)

try:
    Model(positive_float=-1.0)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'greater_than',
            'loc': ('positive_float',),
            'msg': 'Input should be greater than 0',
            'input': -1.0,
            'ctx': {'gt': 0.0},
            'url': 'https://errors.pydantic.dev/2/v/greater_than',
        }
    ]
    '''
```
"""
NegativeFloat = Annotated[float, annotated_types.Lt(0)]
"""A float that must be less than zero.

```python
from pydantic import BaseModel, NegativeFloat, ValidationError

class Model(BaseModel):
    negative_float: NegativeFloat

m = Model(negative_float=-1.0)
print(repr(m))
#> Model(negative_float=-1.0)

try:
    Model(negative_float=1.0)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'less_than',
            'loc': ('negative_float',),
            'msg': 'Input should be less than 0',
            'input': 1.0,
            'ctx': {'lt': 0.0},
            'url': 'https://errors.pydantic.dev/2/v/less_than',
        }
    ]
    '''
```
"""
NonPositiveFloat = Annotated[float, annotated_types.Le(0)]
"""A float that must be less than or equal to zero.

```python
from pydantic import BaseModel, NonPositiveFloat, ValidationError

class Model(BaseModel):
    non_positive_float: NonPositiveFloat

m = Model(non_positive_float=0.0)
print(repr(m))
#> Model(non_positive_float=0.0)

try:
    Model(non_positive_float=1.0)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'less_than_equal',
            'loc': ('non_positive_float',),
            'msg': 'Input should be less than or equal to 0',
            'input': 1.0,
            'ctx': {'le': 0.0},
            'url': 'https://errors.pydantic.dev/2/v/less_than_equal',
        }
    ]
    '''
```
"""
NonNegativeFloat = Annotated[float, annotated_types.Ge(0)]
"""A float that must be greater than or equal to zero.

```python
from pydantic import BaseModel, NonNegativeFloat, ValidationError

class Model(BaseModel):
    non_negative_float: NonNegativeFloat

m = Model(non_negative_float=0.0)
print(repr(m))
#> Model(non_negative_float=0.0)

try:
    Model(non_negative_float=-1.0)
except ValidationError as e:
    print(e.errors())
    '''
    [
        {
            'type': 'greater_than_equal',
            'loc': ('non_negative_float',),
            'msg': 'Input should be greater than or equal to 0',
            'input': -1.0,
            'ctx': {'ge': 0.0},
            'url': 'https://errors.pydantic.dev/2/v/greater_than_equal',
        }
    ]
    '''
```
"""
StrictFloat = Annotated[float, Strict(True)]
"""A float that must be validated in strict mode.

```python
from pydantic import BaseModel, StrictFloat, ValidationError

class StrictFloatModel(BaseModel):
    strict_float: StrictFloat

try:
    StrictFloatModel(strict_float='1.0')
except ValidationError as e:
    print(e)
    '''
    1 validation error for StrictFloatModel
    strict_float
