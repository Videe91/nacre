"""
Virtual environment (venv) package for Python. Based on PEP 405.

Copyright (C) 2011-2014 Vinay Sajip.
Licensed to the PSF under a contributor agreement.
"""
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import types
import shlex


CORE_VENV_DEPS = ('pip',)
logger = logging.getLogger(__name__)


class EnvBuilder:
    """
    This class exists to allow virtual environment creation to be
    customized. The constructor parameters determine the builder's
    behaviour when called upon to create a virtual environment.

    By default, the builder makes the system (global) site-packages dir
    *un*available to the created environment.

    If invoked using the Python -m option, the default is to use copying
    on Windows platforms but symlinks elsewhere. If instantiated some
    other way, the default is to *not* use symlinks.

    :param system_site_packages: If True, the system (global) site-packages
                                 dir is available to created environments.
    :param clear: If True, delete the contents of the environment directory if
                  it already exists, before environment creation.
    :param symlinks: If True, attempt to symlink rather than copy files into
                     virtual environment.
    :param upgrade: If True, upgrade an existing virtual environment.
    :param with_pip: If True, ensure pip is installed in the virtual
                     environment
    :param prompt: Alternative terminal prefix for the environment.
    :param upgrade_deps: Update the base venv modules to the latest on PyPI
    :param scm_ignore_files: Create ignore files for the SCMs specified by the
                             iterable.
    """

    def __init__(self, system_site_packages=False, clear=False,
                 symlinks=False, upgrade=False, with_pip=False, prompt=None,
                 upgrade_deps=False, *, scm_ignore_files=frozenset()):
        self.system_site_packages = system_site_packages
        self.clear = clear
        self.symlinks = symlinks
        self.upgrade = upgrade
        self.with_pip = with_pip
        self.orig_prompt = prompt
        if prompt == '.':  # see bpo-38901
            prompt = os.path.basename(os.getcwd())
        self.prompt = prompt
        self.upgrade_deps = upgrade_deps
        self.scm_ignore_files = frozenset(map(str.lower, scm_ignore_files))

    def create(self, env_dir):
        """
        Create a virtual environment in a directory.

        :param env_dir: The target directory to create an environment in.

        """
        env_dir = os.path.abspath(env_dir)
        context = self.ensure_directories(env_dir)
        for scm in self.scm_ignore_files:
            getattr(self, f"create_{scm}_ignore_file")(context)
        # See issue 24875. We need system_site_packages to be False
        # until after pip is installed.
        true_system_site_packages = self.system_site_packages
        self.system_site_packages = False
        self.create_configuration(context)
        self.setup_python(context)
        if self.with_pip:
            self._setup_pip(context)
        if not self.upgrade:
            self.setup_scripts(context)
            self.post_setup(context)
        if true_system_site_packages:
            # We had set it to False before, now
            # restore it and rewrite the configuration
            self.system_site_packages = True
            self.create_configuration(context)
        if self.upgrade_deps:
            self.upgrade_dependencies(context)

    def clear_directory(self, path):
        for fn in os.listdir(path):
            fn = os.path.join(path, fn)
            if os.path.islink(fn) or os.path.isfile(fn):
                os.remove(fn)
            elif os.path.isdir(fn):
                shutil.rmtree(fn)

    def _venv_path(self, env_dir, name):
        vars = {
            'base': env_dir,
            'platbase': env_dir,
        }
        return sysconfig.get_path(name, scheme='venv', vars=vars)

    @classmethod
    def _same_path(cls, path1, path2):
        """Check whether two paths appear the same.

        Whether they refer to the same file is irrelevant; we're testing for
        whether a human reader would look at the path string and easily tell
        that they're the same file.
        """
        if sys.platform == 'win32':
            if os.path.normcase(path1) == os.path.normcase(path2):
                return True
            # gh-90329: Don't display a warning for short/long names
            import _winapi
            try:
                path1 = _winapi.GetLongPathName(os.fsdecode(path1))
            except OSError:
                pass
            try:
                path2 = _winapi.GetLongPathName(os.fsdecode(path2))
            except OSError:
                pass
            if os.path.normcase(path1) == os.path.normcase(path2):
                return True
            return False
        else:
            return path1 == path2

    def ensure_directories(self, env_dir):
        """
        Create the directories for the environment.

        Returns a context object which holds paths in the environment,
        for use by subsequent logic.
        """

        def create_if_needed(d):
            if not os.path.exists(d):
                os.makedirs(d)
            elif os.path.islink(d) or os.path.isfile(d):
                raise ValueError('Unable to create directory %r' % d)

        if os.pathsep in os.fspath(env_dir):
            raise ValueError(f'Refusing to create a venv in {env_dir} because '
                             f'it contains the PATH separator {os.pathsep}.')
        if os.path.exists(env_dir) and self.clear:
            self.clear_directory(env_dir)
        context = types.SimpleNamespace()
        context.env_dir = env_dir
        context.env_name = os.path.split(env_dir)[1]
        context.prompt = self.prompt if self.prompt is not None else context.env_name
        create_if_needed(env_dir)
        executable = sys._base_executable
        if not executable:  # see gh-96861
            raise ValueError('Unable to determine path to the running '
                             'Python interpreter. Provide an explicit path or '
                             'check that your PATH environment variable is '
                             'correctly set.')
        dirname, exename = os.path.split(os.path.abspath(executable))
        if sys.platform == 'win32':
            # Always create the simplest name in the venv. It will either be a
            # link back to executable, or a copy of the appropriate launcher
            _d = '_d' if os.path.splitext(exename)[0].endswith('_d') else ''
            exename = f'python{_d}.exe'
        context.executable = executable
        context.python_dir = dirname
        context.python_exe = exename
        binpath = self._venv_path(env_dir, 'scripts')
        libpath = self._venv_path(env_dir, 'purelib')

        # PEP 405 says venvs should create a local include directory.
        # See https://peps.python.org/pep-0405/#include-files
        # XXX: This directory is not exposed in sysconfig or anywhere else, and
        #      doesn't seem to be utilized by modern packaging tools. We keep it
        #      for backwards-compatibility, and to follow the PEP, but I would
        #      recommend against using it, as most tooling does not pass it to
        #      compilers. Instead, until we standardize a site-specific include
        #      directory, I would recommend installing headers as package data,
        #      and providing some sort of API to get the include directories.
        #      Example: https://numpy.org/doc/2.1/reference/generated/numpy.get_include.html
        incpath = os.path.join(env_dir, 'Include' if os.name == 'nt' else 'include')

        context.inc_path = incpath
        create_if_needed(incpath)
        context.lib_path = libpath
        create_if_needed(libpath)
        # Issue 21197: create lib64 as a symlink to lib on 64-bit non-OS X POSIX
        if ((sys.maxsize > 2**32) and (os.name == 'posix') and
            (sys.platform != 'darwin')):
            link_path = os.path.join(env_dir, 'lib64')
            if not os.path.exists(link_path):   # Issue #21643
                os.symlink('lib', link_path)
        context.bin_path = binpath
        context.bin_name = os.path.relpath(binpath, env_dir)
        context.env_exe = os.path.join(binpath, exename)
        create_if_needed(binpath)
        # Assign and update the command to use when launching the newly created
        # environment, in case it isn't simply the executable script (e.g. bpo-45337)
        context.env_exec_cmd = context.env_exe
        if sys.platform == 'win32':
            # bpo-45337: Fix up env_exec_cmd to account for file system redirections.
            # Some redirects only apply to CreateFile and not CreateProcess
            real_env_exe = os.path.realpath(context.env_exe)
            if not self._same_path(real_env_exe, context.env_exe):
                logger.warning('Actual environment location may have moved due to '
                               'redirects, links or junctions.\n'
                               '  Requested location: "%s"\n'
                               '  Actual location:    "%s"',
                               context.env_exe, real_env_exe)
                context.env_exec_cmd = real_env_exe
        return context

    def create_configuration(self, context):
        """
        Create a configuration file indicating where the environment's Python
        was copied from, and whether the system site-packages should be made
        available in the environment.

        :param context: The information for the environment creation request
                        being processed.
        """
        context.cfg_path = path = os.path.join(context.env_dir, 'pyvenv.cfg')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('home = %s\n' % context.python_dir)
            if self.system_site_packages:
                incl = 'true'
            else:
                incl = 'false'
            f.write('include-system-site-packages = %s\n' % incl)
            f.write('version = %d.%d.%d\n' % sys.version_info[:3])
            if self.prompt is not None:
                f.write(f'prompt = {self.prompt!r}\n')
            f.write('executable = %s\n' % os.path.realpath(sys.executable))
            args = []
            nt = os.name == 'nt'
            if nt and self.symlinks:
                args.append('--symlinks')
            if not nt and not self.symlinks:
                args.append('--copies')
            if not self.with_pip:
                args.append('--without-pip')
            if self.system_site_packages:
                args.append('--system-site-packages')
            if self.clear:
                args.append('--clear')
            if self.upgrade:
                args.append('--upgrade')
            if self.upgrade_deps:
                args.append('--upgrade-deps')
            if self.orig_prompt is not None:
                args.append(f'--prompt="{self.orig_prompt}"')
            if not self.scm_ignore_files:
                args.append('--without-scm-ignore-files')

            args.append(context.env_dir)
            args = ' '.join(args)
            f.write(f'command = {sys.executable} -m venv {args}\n')

    def symlink_or_copy(self, src, dst, relative_symlinks_ok=False):
        """
        Try symlinking a file, and if that fails, fall back to copying.
        (Unused on Windows, because we can't just copy a failed symlink file: we
        switch to a different set of files instead.)
        """
        assert os.name != 'nt'
        force_copy = not self.symlinks
        if not force_copy:
            try:
                if not os.path.islink(dst):  # can't link to itself!
                    if relative_symlinks_ok:
                        assert os.path.dirname(src) == os.path.dirname(dst)
                        os.symlink(os.path.basename(src), dst)
                    else:
                        os.symlink(src, dst)
            except Exception:   # may need to use a more specific exception
                logger.warning('Unable to symlink %r to %r', src, dst)
                force_copy = True
        if force_copy:
            shutil.copyfile(src, dst)

    def create_git_ignore_file(self, context):
        """
        Create a .gitignore file in the environment directory.

        The contents of the file cause the entire environment directory to be
        ignored by git.
        """
        gitignore_path = os.path.join(context.env_dir, '.gitignore')
        with open(gitignore_path, 'w', encoding='utf-8') as file:
            file.write('# Created by venv; '
                       'see https://docs.python.org/3/library/venv.html\n')
            file.write('*\n')

    if os.name != 'nt':
        def setup_python(self, context):
            """
            Set up a Python executable in the environment.

            :param context: The information for the environment creation request
                            being processed.
            """
            binpath = context.bin_path
            path = context.env_exe
            copier = self.symlink_or_copy
            dirname = context.python_dir
            copier(context.executable, path)
            if not os.path.islink(path):
                os.chmod(path, 0o755)

            suffixes = ['python', 'python3', f'python3.{sys.version_info[1]}']
            if sys.version_info[:2] == (3, 14) and sys.getfilesystemencoding() == 'utf-8':
                suffixes.append('𝜋thon')
            for suffix in suffixes:
                path = os.path.join(binpath, suffix)
                if not os.path.exists(path):
                    # Issue 18807: make copies if
                    # symlinks are not wanted
                    copier(context.env_exe, path, relative_symlinks_ok=True)
                    if not os.path.islink(path):
                        os.chmod(path, 0o755)

    else:
        def setup_python(self, context):
            """
            Set up a Python executable in the environment.

            :param context: The information for the environment creation request
                            being processed.
            """
            binpath = context.bin_path
            dirname = context.python_dir
            exename = os.path.basename(context.env_exe)
            exe_stem = os.path.splitext(exename)[0]
            exe_d = '_d' if os.path.normcase(exe_stem).endswith('_d') else ''
            if sysconfig.is_python_build():
                scripts = dirname
            else:
                scripts = os.path.join(os.path.dirname(__file__),
                                       'scripts', 'nt')
            if not sysconfig.get_config_var("Py_GIL_DISABLED"):
                python_exe = os.path.join(dirname, f'python{exe_d}.exe')
                pythonw_exe = os.path.join(dirname, f'pythonw{exe_d}.exe')
                link_sources = {
                    'python.exe': python_exe,
                    f'python{exe_d}.exe': python_exe,
                    'pythonw.exe': pythonw_exe,
                    f'pythonw{exe_d}.exe': pythonw_exe,
                }
                python_exe = os.path.join(scripts, f'venvlauncher{exe_d}.exe')
                pythonw_exe = os.path.join(scripts, f'venvwlauncher{exe_d}.exe')
                copy_sources = {
                    'python.exe': python_exe,
                    f'python{exe_d}.exe': python_exe,
                    'pythonw.exe': pythonw_exe,
                    f'pythonw{exe_d}.exe': pythonw_exe,
                }
            else:
                exe_t = f'3.{sys.version_info[1]}t'
                python_exe = os.path.join(dirname, f'python{exe_t}{exe_d}.exe')
                pythonw_exe = os.path.join(dirname, f'pythonw{exe_t}{exe_d}.exe')
                link_sources = {
                    'python.exe': python_exe,
                    f'python{exe_d}.exe': python_exe,
                    f'python{exe_t}.exe': python_exe,
                    f'python{exe_t}{exe_d}.exe': python_exe,
                    'pythonw.exe': pythonw_exe,
                    f'pythonw{exe_d}.exe': pythonw_exe,
                    f'pythonw{exe_t}.exe': pythonw_exe,
