# Copyright 2026 The HuggingFace Team. All rights reserved.
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
"""Contains commands to interact with spaces on the Hugging Face Hub."""

import enum
import functools
import itertools
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, get_args

import click
from packaging import version
from typing_extensions import assert_never

from huggingface_hub._hot_reload.client import multi_replica_reload_events
from huggingface_hub._hot_reload.types import ApiGetReloadEventSourceData, ReloadRegion
from huggingface_hub._space_api import SpaceHardware, SpaceStage
from huggingface_hub.cli._cli_utils import SoftChoice
from huggingface_hub.errors import CLIError, RemoteEntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError
from huggingface_hub.file_download import hf_hub_download
from huggingface_hub.hf_api import ExpandSpaceProperty_T, HfApi, SpaceSort_T
from huggingface_hub.repocard import SpaceCard
from huggingface_hub.utils import disable_progress_bars
from huggingface_hub.utils._parsing import parse_duration

from ._cli_utils import (
    REPO_LIST_DEFAULT_LIMIT,
    AuthorOpt,
    EnvFileOpt,
    EnvOpt,
    FilterOpt,
    LimitOpt,
    RevisionOpt,
    SearchOpt,
    SecretsFileOpt,
    SecretsOpt,
    SshDryRunOpt,
    SshIdentityFileOpt,
    TokenOpt,
    VolumesOpt,
    exec_ssh,
    get_hf_api,
    make_expand_properties_parser,
    parse_env_map,
    parse_volumes,
    typer_factory,
)
from ._file_listing import list_repo_files_cmd
from ._framework import Argument, Option
from ._output import _dataclass_to_dict, out


HOT_RELOADING_MIN_GRADIO = "6.1.0"


_EXPAND_PROPERTIES = sorted(get_args(ExpandSpaceProperty_T))
_SORT_OPTIONS = get_args(SpaceSort_T)
SpaceSortEnum = enum.Enum("SpaceSortEnum", {s: s for s in _SORT_OPTIONS}, type=str)  # type: ignore[misc]


ExpandOpt = Annotated[
    str | None,
    Option(
        help=f"Comma-separated properties to return. When used, only the listed properties (and id) are returned. Example: '--expand=likes,tags'. Valid: {', '.join(_EXPAND_PROPERTIES)}.",
        callback=make_expand_properties_parser(_EXPAND_PROPERTIES),
    ),
]

spaces_cli = typer_factory(help="Interact with spaces on the Hub.")
volumes_cli = typer_factory(help="Manage volumes for a Space on the Hub.")
secrets_cli = typer_factory(help="Manage secrets for a Space on the Hub.")
variables_cli = typer_factory(help="Manage environment variables for a Space on the Hub.")
spaces_cli.add_group(volumes_cli, name="volumes")
spaces_cli.add_group(secrets_cli, name="secrets")
spaces_cli.add_group(variables_cli, name="variables")


@spaces_cli.command(
    "list | ls",
    examples=[
        "hf spaces ls --limit 10",
        'hf spaces ls --search "chatbot" --author huggingface',
        "hf spaces ls victor/deepsite",
        "hf spaces ls victor/deepsite -R",
        "hf spaces ls victor/deepsite --tree -h",
    ],
)
def spaces_ls(
    repo_id: Annotated[
        str | None,
        Argument(help="Space ID (e.g. `username/repo-name`) to list files from. If omitted, lists spaces."),
    ] = None,
    search: SearchOpt = None,
    author: AuthorOpt = None,
    filter: FilterOpt = None,
    sort: Annotated[
        SpaceSortEnum | None,
        Option(help="Sort results."),
    ] = None,
    limit: LimitOpt = REPO_LIST_DEFAULT_LIMIT,
    expand: ExpandOpt = None,
    human_readable: Annotated[
        bool,
        Option("--human-readable", "-h", help="Show sizes in human readable format (only for listing files)."),
    ] = False,
    as_tree: Annotated[
        bool,
        Option("--tree", help="List files in tree format (only for listing files)."),
    ] = False,
    recursive: Annotated[
        bool,
        Option("--recursive", "-R", help="List files recursively (only for listing files)."),
    ] = False,
    revision: RevisionOpt = None,
    token: TokenOpt = None,
) -> None:
    """List spaces on the Hub, or files in a space repo.

    When called with no argument, lists spaces on the Hub.
    When called with a space ID, lists files in that space repo.
    """
    if repo_id is not None:
        if search is not None:
            raise click.BadParameter("Cannot use --search when listing files.")
        if author is not None:
            raise click.BadParameter("Cannot use --author when listing files.")
        if filter is not None:
            raise click.BadParameter("Cannot use --filter when listing files.")
        if sort is not None:
            raise click.BadParameter("Cannot use --sort when listing files.")
        if limit != REPO_LIST_DEFAULT_LIMIT:
            raise click.BadParameter("Cannot use --limit when listing files.")
        if expand is not None:
            raise click.BadParameter("Cannot use --expand when listing files.")
        return list_repo_files_cmd(
            repo_id=repo_id,
            repo_type="space",
            human_readable=human_readable,
            as_tree=as_tree,
            recursive=recursive,
            revision=revision,
            token=token,
        )

    if as_tree:
        raise click.BadParameter("Cannot use --tree when listing spaces.")
    if recursive:
        raise click.BadParameter("Cannot use --recursive when listing spaces.")
    if human_readable:
        raise click.BadParameter("Cannot use --human-readable when listing spaces.")
    if revision is not None:
        raise click.BadParameter("Cannot use --revision when listing spaces.")
    api = get_hf_api(token=token)
    sort_key = sort.value if sort else None
    results = [
        _dataclass_to_dict(space_info)
        for space_info in api.list_spaces(
            filter=filter,
            author=author,
            search=search,
            sort=sort_key,
            limit=limit,
            expand=expand,  # type: ignore[arg-type]
        )
    ]
    out.table(results)


@spaces_cli.command(
    "info",
    examples=[
        "hf spaces info enzostvs/deepsite",
        "hf spaces info gradio/theme_builder --expand sdk,runtime,likes",
    ],
)
def spaces_info(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    revision: RevisionOpt = None,
    expand: ExpandOpt = None,
    token: TokenOpt = None,
) -> None:
    """Get info about a space on the Hub."""
    api = get_hf_api(token=token)
    try:
        info = api.space_info(repo_id=space_id, revision=revision, expand=expand)  # type: ignore[arg-type]
    except RepositoryNotFoundError as e:
        raise CLIError(f"Space '{space_id}' not found.") from e
    except RevisionNotFoundError as e:
        raise CLIError(f"Revision '{revision}' not found on '{space_id}'.") from e
    out.dict(info)


@spaces_cli.command(
    "card",
    examples=[
        "hf spaces card mteb/leaderboard",
        "hf spaces card mteb/leaderboard --metadata",
        "hf spaces card mteb/leaderboard --metadata --format json",
        "hf spaces card mteb/leaderboard --text",
    ],
)
def spaces_card(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    metadata: Annotated[bool, Option("--metadata", help="Output only the metadata from the card.")] = False,
    text: Annotated[bool, Option("--text", help="Output only the text body (no metadata).")] = False,
    token: TokenOpt = None,
) -> None:
    """Get the Space card (README) for a Space on the Hub."""
    if metadata and text:
        raise CLIError("--metadata and --text are mutually exclusive.")
    card = SpaceCard.load(space_id, token=token)
    if metadata:
        out.dict(card.data.to_dict())
    elif text:
        out.text(card.text)
    else:
        out.text(card.content)
        out.hint(f"Use `hf spaces card {space_id} --metadata` to extract only the card metadata.")


@spaces_cli.command(
    "templates",
    examples=["hf spaces templates"],
)
def spaces_templates(
    token: TokenOpt = None,
) -> None:
    """List the available Space templates.

    The `repo_id` (or `name`) of a template can be passed to `hf repos create --template ...` to
    create a new Space from that template.
    """
    api = get_hf_api(token=token)
    templates = [_dataclass_to_dict(template) for template in api.list_space_templates()]
    out.table(templates, id_key="name")
    out.hint(
        "Create a Space from a template with `hf repos create <id> --type space --space-sdk <sdk> --template <repo_id>`."
    )


@spaces_cli.command(
    "search",
    examples=[
        'hf spaces search "generate image"',
        'hf spaces search "identify objects in pictures" --sdk gradio --limit 5',
        'hf spaces search "remove background from photo" --description --json',
    ],
)
def spaces_search(
    query: Annotated[str, Argument(help="Search query.")],
    filter: FilterOpt = None,
    sdk: Annotated[list[str] | None, Option(help="Filter by SDK (e.g. gradio, docker, static).")] = None,
    include_non_running: Annotated[bool, Option(help="Include non-running spaces in results.")] = False,
    description: Annotated[bool, Option(help="Show AI-generated descriptions.")] = False,
    limit: LimitOpt = 10,
    token: TokenOpt = None,
) -> None:
    """Search spaces on the Hub using semantic search."""
    api = get_hf_api(token=token)
    results = api.search_spaces(
        query=query,
        filter=filter,
        sdk=sdk,
        include_non_running=include_non_running,
        token=token,
    )
    items = []
    for r in itertools.islice(results, limit):
        item: dict = {
            "id": r.id,
            "title": r.title,
            "sdk": r.sdk,
            "likes": r.likes,
            "stage": r.runtime.stage if r.runtime else None,
            "category": r.ai_category,
            "score": round(r.semantic_relevancy_score, 2) if r.semantic_relevancy_score is not None else None,
        }
        if description:
            item["description"] = r.ai_short_description
        items.append(item)
    out.table(items)
    if not description:
        out.hint("Use --description to show AI-generated descriptions.")


@spaces_cli.command(
    "wait",
    examples=[
        "hf spaces wait username/my-space",
        "hf spaces wait username/my-space --timeout 5m",
    ],
)
def spaces_wait(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    timeout: Annotated[
        str | None,
        Option(
            help="Max time to wait: int with s (seconds, default), m (minutes), h (hours) or d (days).",
        ),
    ] = None,
    token: TokenOpt = None,
) -> None:
    """Wait for a Space to finish building/starting.

    Blocks until the Space leaves an intermediate stage (BUILDING, APP_STARTING, etc.)
    and reaches a settled stage. Exits with code 0 if the Space is RUNNING,
    or a non-zero exit code otherwise (e.g. BUILD_ERROR, RUNTIME_ERROR).
    """
    timeout_secs = parse_duration(timeout) if timeout is not None else None
    api = get_hf_api(token=token)
    status = out.status("Waiting for Space to be ready...")
    try:
        runtime = api.wait_for_space(space_id, timeout=timeout_secs)
    except TimeoutError:
        status.done("Timed out.")
        raise CLIError(f"Timed out after {timeout} waiting for Space '{space_id}' to be ready.") from None
    status.done(f"Space reached stage '{runtime.stage}'.")
    if runtime.stage != SpaceStage.RUNNING:
        raise CLIError(f"Space '{space_id}' is not running (stage='{runtime.stage}').")
    out.result("Space ready", space_id=space_id, stage=str(runtime.stage))
    out.hint(f"Use `hf spaces logs {space_id}` to view run logs.")


@spaces_cli.command(
    "dev-mode",
    examples=[
        "hf spaces dev-mode my-user-name/deepsite",
    ],
)
def dev_mode(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    stop: Annotated[bool, Option(help="Stop dev mode.")] = False,
    token: TokenOpt = None,
):
    """
    Enable or disable dev mode on a Space.

    Spaces Dev Mode eases the debugging of your application and makes iterating on Spaces faster by allowing you to
    restart your application without stopping the Space container itself. This feature is available as part of a PRO
    or Team & Enterprise plan.

    See docs: https://huggingface.co/docs/hub/spaces-dev-mode
    """
    api = get_hf_api(token=token)
    if stop:
        api.disable_space_dev_mode(space_id)
        print(f"Dev mode disabled for '{space_id}'")
        return
    api.enable_space_dev_mode(space_id)
    runtime = api.wait_for_space(space_id)
    if runtime.stage != SpaceStage.RUNNING:
        out.warning(f"Dev mode is not ready (stage='{runtime.stage}')")
        return
    info = api.space_info(space_id)
    folder = getattr(info.card_data, "dev-mode-folder", "" if info.sdk == "docker" else "/home/user/app")
    folder_query_param = f"folder={folder}" if folder else ""
    print("Connect to dev environment:")
    print("")
    print("Web:")
    vscode_web_url = f"https://huggingface.co/spaces/{info.id}/dev-mode/vscode-web"
    if folder_query_param:
        vscode_web_url += f"?{folder_query_param}"
    ssh_host = f"{info.subdomain}@ssh.hf.space"
    print(f"  * VSCode: {vscode_web_url}")
    print("")
    print("Local:")
    print("1. Add your SSH key to https://huggingface.co/settings/keys")
    print(f"2. SSH with `hf spaces ssh {space_id}` (or `ssh -i <your_key> {ssh_host}`)")
    print("   Or open")
    print(f"  * VSCode: vscode://vscode-remote/ssh-remote+{ssh_host}{folder}")
    print(f"  * Cursor: cursor://vscode-remote/ssh-remote+{ssh_host}{folder}")
    print("")
    print("PS: Dev mode stops after 48h of inactivity, don't forget to save your changes regularly.")


@spaces_cli.command(
    "ssh",
    examples=[
        "hf spaces ssh username/my-space",
        "hf spaces ssh username/my-space --dry-run",
        "hf spaces ssh username/my-space -i ~/.ssh/id_ed25519",
        "hf spaces ssh username/my-space --auto",
    ],
)
def spaces_ssh(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    identity_file: SshIdentityFileOpt = None,
    dry_run: SshDryRunOpt = False,
    auto: Annotated[
        bool,
        Option("--auto", help="Enable Dev Mode without prompting if not already enabled."),
    ] = False,
    token: TokenOpt = None,
) -> None:
    """SSH into a Space's Dev Mode container.

    Requires Dev Mode to be running on the Space and your SSH public key to be registered at https://huggingface.co/settings/keys.

    See: https://huggingface.co/docs/hub/spaces-dev-mode
    """
    api = get_hf_api(token=token)
    info = api.space_info(space_id)
    if info.runtime is None or not info.runtime.dev_mode:
        out.confirm(
            f"Dev Mode is disabled on '{space_id}'. Enable it now?", yes=auto, default=True, confirm_param="--auto"
        )
        api.enable_space_dev_mode(space_id)
        runtime = api.wait_for_space(space_id)
        if runtime.stage != SpaceStage.RUNNING:
            raise CLIError(f"Space '{space_id}' is not running (stage='{runtime.stage}').")
        info = api.space_info(space_id)
    exec_ssh(f"{info.subdomain}@ssh.hf.space", identity_file=identity_file, dry_run=dry_run)


@spaces_cli.command(
    "pause",
    examples=[
        "hf spaces pause username/my-space",
    ],
)
def spaces_pause(
    space_id: Annotated[str, Argument(help="The space ID (e.g. `username/repo-name`).")],
    token: TokenOpt = None,
) -> None:
    """Pause a Space."""
    api = get_hf_api(token=token)
    runtime = api.pause_space(space_id)
    out.result("Space paused", space_id=space_id, stage=runtime.stage)
    out.hint(f"Use `hf spaces restart {space_id}` to restart it.")
    out.hint(
