# Copyright 2025-present, the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contains the 'hf cache' command group with cache management subcommands."""

import re
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any

import click

from huggingface_hub.errors import CLIError

from ..utils import (
    ANSI,
    CachedFileInfo,
    CachedRepoInfo,
    CachedRevisionInfo,
    CacheNotFound,
    HFCacheInfo,
    HfUri,
    _format_size,
    _shared_blobs,
    parse_hf_uri,
    scan_cache_dir,
)
from ..utils._parsing import parse_duration, parse_size
from ._cli_utils import RepoIdArg, RepoTypeOpt, RevisionOpt, TokenOpt, get_hf_api, typer_factory
from ._framework import Argument, Option
from ._output import out


cache_cli = typer_factory(help="Manage local cache directory.")


#### Cache helper utilities


@dataclass(frozen=True)
class _DeletionResolution:
    revisions: frozenset[str]
    selected: dict[CachedRepoInfo, frozenset[CachedRevisionInfo]]
    missing: tuple[str, ...]
    files: dict[CachedFileInfo, str] = field(default_factory=dict)  # selected files, mapped to their display label


_FILTER_PATTERN = re.compile(r"(?P<key>[a-zA-Z_]+)\s*(?P<op>==|!=|>=|<=|>|<|=)\s*(?P<value>.+)")
_ALLOWED_OPERATORS = {"=", "!=", ">", "<", ">=", "<="}
_FILTER_KEYS = {"accessed", "modified", "refs", "size", "type"}
_SORT_KEYS = {"accessed", "modified", "name", "size"}
_SORT_PATTERN = re.compile(r"(?P<key>[a-zA-Z_]+)(?::(?P<order>asc|desc))?")
_SORT_DEFAULT_ORDER = {
    # Default ordering: accessed/modified/size are descending (newest/biggest first), name is ascending
    "accessed": "desc",
    "modified": "desc",
    "size": "desc",
    "name": "asc",
}


# Dynamically generate SortOptions enum from _SORT_KEYS
_sort_options_dict = {}
for key in sorted(_SORT_KEYS):
    _sort_options_dict[key] = key
    _sort_options_dict[f"{key}_asc"] = f"{key}:asc"
    _sort_options_dict[f"{key}_desc"] = f"{key}:desc"

SortOptions = Enum("SortOptions", _sort_options_dict, type=str, module=__name__)  # type: ignore


@dataclass(frozen=True)
class CacheDeletionCounts:
    """Simple counters summarizing cache deletions for CLI messaging."""

    repo_count: int
    partial_revision_count: int
    total_revision_count: int


CacheEntry = tuple[CachedRepoInfo, CachedRevisionInfo | None]
RepoRefsMap = dict[CachedRepoInfo, frozenset[str]]


def summarize_deletions(
    selected_by_repo: Mapping[CachedRepoInfo, frozenset[CachedRevisionInfo]],
) -> CacheDeletionCounts:
    """Summarize deletions across repositories."""
    repo_count = 0
    total_revisions = 0
    revisions_in_full_repos = 0

    for repo, revisions in selected_by_repo.items():
        total_revisions += len(revisions)
        if len(revisions) == len(repo.revisions):
            repo_count += 1
            revisions_in_full_repos += len(revisions)

    partial_revision_count = total_revisions - revisions_in_full_repos
    return CacheDeletionCounts(repo_count, partial_revision_count, total_revisions)


def _prune_summary(revision_count: int, incomplete_count: int, shared_blob_count: int) -> str:
    """Build the human-readable summary of what `hf cache prune` is about to delete."""
    parts: list[str] = []
    if revision_count:
        parts.append(f"{revision_count} unreferenced revision(s)")
    if incomplete_count:
        parts.append(f"{incomplete_count} incomplete download(s)")
    if shared_blob_count:
        parts.append(f"{shared_blob_count} unreferenced shared blob(s)")
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return "".join(parts)


def print_cache_selected_revisions(selected_by_repo: Mapping[CachedRepoInfo, frozenset[CachedRevisionInfo]]) -> None:
    """Pretty-print selected cache revisions during confirmation prompts."""
    for repo in sorted(selected_by_repo.keys(), key=lambda repo: (repo.repo_type, repo.repo_id.lower())):
        repo_key = f"{repo.repo_type}/{repo.repo_id}"
        revisions = sorted(selected_by_repo[repo], key=lambda rev: rev.commit_hash)
        if len(revisions) == len(repo.revisions):
            out.text(f"  - {repo_key} (entire repo)")
            continue

        out.text(f"  - {repo_key}:")
        for revision in revisions:
            refs = " ".join(sorted(revision.refs)) or "(detached)"
            out.text(f"      {revision.commit_hash} [{refs}] {revision.size_on_disk_str}")


def build_cache_index(
    hf_cache_info: HFCacheInfo,
) -> tuple[
    dict[str, CachedRepoInfo],
    dict[str, tuple[CachedRepoInfo, CachedRevisionInfo]],
]:
    """Create lookup tables so CLI commands can resolve repo ids and revisions quickly."""
    repo_lookup: dict[str, CachedRepoInfo] = {}
    revision_lookup: dict[str, tuple[CachedRepoInfo, CachedRevisionInfo]] = {}
    for repo in hf_cache_info.repos:
        repo_key = repo.cache_id.lower()
        repo_lookup[repo_key] = repo
        for revision in repo.revisions:
            revision_lookup[revision.commit_hash.lower()] = (repo, revision)
    return repo_lookup, revision_lookup


def _parse_hf_uri_target(target: str) -> HfUri:
    """Parse and validate an hf:// target passed to `hf cache rm`."""
    uri = parse_hf_uri(target)
    if not uri.is_repo:
        raise CLIError("Only repository hf:// URIs are supported by `hf cache rm`.")
    if uri.revision is not None:
        hint = (
            "Drop '@<revision>' to delete the file from all cached revisions."
            if uri.path_in_repo
            else "Pass the revision hash instead, see 'hf cache ls --revisions'."
        )
        raise CLIError(f"Revisions in hf:// URIs are not supported by `hf cache rm`. {hint}")
    return uri


def _cache_id(uri: HfUri) -> str:
    """Return the `type/id` cache id of a repo URI."""
    return f"{uri.type}/{uri.id}"


def collect_cache_entries(
    hf_cache_info: HFCacheInfo, *, include_revisions: bool
) -> tuple[list[CacheEntry], RepoRefsMap]:
    """Flatten cache metadata into rows consumed by `hf cache ls`."""
    entries: list[CacheEntry] = []
    repo_refs_map: RepoRefsMap = {}
    sorted_repos = sorted(hf_cache_info.repos, key=lambda repo: (repo.repo_type, repo.repo_id.lower()))
    for repo in sorted_repos:
        repo_refs_map[repo] = frozenset({ref for revision in repo.revisions for ref in revision.refs})
        if include_revisions:
            for revision in sorted(repo.revisions, key=lambda rev: rev.commit_hash):
                entries.append((repo, revision))
        else:
            entries.append((repo, None))
    if include_revisions:
        entries.sort(
            key=lambda entry: (
                entry[0].cache_id,
                entry[1].commit_hash if entry[1] is not None else "",
            )
        )
    else:
        entries.sort(key=lambda entry: entry[0].cache_id)
    return entries, repo_refs_map


def compile_cache_filter(
    expr: str, repo_refs_map: RepoRefsMap
) -> Callable[[CachedRepoInfo, CachedRevisionInfo | None, float], bool]:
    """Convert a `hf cache ls` filter expression into the yes/no test we apply to each cache entry before displaying it."""
    match = _FILTER_PATTERN.fullmatch(expr.strip())
    if not match:
        raise ValueError(f"Invalid filter expression: '{expr}'.")

    key = match.group("key").lower()
    op = match.group("op")
    value_raw = match.group("value").strip()

    if op not in _ALLOWED_OPERATORS:
        raise ValueError(f"Unsupported operator '{op}' in filter '{expr}'. Must be one of {list(_ALLOWED_OPERATORS)}.")

    if key not in _FILTER_KEYS:
        raise ValueError(f"Unsupported filter key '{key}' in '{expr}'. Must be one of {list(_FILTER_KEYS)}.")
    # at this point we know that key is in `_FILTER_KEYS`
    if key == "size":
        size_threshold = parse_size(value_raw)
        return lambda repo, revision, _: _compare_numeric(
            revision.size_on_disk if revision is not None else repo.size_on_disk,
            op,
            size_threshold,
        )

    if key in {"modified", "accessed"}:
        seconds = parse_duration(value_raw.strip())

        def _time_filter(repo: CachedRepoInfo, revision: CachedRevisionInfo | None, now: float) -> bool:
            timestamp = (
                repo.last_accessed
                if key == "accessed"
                else revision.last_modified
                if revision is not None
                else repo.last_modified
            )
            if timestamp is None:
                return False
            return _compare_numeric(now - timestamp, op, seconds)

        return _time_filter

    if key == "type":
        expected = value_raw.lower()

        if op != "=":
            raise ValueError(f"Only '=' is supported for 'type' filters. Got '{op}'.")

        def _type_filter(repo: CachedRepoInfo, revision: CachedRevisionInfo | None, _: float) -> bool:
            return repo.repo_type.lower() == expected

        return _type_filter

    else:  # key == "refs"
        if op != "=":
            raise ValueError(f"Only '=' is supported for 'refs' filters. Got {op}.")

        def _refs_filter(repo: CachedRepoInfo, revision: CachedRevisionInfo | None, _: float) -> bool:
            refs = revision.refs if revision is not None else repo_refs_map.get(repo, frozenset())
            return value_raw.lower() in [ref.lower() for ref in refs]

        return _refs_filter


def _compare_numeric(left: float | None, op: str, right: float) -> bool:
    """Evaluate numeric comparisons for filters."""
    if left is None:
        return False

    comparisons = {
        "=": left == right,
        "!=": left != right,
        ">": left > right,
        "<": left < right,
        ">=": left >= right,
        "<=": left <= right,
    }

    if op not in comparisons:
        raise ValueError(f"Unsupported numeric comparison operator: {op}")

    return comparisons[op]


def compile_cache_sort(sort_expr: str) -> tuple[Callable[[CacheEntry], tuple[Any, ...]], bool]:
    """Convert a `hf cache ls` sort expression into a key function for sorting entries.

    Returns:
        A tuple of (key_function, reverse_flag) where reverse_flag indicates whether
        to sort in descending order (True) or ascending order (False).
    """
    match = _SORT_PATTERN.fullmatch(sort_expr.strip().lower())
    if not match:
        raise ValueError(f"Invalid sort expression: '{sort_expr}'. Expected format: 'key' or 'key:asc' or 'key:desc'.")

    key = match.group("key").lower()
    explicit_order = match.group("order")

    if key not in _SORT_KEYS:
        raise ValueError(f"Unsupported sort key '{key}' in '{sort_expr}'. Must be one of {list(_SORT_KEYS)}.")

    # Use explicit order if provided, otherwise use default for the key
    order = explicit_order if explicit_order else _SORT_DEFAULT_ORDER[key]
    reverse = order == "desc"

    def _sort_key(entry: CacheEntry) -> tuple[Any, ...]:
        repo, revision = entry

        if key == "name":
            # Sort by cache_id (repo type/id)
            value: Any = repo.cache_id.lower()
            return (value,)

        if key == "size":
            # Use revision size if available, otherwise repo size
            value = revision.size_on_disk if revision is not None else repo.size_on_disk
            return (value,)

        if key == "accessed":
            # For revisions, accessed is not available per-revision, use repo's last_accessed
            # For repos, use repo's last_accessed
            value = repo.last_accessed if repo.last_accessed is not None else 0.0
            return (value,)

        if key == "modified":
            # Use revision's last_modified if available, otherwise repo's last_modified
            if revision is not None:
                value = revision.last_modified if revision.last_modified is not None else 0.0
            else:
                value = repo.last_modified if repo.last_modified is not None else 0.0
            return (value,)

        # Should never reach here due to validation above
        raise ValueError(f"Unsupported sort key: {key}")

    return _sort_key, reverse


def _resolve_deletion_targets(hf_cache_info: HFCacheInfo, targets: list[str]) -> _DeletionResolution:
    """Resolve the deletion targets into a deletion resolution."""
    targets = [stripped for target in targets if (stripped := target.strip())]
    uris = {target: _parse_hf_uri_target(target) for target in targets if target.startswith("hf://")}
    file_uris = {target: uri for target, uri in uris.items() if uri.path_in_repo}
    if file_uris:
        if len(file_uris) != len(set(targets)):
            raise CLIError("File targets cannot be mixed with repository or revision targets.")
        return _resolve_file_targets(hf_cache_info, file_uris)

    repo_lookup, revision_lookup = build_cache_index(hf_cache_info)

    selected: dict[CachedRepoInfo, set[CachedRevisionInfo]] = defaultdict(set)
    revisions: set[str] = set()
    missing: list[str] = []

    for target in targets:
        lowered = target.lower()

        if re.fullmatch(r"[0-9a-fA-F]{40}", lowered):
            match = revision_lookup.get(lowered)
            if match is None:
                missing.append(target)
                continue
            repo, revision = match
            selected[repo].add(revision)
            revisions.add(revision.commit_hash)
            continue

        cache_id = _cache_id(uris[target]) if target in uris else target
        matched_repo = repo_lookup.get(cache_id.lower())
        if matched_repo is None:
            missing.append(target)
            continue

        for revision in matched_repo.revisions:
            selected[matched_repo].add(revision)
            revisions.add(revision.commit_hash)

    frozen_selected = {repo: frozenset(revs) for repo, revs in selected.items()}
    return _DeletionResolution(
        revisions=frozenset(revisions),
        selected=frozen_selected,
        missing=tuple(missing),
    )


def _resolve_file_targets(hf_cache_info: HFCacheInfo, file_uris: dict[str, HfUri]) -> _DeletionResolution:
    """Select the cached files matching hf:// file URIs, in every cached revision of their repo."""
    repo_lookup, _ = build_cache_index(hf_cache_info)
    files: dict[CachedFileInfo, str] = {}
    missing: list[str] = []
    for target, uri in file_uris.items():
        repo = repo_lookup.get(_cache_id(uri).lower())
        if repo is None:
            missing.append(target)
            continue
        matches = {
            file: f"{repo.cache_id}@{revision.commit_hash}/{uri.path_in_repo}"
            for revision in repo.revisions
            for file in revision.files
            if file.file_path.relative_to(revision.snapshot_path).as_posix() == uri.path_in_repo
        }
        if not matches:
            missing.append(target)
        files.update(matches)
    return _DeletionResolution(revisions=frozenset(), selected={}, missing=tuple(missing), files=files)


#### Cache CLI commands


@cache_cli.command(
    "list | ls",
    examples=[
        "hf cache ls",
        "hf cache ls --revisions",
        'hf cache ls --filter "size>1GB" --limit 20',
        "hf cache ls --format json",
    ],
)
def ls(
    cache_dir: Annotated[
        str | None,
        Option(
            help="Cache directory to scan (defaults to Hugging Face cache).",
        ),
    ] = None,
    revisions: Annotated[
        bool,
        Option(
            help="Include revisions in the output instead of aggregated repositories.",
        ),
    ] = False,
    filter: Annotated[
        list[str] | None,
        Option(
            "-f",
            "--filter",
