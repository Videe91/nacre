from __future__ import annotations

import abc
import collections.abc as cabc
import enum
import os
import stat
import sys
import typing as t
import uuid
from datetime import datetime
from gettext import gettext as _
from gettext import ngettext

from ._compat import _get_argv_encoding
from ._compat import open_stream
from .exceptions import BadParameter
from .utils import _LazyFile
from .utils import _safecall
from .utils import format_filename

if t.TYPE_CHECKING:
    import typing_extensions as te

    from .core import Context
    from .core import Parameter
    from .shell_completion import CompletionItem

_ValueT = t.TypeVar("_ValueT")
_ValueT_contra = t.TypeVar("_ValueT_contra", contravariant=True)
_ValueT_co = t.TypeVar("_ValueT_co", covariant=True)

# The input type parameter of ParamType defaults to Any. TypeVar defaults
# (PEP 696) landed in typing on Python 3.13, so type checkers get the
# default from typing_extensions, which is never imported at runtime. On
# older Pythons the runtime TypeVar carries no default;
# ParamType.__class_getitem__ fills in the omitted parameter instead.
if t.TYPE_CHECKING:
    _InputT_contra = te.TypeVar("_InputT_contra", contravariant=True, default=t.Any)
elif sys.version_info >= (3, 13):
    _InputT_contra = t.TypeVar("_InputT_contra", contravariant=True, default=t.Any)
else:
    _InputT_contra = t.TypeVar("_InputT_contra", contravariant=True)

_FloatValueT = t.TypeVar("_FloatValueT", bound=float)
_FloatValueT_co = t.TypeVar("_FloatValueT_co", bound=float, covariant=True)


class ParamTypeInfoDict(t.TypedDict):
    param_type: str
    name: str


class ParamType(t.Generic[_ValueT_co, _InputT_contra], abc.ABC):
    """Represents the type of a parameter. Validates and converts values
    from the command line or Python into the correct type.

    To implement a custom type, subclass and implement at least the
    following:

    -   The :attr:`name` class attribute must be set.
    -   Calling an instance of the type with ``None`` must return
        ``None``. This is already implemented by default.
    -   :meth:`convert` must convert string values to the correct type.
    -   :meth:`convert` must accept values that are already the correct
        type.
    -   It must be able to convert a value if the ``ctx`` and ``param``
        arguments are ``None``. This can occur when converting prompt
        input.

    .. versionchanged:: 8.4.0
        Now a generic abstract base class. Parameterize with the
        converted value type (``ParamType[int]`` for an integer-returning
        type) so that :meth:`convert` and downstream consumers carry the
        narrowed return type.

    .. versionchanged:: 8.5.0
        Accepts a second optional type parameter for the input value type
        that :meth:`convert` accepts (``ParamType[int, str]`` for a type
        converting strings to integers), defaulting to ``Any``.
    """

    is_composite: t.ClassVar[bool] = False
    arity: int = 1  # read-only

    #: the descriptive name of this type
    name: str

    #: if a list of this type is expected and the value is pulled from a
    #: string environment variable, this is what splits it up.  `None`
    #: means any whitespace.  For all parameters the general rule is that
    #: whitespace splits them up.  The exception are paths and files which
    #: are split by ``os.path.pathsep`` by default (":" on Unix and ";" on
    #: Windows).
    envvar_list_splitter: t.ClassVar[str | None] = None

    if sys.version_info < (3, 13):
        # ``_InputT_contra`` carries its ``Any`` default only for type
        # checkers: ``TypeVar(default=...)`` (PEP 696) is unavailable at
        # runtime before Python 3.13. Fill in the omitted input type
        # parameter by hand so ``ParamType[int]`` keeps working.
        def __class_getitem__(cls, params: t.Any) -> t.Any:
            if cls is ParamType:
                if not isinstance(params, tuple):
                    params = (params,)
                if len(params) == 1:
                    params = (*params, t.Any)
            # Checkers cannot see Generic.__class_getitem__ through super().
            return super().__class_getitem__(params)  # type: ignore[misc]

    def to_info_dict(self) -> ParamTypeInfoDict:
        """Gather information that could be useful for a tool generating
        user-facing documentation.

        Use :meth:`click.Context.to_info_dict` to traverse the entire
        CLI structure.

        .. versionadded:: 8.0
        """
        # The class name without the "ParamType" suffix.
        param_type = type(self).__name__.partition("ParamType")[0]
        param_type = param_type.partition("ParameterType")[0]

        # Custom subclasses might not remember to set a name.
        if hasattr(self, "name"):
            name = self.name
        else:
            name = param_type

        return {"param_type": param_type, "name": name}

    @t.overload
    def __call__(
        self,
        value: None,
        param: Parameter | None = None,
        ctx: Context | None = None,
    ) -> None: ...

    @t.overload
    def __call__(
        self,
        value: _InputT_contra,
        param: Parameter | None = None,
        ctx: Context | None = None,
    ) -> _ValueT_co: ...

    def __call__(
        self,
        value: _InputT_contra | None,
        param: Parameter | None = None,
        ctx: Context | None = None,
    ) -> _ValueT_co | None:
        if value is not None:
            return self.convert(value, param, ctx)
        return None

    def get_metavar(self, param: Parameter, ctx: Context) -> str | None:
        """Returns the metavar default for this param if it provides one."""

    def get_missing_message(self, param: Parameter, ctx: Context | None) -> str | None:
        """Optionally might return extra information about a missing
        parameter.

        .. versionadded:: 2.0
        """

    def convert(
        self, value: _InputT_contra, param: Parameter | None, ctx: Context | None
    ) -> _ValueT_co:
        """Convert the value to the correct type. This is not called if
        the value is ``None`` (the missing value).

        This must accept string values from the command line, as well as
        values that are already the correct type. It may also convert
        other compatible types.

        The ``param`` and ``ctx`` arguments may be ``None`` in certain
        situations, such as when converting prompt input.

        If the value cannot be converted, call :meth:`fail` with a
        descriptive message.

        :param value: The value to convert.
        :param param: The parameter that is using this type to convert
            its value. May be ``None``.
        :param ctx: The current context that arrived at this value. May
            be ``None``.
        """
        # The default returns the value as-is so subclasses that only customize
        # metadata are not forced to redeclare ``convert``.
        return t.cast("_ValueT_co", value)

    def split_envvar_value(self, rv: str) -> cabc.Sequence[str]:
        """Given a value from an environment variable this splits it up
        into small chunks depending on the defined envvar list splitter.

        If the splitter is set to `None`, which means that whitespace splits,
        then leading and trailing whitespace is ignored.  Otherwise, leading
        and trailing splitters usually lead to empty items being included.
        """
        return (rv or "").split(self.envvar_list_splitter)

    def fail(
        self,
        message: str,
        param: Parameter | None = None,
        ctx: Context | None = None,
    ) -> t.NoReturn:
        """Helper method to fail with an invalid value message."""
        raise BadParameter(message, ctx=ctx, param=param)

    def shell_complete(
        self, ctx: Context, param: Parameter, incomplete: str
    ) -> list[CompletionItem]:
        """Return a list of
        :class:`~click.shell_completion.CompletionItem` objects for the
        incomplete value. Most types do not provide completions, but
        some do, and this allows custom types to provide custom
        completions as well.

        :param ctx: Invocation context for this command.
        :param param: The parameter that is requesting completion.
        :param incomplete: Value being completed. May be empty.

        .. versionadded:: 8.0
        """
        return []


class CompositeParamType(ParamType[_ValueT_co]):
    is_composite: t.ClassVar[bool] = True

    @property
    @abc.abstractmethod
    def arity(self) -> int: ...  # type: ignore[override]


if t.TYPE_CHECKING:
    # on Python 3.10 this will raise a TypeError

    class FuncParamTypeInfoDict(
        ParamTypeInfoDict,
        t.Generic[_ValueT_contra, _ValueT_co],
    ):
        func: t.Callable[[_ValueT_contra], _ValueT_co]
else:

    class FuncParamTypeInfoDict(ParamTypeInfoDict):
        func: t.Callable[[t.Any], t.Any]


class FuncParamType(ParamType[_ValueT_co], t.Generic[_ValueT_contra, _ValueT_co]):
    name: str
    func: t.Callable[[_ValueT_contra], _ValueT_co]

    def __init__(self, func: t.Callable[[_ValueT_contra], _ValueT_co]) -> None:
        self.name = func.__name__
        self.func = func

    def to_info_dict(self) -> FuncParamTypeInfoDict[_ValueT_contra, _ValueT_co]:
        return {"func": self.func, **super().to_info_dict()}

    def convert(
        self, value: _ValueT_contra, param: Parameter | None, ctx: Context | None
    ) -> _ValueT_co:
        try:
            return self.func(value)
        except ValueError as exc:
            message = str(exc)

            if not message:
                try:
                    message = str(value)
                except UnicodeError:
                    message = t.cast("bytes", value).decode("utf-8", "replace")

            self.fail(message, param, ctx)


class UnprocessedParamType(ParamType[t.Any]):
    name = "text"

    def convert(
        self, value: _ValueT, param: Parameter | None, ctx: Context | None
    ) -> _ValueT:
        return value

    def __repr__(self) -> str:
        return "UNPROCESSED"


class StringParamType(ParamType[str]):
    name = "text"

    def convert(
        self, value: t.Any, param: Parameter | None, ctx: Context | None
    ) -> str:
        if isinstance(value, bytes):
            enc = _get_argv_encoding()
            try:
                return value.decode(enc)
            except UnicodeError:
                fs_enc = sys.getfilesystemencoding()
                if fs_enc != enc:
                    try:
                        return value.decode(fs_enc)
                    except UnicodeError:
                        return value.decode("utf-8", "replace")
                else:
                    return value.decode("utf-8", "replace")
        return str(value)

    def __repr__(self) -> str:
        return "STRING"


if t.TYPE_CHECKING:
    # on Python 3.10 this will raise a TypeError

    class ChoiceInfoDict(ParamTypeInfoDict, t.Generic[_ValueT_co]):
        choices: tuple[_ValueT_co, ...]
        case_sensitive: bool
else:

    class ChoiceInfoDict(ParamTypeInfoDict):
        choices: tuple[t.Any, ...]
        case_sensitive: bool


class Choice(ParamType[_ValueT_co], t.Generic[_ValueT_co]):
    """The choice type allows a value to be checked against a fixed set
    of supported values.

    You may pass any iterable value which will be converted to a tuple
    and thus will only be iterated once.

    The resulting value will always be one of the originally passed choices.
    See :meth:`normalize_choice` for more info on the mapping of strings
    to choices. See :ref:`choice-opts` for an example.

    :param case_sensitive: Set to false to make choices case
        insensitive. Defaults to true.

    .. versionchanged:: 8.4.0
        Now generic in the choice value type. Parameterize with the type of
        the choice values (``Choice[HashType]`` for an enum, ``Choice[str]``
        for plain strings) to enable type-checked consumers.

    .. versionchanged:: 8.2.0
        Non-``str`` ``choices`` are now supported. It can additionally be any
        iterable. Before you were not recommended to pass anything but a list or
        tuple.

    .. versionadded:: 8.2.0
        Choice normalization can be overridden via :meth:`normalize_choice`.
    """

    name: str = "choice"

    choices: tuple[_ValueT_co, ...]
    case_sensitive: bool

    def __init__(
        self, choices: cabc.Iterable[_ValueT_co], case_sensitive: bool = True
    ) -> None:
        self.choices = tuple(choices)
        self.case_sensitive = case_sensitive

    def to_info_dict(self) -> ChoiceInfoDict[_ValueT_co]:
        return {
            "choices": self.choices,
            "case_sensitive": self.case_sensitive,
            **super().to_info_dict(),
        }

    def _normalized_mapping(
        self, ctx: Context | None = None
    ) -> cabc.Mapping[_ValueT_co, str]:
        """
        Returns mapping where keys are the original choices and the values are
        the normalized values that are accepted via the command line.

        This is a simple wrapper around :meth:`normalize_choice`, use that
        instead which is supported.
        """
        return {
            choice: self.normalize_choice(
                choice=choice,
                ctx=ctx,
            )
            for choice in self.choices
        }

    def normalize_choice(self, choice: object, ctx: Context | None) -> str:
        """
        Normalize a choice value, used to map a passed string to a choice.
        Each choice must have a unique normalized value.

        By default uses :meth:`Context.token_normalize_func` and if not case
        sensitive, convert it to a casefolded value.

        .. versionadded:: 8.2.0
        """
        normed_value = choice.name if isinstance(choice, enum.Enum) else str(choice)

        if ctx is not None and ctx.token_normalize_func is not None:
            normed_value = ctx.token_normalize_func(normed_value)

        if not self.case_sensitive:
            normed_value = normed_value.casefold()

        return normed_value

    def get_metavar(self, param: Parameter, ctx: Context) -> str | None:
        if param.param_type_name == "option" and not param.show_choices:  # type: ignore[attr-defined]
            choice_metavars = [
                convert_type(type(choice)).name.upper() for choice in self.choices
            ]
            choices_str = "|".join([*dict.fromkeys(choice_metavars)])
        else:
            choices_str = "|".join(
                [str(i) for i in self._normalized_mapping(ctx=ctx).values()]
            )

        # Use curly braces to indicate a required argument.
        if param.required and param.param_type_name == "argument":
            return f"{{{choices_str}}}"

        # Use square braces to indicate an option or optional argument.
        return f"[{choices_str}]"

    def get_missing_message(self, param: Parameter, ctx: Context | None) -> str:
        """
        Message shown when no choice is passed.

        .. versionchanged:: 8.2.0 Added ``ctx`` argument.
        """
        return _("Choose from:\n\t{choices}").format(
            choices=",\n\t".join(self._normalized_mapping(ctx=ctx).values())
        )

    def convert(
        self, value: t.Any, param: Parameter | None, ctx: Context | None
    ) -> _ValueT_co:
        """
        For a given value from the parser, normalize it and find its
        matching normalized value in the list of choices. Then return the
        matched "original" choice.
        """
        normed_value = self.normalize_choice(choice=value, ctx=ctx)
        normalized_mapping = self._normalized_mapping(ctx=ctx)

        try:
            return next(
                original
                for original, normalized in normalized_mapping.items()
                if normalized == normed_value
            )
        except StopIteration:
            self.fail(
                self.get_invalid_choice_message(value=value, ctx=ctx),
                param=param,
                ctx=ctx,
            )

    def get_invalid_choice_message(self, value: t.Any, ctx: Context | None) -> str:
        """Get the error message when the given choice is invalid.

        :param value: The invalid value.

        .. versionadded:: 8.2
