from __future__ import annotations

import functools
import logging
import os
import shutil
import sys
import uuid
import zipfile
from collections.abc import Collection, Iterable
from optparse import Values
from pathlib import Path
from typing import Any

from pip._vendor.packaging.markers import Marker
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name
from pip._vendor.packaging.version import Version
from pip._vendor.packaging.version import parse as parse_version
from pip._vendor.pyproject_hooks import BuildBackendHookCaller

from pip._internal.build_env import BuildEnvironment, NoOpBuildEnvironment
from pip._internal.exceptions import InstallationError, PreviousBuildDirError
from pip._internal.locations import get_scheme
from pip._internal.metadata import (
    BaseDistribution,
    get_default_environment,
    get_directory_distribution,
    get_wheel_distribution,
)
from pip._internal.metadata.base import FilesystemWheel
from pip._internal.models.direct_url import DirectUrl
from pip._internal.models.link import Link
from pip._internal.operations.build.metadata import generate_metadata
from pip._internal.operations.build.metadata_editable import generate_editable_metadata
from pip._internal.operations.install.wheel import install_wheel
from pip._internal.pyproject import load_pyproject_toml, make_pyproject_path
from pip._internal.req.req_uninstall import UninstallPathSet
from pip._internal.utils.deprecation import deprecated
from pip._internal.utils.hashes import Hashes
from pip._internal.utils.misc import (
    ConfiguredBuildBackendHookCaller,
    ask_path_exists,
    backup_dir,
    display_path,
    hide_url,
    is_installable_dir,
    redact_auth_from_requirement,
    redact_auth_from_url,
)
from pip._internal.utils.packaging import get_requirement
from pip._internal.utils.subprocess import runner_with_spinner_message
from pip._internal.utils.temp_dir import TempDirectory, tempdir_kinds
from pip._internal.utils.unpacking import unpack_file
from pip._internal.utils.virtualenv import running_under_virtualenv
from pip._internal.vcs import vcs

logger = logging.getLogger(__name__)


class InstallRequirement:
    """
    Represents something that may be installed later on, may have information
    about where to fetch the relevant requirement and also contains logic for
    installing the said requirement.
    """

    def __init__(
        self,
        req: Requirement | None,
        comes_from: str | InstallRequirement | None,
        editable: bool = False,
        link: Link | None = None,
        markers: Marker | None = None,
        isolated: bool = False,
        *,
        hash_options: dict[str, list[str]] | None = None,
        config_settings: dict[str, str | list[str]] | None = None,
        constraint: bool = False,
        extras: Collection[str] = (),
        user_supplied: bool = False,
        permit_editable_wheels: bool = False,
    ) -> None:
        assert req is None or isinstance(req, Requirement), req
        self.req = req
        self.comes_from = comes_from
        self.constraint = constraint
        self.editable = editable
        self.permit_editable_wheels = permit_editable_wheels

        # source_dir is the local directory where the linked requirement is
        # located, or unpacked. In case unpacking is needed, creating and
        # populating source_dir is done by the RequirementPreparer. Note this
        # is not necessarily the directory where pyproject.toml or setup.py is
        # located - that one is obtained via unpacked_source_directory.
        self.source_dir: str | None = None
        if self.editable:
            assert link
            if link.is_file:
                self.source_dir = os.path.normpath(os.path.abspath(link.file_path))

        # original_link is the direct URL that was provided by the user for the
        # requirement, either directly or via a constraints file.
        if link is None and req and req.url:
            # PEP 508 URL requirement
            link = Link(req.url)
        self.link = self.original_link = link

        # When this InstallRequirement is a wheel obtained from the cache of locally
        # built wheels, this is the source link corresponding to the cache entry, which
        # was used to download and build the cached wheel.
        self.cached_wheel_source_link: Link | None = None

        # Information about the location of the artifact that was downloaded . This
        # property is guaranteed to be set in resolver results.
        self.download_info: DirectUrl | None = None

        # Path to any downloaded or already-existing package.
        self.local_file_path: str | None = None
        if self.link and self.link.is_file:
            self.local_file_path = self.link.file_path

        if extras:
            self.extras = extras
        elif req:
            self.extras = req.extras
        else:
            self.extras = set()
        if markers is None and req:
            markers = req.marker
        self.markers = markers

        # This holds the Distribution object if this requirement is already installed.
        self.satisfied_by: BaseDistribution | None = None
        # Whether the installation process should try to uninstall an existing
        # distribution before installing this requirement.
        self.should_reinstall = False
        # Temporary build location
        self._temp_build_dir: TempDirectory | None = None
        # Set to True after successful installation
        self.install_succeeded: bool | None = None
        # Supplied options
        self.hash_options = hash_options if hash_options else {}
        self.config_settings = config_settings
        # Set to True after successful preparation of this requirement
        self.prepared = False
        # User supplied requirement are explicitly requested for installation
        # by the user via CLI arguments or requirements files, as opposed to,
        # e.g. dependencies, extras or constraints.
        self.user_supplied = user_supplied

        self.isolated = isolated
        self.build_env: BuildEnvironment = NoOpBuildEnvironment()

        # For PEP 517, the directory where we request the project metadata
        # gets stored. We need this to pass to build_wheel, so the backend
        # can ensure that the wheel matches the metadata (see the PEP for
        # details).
        self.metadata_directory: str | None = None

        # The cached metadata distribution that this requirement represents.
        # See get_dist / set_dist.
        self._distribution: BaseDistribution | None = None

        # The static build requirements (from pyproject.toml)
        self.pyproject_requires: list[str] | None = None

        # Build requirements that we will check are available
        self.requirements_to_check: list[str] = []

        # The PEP 517 backend we should use to build the project
        self.pep517_backend: BuildBackendHookCaller | None = None

        # This requirement needs more preparation before it can be built
        self.needs_more_preparation = False

        # This requirement needs to be unpacked before it can be installed.
        self._archive_source: Path | None = None

    def __str__(self) -> str:
        if self.req:
            s = redact_auth_from_requirement(self.req)
            if self.link:
                s += f" from {redact_auth_from_url(self.link.url)}"
        elif self.link:
            s = redact_auth_from_url(self.link.url)
        else:
            s = "<InstallRequirement>"
        if self.satisfied_by is not None:
            if self.satisfied_by.location is not None:
                location = display_path(self.satisfied_by.location)
            else:
                location = "<memory>"
            s += f" in {location}"
        if self.comes_from:
            if isinstance(self.comes_from, str):
                comes_from: str | None = self.comes_from
            else:
                comes_from = self.comes_from.from_path()
            if comes_from:
                s += f" (from {comes_from})"
        return s

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} object: "
            f"{str(self)} editable={self.editable!r}>"
        )

    def format_debug(self) -> str:
        """An un-tested helper for getting state, for debugging."""
        attributes = vars(self)
        names = sorted(attributes)

        state = (f"{attr}={attributes[attr]!r}" for attr in sorted(names))
        return "<{name} object: {{{state}}}>".format(
            name=self.__class__.__name__,
            state=", ".join(state),
        )

    # Things that are valid for all kinds of requirements?
    @property
    def name(self) -> str | None:
        if self.req is None:
            return None
        return self.req.name

    @functools.cached_property
    def supports_pyproject_editable(self) -> bool:
        assert self.pep517_backend
        with self.build_env:
            runner = runner_with_spinner_message(
                "Checking if build backend supports build_editable"
            )
            with self.pep517_backend.subprocess_runner(runner):
                return "build_editable" in self.pep517_backend._supported_features()

    @property
    def specifier(self) -> SpecifierSet:
        assert self.req is not None
        return self.req.specifier

    @property
    def is_direct(self) -> bool:
        """Whether this requirement was specified as a direct URL."""
        return self.original_link is not None

    @property
    def is_pinned(self) -> bool:
        """Return whether I am pinned to an exact version.

        For example, some-package==1.2 is pinned; some-package>1.2 is not.
        """
        assert self.req is not None
        specifiers = self.req.specifier
        return len(specifiers) == 1 and next(iter(specifiers)).operator in {"==", "==="}

    def match_markers(self, extras_requested: Iterable[str] | None = None) -> bool:
        if not extras_requested:
            # Provide an extra to safely evaluate the markers
            # without matching any extra
            extras_requested = ("",)
        if self.markers is not None:
            return any(
                self.markers.evaluate({"extra": extra}) for extra in extras_requested
            )
        else:
            return True

    @property
    def has_hash_options(self) -> bool:
        """Return whether any known-good hashes are specified as options.

        These activate --require-hashes mode; hashes specified as part of a
        URL do not.

        """
        return bool(self.hash_options)

    def hashes(self, trust_internet: bool = True) -> Hashes:
        """Return a hash-comparer that considers my option- and URL-based
        hashes to be known-good.

        Hashes in URLs--ones embedded in the requirements file, not ones
        downloaded from an index server--are almost peers with ones from
        flags. They satisfy --require-hashes (whether it was implicitly or
        explicitly activated) but do not activate it. md5 and sha224 are not
        allowed in flags, which should nudge people toward good algos. We
        always OR all hashes together, even ones from URLs.

        :param trust_internet: Whether to trust URL-based (#md5=...) hashes
            downloaded from the internet, as by populate_link()

        """
        good_hashes = self.hash_options.copy()
        if trust_internet:
            link = self.link
        elif self.is_direct and self.user_supplied:
            link = self.original_link
        else:
            link = None
        if link and link.hash:
            assert link.hash_name is not None
            good_hashes.setdefault(link.hash_name, []).append(link.hash)
        return Hashes(good_hashes)

    def from_path(self) -> str | None:
        """Format a nice indicator to show where this "comes from" """
        if self.req is None:
            return None
        s = str(self.req)
        if self.comes_from:
            comes_from: str | None
            if isinstance(self.comes_from, str):
                comes_from = self.comes_from
            else:
                comes_from = self.comes_from.from_path()
            if comes_from:
                s += "->" + comes_from
        return s

    def ensure_build_location(
        self, build_dir: str, autodelete: bool, parallel_builds: bool
    ) -> str:
        assert build_dir is not None
        if self._temp_build_dir is not None:
            assert self._temp_build_dir.path
            return self._temp_build_dir.path
        if self.req is None:
            # Some systems have /tmp as a symlink which confuses custom
            # builds (such as numpy). Thus, we ensure that the real path
            # is returned.
            self._temp_build_dir = TempDirectory(
                kind=tempdir_kinds.REQ_BUILD, globally_managed=True
            )

            return self._temp_build_dir.path

        # This is the only remaining place where we manually determine the path
        # for the temporary directory. It is only needed for editables where
        # it is the value of the --src option.

        # When parallel builds are enabled, add a UUID to the build directory
        # name so multiple builds do not interfere with each other.
        dir_name: str = canonicalize_name(self.req.name)
        if parallel_builds:
            dir_name = f"{dir_name}_{uuid.uuid4().hex}"

        # FIXME: Is there a better place to create the build_dir? (hg and bzr
        # need this)
        if not os.path.exists(build_dir):
            logger.debug("Creating directory %s", build_dir)
            os.makedirs(build_dir)
        actual_build_dir = os.path.join(build_dir, dir_name)
        # `None` indicates that we respect the globally-configured deletion
        # settings, which is what we actually want when auto-deleting.
        delete_arg = None if autodelete else False
        return TempDirectory(
            path=actual_build_dir,
            delete=delete_arg,
            kind=tempdir_kinds.REQ_BUILD,
            globally_managed=True,
        ).path

    def _set_requirement(self) -> None:
        """Set requirement after generating metadata."""
        assert self.req is None
        assert self.metadata is not None
        assert self.source_dir is not None

        # Construct a Requirement object from the generated metadata
        if isinstance(parse_version(self.metadata["Version"]), Version):
            op = "=="
        else:
            op = "==="

        self.req = get_requirement(
            "".join(
                [
                    self.metadata["Name"],
                    op,
                    self.metadata["Version"],
                ]
            )
        )

    def warn_on_mismatching_name(self) -> None:
        assert self.req is not None
        metadata_name = canonicalize_name(self.metadata["Name"])
        if canonicalize_name(self.req.name) == metadata_name:
            # Everything is fine.
            return

        # If we're here, there's a mismatch. Log a warning about it.
        logger.warning(
            "Generating metadata for package %s "
            "produced metadata for project name %s. Fix your "
            "#egg=%s fragments.",
            self.name,
            metadata_name,
            self.name,
        )
        self.req = get_requirement(metadata_name)

    def check_if_exists(self, use_user_site: bool) -> None:
        """Find an installed distribution that satisfies or conflicts
        with this requirement, and set self.satisfied_by or
        self.should_reinstall appropriately.
        """
        if self.req is None:
            return
        existing_dist = get_default_environment().get_distribution(self.req.name)
        if not existing_dist:
            return

        version_compatible = self.req.specifier.contains(
            existing_dist.version,
            prereleases=True,
        )
        if not version_compatible:
            self.satisfied_by = None
            if use_user_site:
                if existing_dist.in_usersite:
                    self.should_reinstall = True
