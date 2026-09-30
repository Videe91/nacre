"""Access to Python's configuration information."""

import os
import sys
import threading
from os.path import realpath

__all__ = [
    'get_config_h_filename',
    'get_config_var',
    'get_config_vars',
    'get_makefile_filename',
    'get_path',
    'get_path_names',
    'get_paths',
    'get_platform',
    'get_python_version',
    'get_scheme_names',
    'parse_config_h',
]

# Keys for get_config_var() that are never converted to Python integers.
_ALWAYS_STR = {
    'IPHONEOS_DEPLOYMENT_TARGET',
    'MACOSX_DEPLOYMENT_TARGET',
}

_INSTALL_SCHEMES = {
    'posix_prefix': {
        'stdlib': '{installed_base}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
        'platstdlib': '{platbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
        'purelib': '{base}/lib/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
        'platlib': '{platbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
        'include':
            '{installed_base}/include/{implementation_lower}{py_version_short}{abiflags}',
        'platinclude':
            '{installed_platbase}/include/{implementation_lower}{py_version_short}{abiflags}',
        'scripts': '{base}/bin',
        'data': '{base}',
        },
    'posix_home': {
        'stdlib': '{installed_base}/lib/{implementation_lower}',
        'platstdlib': '{base}/lib/{implementation_lower}',
        'purelib': '{base}/lib/{implementation_lower}',
        'platlib': '{base}/lib/{implementation_lower}',
        'include': '{installed_base}/include/{implementation_lower}',
        'platinclude': '{installed_base}/include/{implementation_lower}',
        'scripts': '{base}/bin',
        'data': '{base}',
        },
    'nt': {
        'stdlib': '{installed_base}/Lib',
        'platstdlib': '{base}/Lib',
        'purelib': '{base}/Lib/site-packages',
        'platlib': '{base}/Lib/site-packages',
        'include': '{installed_base}/Include',
        'platinclude': '{installed_base}/Include',
        'scripts': '{base}/Scripts',
        'data': '{base}',
        },

    # Downstream distributors can overwrite the default install scheme.
    # This is done to support downstream modifications where distributors change
    # the installation layout (eg. different site-packages directory).
    # So, distributors will change the default scheme to one that correctly
    # represents their layout.
    # This presents an issue for projects/people that need to bootstrap virtual
    # environments, like virtualenv. As distributors might now be customizing
    # the default install scheme, there is no guarantee that the information
    # returned by sysconfig.get_default_scheme/get_paths is correct for
    # a virtual environment, the only guarantee we have is that it is correct
    # for the *current* environment. When bootstrapping a virtual environment,
    # we need to know its layout, so that we can place the files in the
    # correct locations.
    # The "*_venv" install scheme is a scheme to bootstrap virtual environments,
    # essentially identical to the default posix_prefix/nt schemes.
    # Downstream distributors who patch posix_prefix/nt scheme are encouraged to
    # leave the following schemes unchanged
    'posix_venv': {
        'stdlib': '{installed_base}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
        'platstdlib': '{platbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
        'purelib': '{base}/lib/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
        'platlib': '{platbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
        'include':
            '{installed_base}/include/{implementation_lower}{py_version_short}{abiflags}',
        'platinclude':
            '{installed_platbase}/include/{implementation_lower}{py_version_short}{abiflags}',
        'scripts': '{base}/bin',
        'data': '{base}',
        },
    'nt_venv': {
        'stdlib': '{installed_base}/Lib',
        'platstdlib': '{base}/Lib',
        'purelib': '{base}/Lib/site-packages',
        'platlib': '{base}/Lib/site-packages',
        'include': '{installed_base}/Include',
        'platinclude': '{installed_base}/Include',
        'scripts': '{base}/Scripts',
        'data': '{base}',
        },
    'osx_framework_library': {
        'stdlib': '{installed_base}/{platlibdir}/python{py_version_short}{abi_thread}',
        'platstdlib': '{platbase}/{platlibdir}/python{py_version_short}{abi_thread}',
        'purelib': '/opt/homebrew/lib/python{py_version_short}{abi_thread}/site-packages',
        'platlib': '/opt/homebrew/{platlibdir}/python{py_version_short}{abi_thread}/site-packages',
        'include': '{installed_base}/include/python{py_version_short}{abiflags}',
        'platinclude': '{installed_platbase}/include/python{py_version_short}{abiflags}',
        'scripts': '/opt/homebrew/bin',
        'data': '/opt/homebrew',
        },
    }

# For the OS-native venv scheme, we essentially provide an alias:
if os.name == 'nt':
    _INSTALL_SCHEMES['venv'] = _INSTALL_SCHEMES['nt_venv']
else:
    _INSTALL_SCHEMES['venv'] = _INSTALL_SCHEMES['posix_venv']

def _get_implementation():
    return 'Python'

# NOTE: site.py has copy of this function.
# Sync it when modify this function.
def _getuserbase():
    env_base = os.environ.get("PYTHONUSERBASE", None)
    if env_base:
        return env_base

    # Emscripten, iOS, tvOS, VxWorks, WASI, and watchOS have no home directories.
    # Use _PYTHON_HOST_PLATFORM to get the correct platform when cross-compiling.
    system_name = os.environ.get('_PYTHON_HOST_PLATFORM', sys.platform).split('-')[0]
    if system_name in {"emscripten", "ios", "tvos", "vxworks", "wasi", "watchos"}:
        return None

    def joinuser(*args):
        return os.path.expanduser(os.path.join(*args))

    if os.name == "nt":
        base = os.environ.get("APPDATA") or "~"
        return joinuser(base,  _get_implementation())

    if sys.platform == "darwin" and sys._framework:
        return joinuser("~", "Library", sys._framework,
                        f"{sys.version_info[0]}.{sys.version_info[1]}")

    return joinuser("~", ".local")

_HAS_USER_BASE = (_getuserbase() is not None)

if _HAS_USER_BASE:
    _INSTALL_SCHEMES |= {
        # NOTE: When modifying "purelib" scheme, update site._get_path() too.
        'nt_user': {
            'stdlib': '{userbase}/{implementation}{py_version_nodot_plat}',
            'platstdlib': '{userbase}/{implementation}{py_version_nodot_plat}',
            'purelib': '{userbase}/{implementation}{py_version_nodot_plat}/site-packages',
            'platlib': '{userbase}/{implementation}{py_version_nodot_plat}/site-packages',
            'include': '{userbase}/{implementation}{py_version_nodot_plat}/Include',
            'scripts': '{userbase}/{implementation}{py_version_nodot_plat}/Scripts',
            'data': '{userbase}',
            },
        'posix_user': {
            'stdlib': '{userbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
            'platstdlib': '{userbase}/{platlibdir}/{implementation_lower}{py_version_short}{abi_thread}',
            'purelib': '{userbase}/lib/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
            'platlib': '{userbase}/lib/{implementation_lower}{py_version_short}{abi_thread}/site-packages',
            'include': '{userbase}/include/{implementation_lower}{py_version_short}{abi_thread}',
            'scripts': '{userbase}/bin',
            'data': '{userbase}',
            },
        'osx_framework_user': {
            'stdlib': '{userbase}/lib/{implementation_lower}',
            'platstdlib': '{userbase}/lib/{implementation_lower}',
            'purelib': '{userbase}/lib/{implementation_lower}/site-packages',
            'platlib': '{userbase}/lib/{implementation_lower}/site-packages',
            'include': '{userbase}/include/{implementation_lower}{py_version_short}',
            'scripts': '{userbase}/bin',
            'data': '{userbase}',
            },
    }

_SCHEME_KEYS = ('stdlib', 'platstdlib', 'purelib', 'platlib', 'include',
                'scripts', 'data')

_PY_VERSION = sys.version.split()[0]
_PY_VERSION_SHORT = f'{sys.version_info[0]}.{sys.version_info[1]}'
_PY_VERSION_SHORT_NO_DOT = f'{sys.version_info[0]}{sys.version_info[1]}'
_BASE_PREFIX = os.path.normpath(sys.base_prefix)
_BASE_EXEC_PREFIX = os.path.normpath(sys.base_exec_prefix)
# Mutex guarding initialization of _CONFIG_VARS.
_CONFIG_VARS_LOCK = threading.RLock()
_CONFIG_VARS = None
# True iff _CONFIG_VARS has been fully initialized.
_CONFIG_VARS_INITIALIZED = False
_USER_BASE = None


def _safe_realpath(path):
    try:
        return realpath(path)
    except OSError:
        return path

if sys.executable:
    _PROJECT_BASE = os.path.dirname(_safe_realpath(sys.executable))
else:
    # sys.executable can be empty if argv[0] has been changed and Python is
    # unable to retrieve the real program name
    _PROJECT_BASE = _safe_realpath(os.getcwd())

# In a virtual environment, `sys._home` gives us the target directory
# `_PROJECT_BASE` for the executable that created it when the virtual
# python is an actual executable ('venv --copies' or Windows).
_sys_home = getattr(sys, '_home', None)
if _sys_home:
    _PROJECT_BASE = _sys_home

if os.name == 'nt':
    # In a source build, the executable is in a subdirectory of the root
    # that we want (<root>\PCbuild\<platname>).
    # `_BASE_PREFIX` is used as the base installation is where the source
    # will be.  The realpath is needed to prevent mount point confusion
    # that can occur with just string comparisons.
    if _safe_realpath(_PROJECT_BASE).startswith(
            _safe_realpath(f'{_BASE_PREFIX}\\PCbuild')):
        _PROJECT_BASE = _BASE_PREFIX

# set for cross builds
if "_PYTHON_PROJECT_BASE" in os.environ:
    _PROJECT_BASE = _safe_realpath(os.environ["_PYTHON_PROJECT_BASE"])

def is_python_build(check_home=None):
    if check_home is not None:
        import warnings
        warnings.warn(
            (
                'The check_home argument of sysconfig.is_python_build is '
                'deprecated and its value is ignored. '
                'It will be removed in Python 3.15.'
            ),
            DeprecationWarning,
            stacklevel=2,
        )
    for fn in ("Setup", "Setup.local"):
        if os.path.isfile(os.path.join(_PROJECT_BASE, "Modules", fn)):
            return True
    return False

_PYTHON_BUILD = is_python_build()

if _PYTHON_BUILD:
    for scheme in ('posix_prefix', 'posix_home'):
        # On POSIX-y platforms, Python will:
        # - Build from .h files in 'headers' (which is only added to the
        #   scheme when building CPython)
        # - Install .h files to 'include'
        scheme = _INSTALL_SCHEMES[scheme]
        scheme['headers'] = scheme['include']
        scheme['include'] = '{srcdir}/Include'
        scheme['platinclude'] = '{projectbase}/.'
    del scheme


def _subst_vars(s, local_vars):
    try:
        return s.format(**local_vars)
    except KeyError as var:
        try:
            return s.format(**os.environ)
        except KeyError:
            raise AttributeError(f'{var}') from None

def _extend_dict(target_dict, other_dict):
    target_keys = target_dict.keys()
    for key, value in other_dict.items():
        if key in target_keys:
            continue
        target_dict[key] = value


def _expand_vars(scheme, vars):
    res = {}
    if vars is None:
        vars = {}
    _extend_dict(vars, get_config_vars())
    if os.name == 'nt':
        # On Windows we want to substitute 'lib' for schemes rather
        # than the native value (without modifying vars, in case it
        # was passed in)
        vars = vars | {'platlibdir': 'lib'}

    for key, value in _INSTALL_SCHEMES[scheme].items():
        if os.name in ('posix', 'nt'):
            value = os.path.expanduser(value)
        res[key] = os.path.normpath(_subst_vars(value, vars))
    return res


def _get_preferred_schemes():
    if os.name == 'nt':
        return {
            'prefix': 'nt',
            'home': 'posix_home',
            'user': 'nt_user',
        }
    if sys.platform == 'darwin' and sys._framework:
        return {
            'prefix': 'osx_framework_library',
            'home': 'posix_home',
            'user': 'osx_framework_user',
        }

    return {
        'prefix': 'osx_framework_library',
        'home': 'posix_home',
        'user': 'posix_user',
    }


def get_preferred_scheme(key):
    if key == 'prefix' and (sys.prefix != sys.base_prefix or os.environ.get("ENSUREPIP_OPTIONS", None)):
        return 'venv'
    scheme = _get_preferred_schemes()[key]
    if scheme not in _INSTALL_SCHEMES:
        raise ValueError(
            f"{key!r} returned {scheme!r}, which is not a valid scheme "
            f"on this platform"
        )
    return scheme


def get_default_scheme():
    return get_preferred_scheme('prefix')


def get_makefile_filename():
    """Return the path of the Makefile."""

    # GH-127429: When cross-compiling, use the Makefile from the target, instead of the host Python.
    if cross_base := os.environ.get('_PYTHON_PROJECT_BASE'):
        return os.path.join(cross_base, 'Makefile')

    if _PYTHON_BUILD:
        return os.path.join(_PROJECT_BASE, "Makefile")

    if hasattr(sys, 'abiflags'):
        config_dir_name = f'config-{_PY_VERSION_SHORT}{sys.abiflags}'
    else:
        config_dir_name = 'config'

    if hasattr(sys.implementation, '_multiarch'):
        config_dir_name += f'-{sys.implementation._multiarch}'

    return os.path.join(get_path('stdlib'), config_dir_name, 'Makefile')


def _import_from_directory(path, name):
    if name not in sys.modules:
        import importlib.machinery
        import importlib.util

        spec = importlib.machinery.PathFinder.find_spec(name, [path])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


def _get_sysconfigdata_name():
    multiarch = getattr(sys.implementation, '_multiarch', '')
    return os.environ.get(
        '_PYTHON_SYSCONFIGDATA_NAME',
        f'_sysconfigdata_{sys.abiflags}_{sys.platform}_{multiarch}',
    )


def _get_sysconfigdata():
    import importlib

    name = _get_sysconfigdata_name()
    path = os.environ.get('_PYTHON_SYSCONFIGDATA_PATH')
    module = _import_from_directory(path, name) if path else importlib.import_module(name)

    return module.build_time_vars


def _installation_is_relocated():
    """Is the Python installation running from a different prefix than what was targetted when building?"""
    if os.name != 'posix':
        raise NotImplementedError('sysconfig._installation_is_relocated() is currently only supported on POSIX')

    data = _get_sysconfigdata()
    return (
        data['prefix'] != getattr(sys, 'base_prefix', '')
        or data['exec_prefix'] != getattr(sys, 'base_exec_prefix', '')
    )


def _init_posix(vars):
    """Initialize the module as appropriate for POSIX systems."""
    # GH-126920: Make sure we don't overwrite any of the keys already set
    vars.update(_get_sysconfigdata() | vars)


def _init_non_posix(vars):
    """Initialize the module as appropriate for NT"""
    # set basic install directories
    import _winapi
    import _sysconfig
    vars['LIBDEST'] = get_path('stdlib')
    vars['BINLIBDEST'] = get_path('platstdlib')
    vars['INCLUDEPY'] = get_path('include')

    # Add EXT_SUFFIX, SOABI, Py_DEBUG, and Py_GIL_DISABLED
    vars.update(_sysconfig.config_vars())

    # NOTE: ABIFLAGS is only an emulated value. It is not present during build
    #       on Windows. sys.abiflags is absent on Windows and vars['abiflags']
    #       is already widely used to calculate paths, so it should remain an
    #       empty string.
    vars['ABIFLAGS'] = ''.join(
        (
            't' if vars['Py_GIL_DISABLED'] else '',
            '_d' if vars['Py_DEBUG'] else '',
        ),
    )

    vars['LIBDIR'] = _safe_realpath(os.path.join(get_config_var('installed_base'), 'libs'))
    if hasattr(sys, 'dllhandle'):
        dllhandle = _winapi.GetModuleFileName(sys.dllhandle)
