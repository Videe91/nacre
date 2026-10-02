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
import hashlib
import platform
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from huggingface_hub import constants
from huggingface_hub._space_api import Volume
from huggingface_hub.utils._datetime import parse_datetime


class JobHardware(str, Enum):
    """
    Enumeration of hardware flavors available to run Jobs on the Hub.

    Value can be compared to a string:
    ```py
    assert JobHardware.CPU_BASIC == "cpu-basic"
    ```

    Both enums are kept in sync with the Hub API by `utils/check_hardware_flavors.py`.
    """

    # CPU
    CPU_BASIC = "cpu-basic"
    CPU_UPGRADE = "cpu-upgrade"
    CPU_PERFORMANCE = "cpu-performance"
    CPU_XL = "cpu-xl"

    # GPU
    T4_SMALL = "t4-small"
    T4_MEDIUM = "t4-medium"
    L4X1 = "l4x1"
    L4X4 = "l4x4"
    L40SX1 = "l40sx1"
    L40SX4 = "l40sx4"
    L40SX8 = "l40sx8"
    A10G_SMALL = "a10g-small"
    A10G_LARGE = "a10g-large"
    A10G_LARGEX2 = "a10g-largex2"
    A10G_LARGEX4 = "a10g-largex4"
    A100_LARGE = "a100-large"
    A100X4 = "a100x4"
    A100X8 = "a100x8"
    H200 = "h200"
    H200X2 = "h200x2"
    H200X4 = "h200x4"
    H200X8 = "h200x8"
    RTX_PRO_6000 = "rtx-pro-6000"
    RTX_PRO_6000X2 = "rtx-pro-6000x2"
    RTX_PRO_6000X4 = "rtx-pro-6000x4"
    RTX_PRO_6000X8 = "rtx-pro-6000x8"


class JobStage(str, Enum):
    """
    Enumeration of possible stage of a Job on the Hub.

    Value can be compared to a string:
    ```py
    assert JobStage.COMPLETED == "COMPLETED"
    ```
    Possible values are: `COMPLETED`, `CANCELED`, `ERROR`, `DELETED`, `SCHEDULING`, `RUNNING`.
    Taken from https://github.com/huggingface/moon-landing/blob/main/server/job_types/JobInfo.ts#L61 (private url).
    """

    # Copied from moon-landing > server > lib > Job.ts
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    ERROR = "ERROR"
    DELETED = "DELETED"
    SCHEDULING = "SCHEDULING"
    RUNNING = "RUNNING"


# Stages indicating the Job has reached a terminal state and will not run further.
TERMINAL_JOB_STAGES = (JobStage.COMPLETED, JobStage.CANCELED, JobStage.ERROR, JobStage.DELETED)

# Default image used to run UV scripts: Debian Bookworm with Python 3.12 and `uv` pre-installed.
DEFAULT_UV_IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm"

# URL prefixes identifying an image that points to a HF Space rather than a Docker image.
_SPACE_IMAGE_PREFIXES = (
    "https://huggingface.co/spaces/",
    "https://hf.co/spaces/",
    "huggingface.co/spaces/",
    "hf.co/spaces/",
)


@dataclass
class JobStatus:
    stage: JobStage
    message: str | None
    expose_urls: list[str] | None
    ssh_url: str | None


@dataclass
class JobOwner:
    id: str
    name: str
    type: str


@dataclass
class JobNetwork:
    """
    Network group a Job joined.

    Args:
        group (`str`):
            Name of the network group, as passed to `network_group=`.
        aliases (`list[str]`):
            Aliases the Job claims in the group, as passed to `network_aliases=`. Empty when none.
    """

    group: str
    aliases: list[str]

    def __init__(self, **kwargs) -> None:
        self.group = kwargs["group"]
        self.aliases = kwargs.get("aliases") or []


@dataclass
class JobDurations:
    """
    Timing breakdown for a Job, computed server-side.

    Args:
        scheduling_secs (`int` or `None`):
            Seconds the job spent in the scheduling stage before starting to run.
            `None` if the job never reached the running stage.
        running_secs (`int` or `None`):
            Seconds the job has been or was running. Recomputed on each request
            while the job is in progress. `None` if the job never started running.
        total_secs (`int` or `None`):
            Total seconds elapsed since the job was created. Recomputed on each
            request while the job is in progress.
    """

    scheduling_secs: int | None
    running_secs: int | None
    total_secs: int | None

    def __init__(self, **kwargs) -> None:
        self.scheduling_secs = kwargs.get("schedulingSecs", kwargs.get("scheduling_secs"))
        self.running_secs = kwargs.get("runningSecs", kwargs.get("running_secs"))
        self.total_secs = kwargs.get("totalSecs", kwargs.get("total_secs"))


@dataclass
class JobInitiator:
    """
    Contains information about what triggered a Job.

    Args:
        type (`str`): Initiator kind, for example `"user"`, `"org"`, `"scheduled-job"`, or `"duplicated-job"`.
        id (`str`): Identifier of the initiator.
        name (`str` or `None`): Human-readable name when available, usually for user/org initiators.
    """

    type: str
    id: str
    name: str | None = None


@dataclass
class JobInfo:
    """
    Contains information about a Job.

    Args:
        id (`str`):
            Job ID.
        created_at (`datetime` or `None`):
            When the Job was created.
        started_at (`datetime` or `None`):
            When the Job started running. None while the Job is still scheduling.
        finished_at (`datetime` or `None`):
            When the Job finished. None while the Job is still scheduling or running.
        docker_image (`str` or `None`):
            The Docker image from Docker Hub used for the Job.
            Can be None if space_id is present instead.
        space_id (`str` or `None`):
            The Docker image from Hugging Face Spaces used for the Job.
            Can be None if docker_image is present instead.
        command (`list[str]` or `None`):
            Command of the Job, e.g. `["python", "-c", "print('hello world')"]`
        arguments (`list[str]` or `None`):
            Arguments passed to the command
        environment (`dict[str]` or `None`):
            Environment variables of the Job as a dictionary.
        secrets (`dict[str]` or `None`):
            Secret environment variables of the Job (encrypted).
        flavor (`str` or `None`):
            Flavor for the hardware. See [`JobHardware`] for possible values.
            E.g. `"cpu-basic"`.
        labels (`dict[str, str]` or `None`):
            Labels to attach to the job (key-value pairs).
        volumes (`list[Volume]` or `None`):
            Volumes mounted in the job container (buckets, models, datasets, spaces).
        status: (`JobStatus` or `None`):
            Status of the Job, e.g. `JobStatus(stage="RUNNING", message=None)`
            See [`JobStage`] for possible stage values.
        durations (`JobDurations` or `None`):
            Timing breakdown of the Job. Present for all job states including SCHEDULING.
        owner: (`JobOwner` or `None`):
            Owner of the Job, e.g. `JobOwner(id="5e9ecfc04957053f60648a3e", name="lhoestq", type="user")`
        initiator (`JobInitiator` or `None`):
            What triggered the Job, e.g. `JobInitiator(type="scheduled-job", id="...")` for a cron-triggered run.
        expose_urls (`list[str]` or `None`):
            Public URLs through which the Job's exposed ports are reachable (one per port exposed via `expose=`),
            e.g. `["https://687fb701029421ae5549d998--8000.hf.jobs"]`. `None` when no port is exposed.
            Accessing a URL requires an HF token with read access to the Job's namespace.
        ssh_url (`str` or `None`):
            SSH endpoint of the Job, e.g. `"ssh://687fb701029421ae5549d998@ssh.hf.jobs"`. Only present when the Job
            was started with `ssh=True`. Connecting requires write access to the Job's namespace and an SSH public
            key registered on the Hub (https://huggingface.co/settings/keys).
        network (`JobNetwork` or `None`):
            Network group the Job joined and the aliases it claims, e.g. `JobNetwork(group="train", aliases=["master"])`.
            `None` when the Job was started without `network_group=`.

    Example:

    ```python
    >>> from huggingface_hub import run_job
    >>> job = run_job(
    ...     image="python:3.12",
    ...     command=["python", "-c", "print('Hello from the cloud!')"]
    ... )
    >>> job
    JobInfo(id='687fb701029421ae5549d998', created_at=datetime.datetime(2025, 7, 22, 16, 6, 25, 79000, tzinfo=datetime.timezone.utc), started_at=datetime.datetime(2025, 7, 22, 16, 6, 31, 79000, tzinfo=datetime.timezone.utc), finished_at=None, docker_image='python:3.12', space_id=None, command=['python', '-c', "print('Hello from the cloud!')"], arguments=[], environment={}, secrets={}, flavor='cpu-basic', labels=None, status=JobStatus(stage='RUNNING', message=None), durations=JobDurations(scheduling_secs=6, running_secs=2, total_secs=8), owner=JobOwner(id='5e9ecfc04957053f60648a3e', name='lhoestq', type='user'), initiator=JobInitiator(type='user', id='5e9ecfc04957053f60648a3e', name='lhoestq'), endpoint='https://huggingface.co', url='https://huggingface.co/jobs/lhoestq/687fb701029421ae5549d998')
    >>> job.id
    '687fb701029421ae5549d998'
    >>> job.url
    'https://huggingface.co/jobs/lhoestq/687fb701029421ae5549d998'
    >>> job.status.stage
    'RUNNING'
    ```
    """

    id: str
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    docker_image: str | None
    space_id: str | None
    command: list[str] | None
    arguments: list[str] | None
    environment: dict[str, Any] | None
    secrets: dict[str, Any] | None
    flavor: JobHardware | None
    labels: dict[str, str] | None
    volumes: list[Volume] | None
    status: JobStatus
    durations: JobDurations | None
    owner: JobOwner
    initiator: JobInitiator | None
    network: JobNetwork | None

    # Inferred fields
    endpoint: str
    url: str

    def __init__(self, **kwargs) -> None:
        self.id = kwargs["id"]
        created_at = kwargs.get("createdAt") or kwargs.get("created_at")
        self.created_at = parse_datetime(created_at) if created_at else None
        started_at = kwargs.get("startedAt") or kwargs.get("started_at")
        self.started_at = parse_datetime(started_at) if started_at else None
        finished_at = kwargs.get("finishedAt") or kwargs.get("finished_at")
        self.finished_at = parse_datetime(finished_at) if finished_at else None
        self.docker_image = kwargs.get("dockerImage") or kwargs.get("docker_image")
        self.space_id = kwargs.get("spaceId") or kwargs.get("space_id")
        owner = kwargs.get("owner", {})
        self.owner = JobOwner(id=owner["id"], name=owner["name"], type=owner["type"])
        self.command = kwargs.get("command")
        self.arguments = kwargs.get("arguments")
        self.environment = kwargs.get("environment")
        self.secrets = kwargs.get("secrets")
        self.flavor = kwargs.get("flavor")
        self.labels = kwargs.get("labels")
        volumes = kwargs.get("volumes")
        self.volumes = [Volume(**v) for v in volumes] if volumes else None
        status = kwargs.get("status", {})
        self.status = JobStatus(
            stage=status["stage"],
            message=status.get("message"),
            expose_urls=status.get("exposeUrls"),
            ssh_url=status.get("sshUrl"),
        )
        durations = kwargs.get("durations")
        self.durations = JobDurations(**durations) if durations else None
        initiator = kwargs.get("initiator")
        self.initiator = (
            JobInitiator(type=initiator["type"], id=initiator["id"], name=initiator.get("name")) if initiator else None
        )
        network = kwargs.get("network")
        self.network = JobNetwork(**network) if network else None

        # Inferred fields
        self.endpoint = kwargs.get("endpoint", constants.ENDPOINT)
        self.url = f"{self.endpoint}/jobs/{self.owner.name}/{self.id}"


@dataclass
class JobSpec:
    docker_image: str | None
    space_id: str | None
    command: list[str] | None
    arguments: list[str] | None
    environment: dict[str, Any] | None
    secrets: dict[str, Any] | None
    flavor: JobHardware | None
    timeout: int | None
    tags: list[str] | None
    arch: str | None
    labels: dict[str, str] | None
    volumes: list[Volume] | None

    def __init__(self, **kwargs) -> None:
        self.docker_image = kwargs.get("dockerImage") or kwargs.get("docker_image")
        self.space_id = kwargs.get("spaceId") or kwargs.get("space_id")
        self.command = kwargs.get("command")
        self.arguments = kwargs.get("arguments")
        self.environment = kwargs.get("environment")
        self.secrets = kwargs.get("secrets")
        self.flavor = kwargs.get("flavor")
        self.timeout = kwargs.get("timeout")
        self.tags = kwargs.get("tags")
        self.arch = kwargs.get("arch")
        self.labels = kwargs.get("labels")
        volumes = kwargs.get("volumes")
        self.volumes = [Volume(**v) for v in volumes] if volumes else None


@dataclass
class LastJobInfo:
    id: str
    at: datetime

    def __init__(self, **kwargs) -> None:
        self.id = kwargs["id"]
        self.at = parse_datetime(kwargs["at"])


@dataclass
class ScheduledJobStatus:
    last_job: LastJobInfo | None
    next_job_run_at: datetime | None

    def __init__(self, **kwargs) -> None:
        last_job = kwargs.get("lastJob") or kwargs.get("last_job")
        self.last_job = LastJobInfo(**last_job) if last_job else None
        next_job_run_at = kwargs.get("nextJobRunAt") or kwargs.get("next_job_run_at")
        self.next_job_run_at = parse_datetime(str(next_job_run_at)) if next_job_run_at else None


@dataclass
class ScheduledJobInfo:
    """
    Contains information about a Job.

    Args:
        id (`str`):
            Scheduled Job ID.
        created_at (`datetime` or `None`):
            When the scheduled Job was created.
        tags (`list[str]` or `None`):
            The tags of the scheduled Job.
        schedule (`str` or `None`):
            One of "@annually", "@yearly", "@monthly", "@weekly", "@daily", "@hourly", or a
            CRON schedule expression (e.g., '0 9 * * 1' for 9 AM every Monday).
        suspend (`bool` or `None`):
            Whether the scheduled job is suspended (paused).
        concurrency (`bool` or `None`):
            Whether multiple instances of this Job can run concurrently.
        status (`ScheduledJobStatus` or `None`):
            Status of the scheduled Job.
        owner: (`JobOwner` or `None`):
            Owner of the scheduled Job, e.g. `JobOwner(id="5e9ecfc04957053f60648a3e", name="lhoestq", type="user")`
        job_spec: (`JobSpec` or `None`):
            Specifications of the Job.

    Example:

    ```python
    >>> from huggingface_hub import run_job
    >>> scheduled_job = create_scheduled_job(
    ...     image="python:3.12",
    ...     command=["python", "-c", "print('Hello from the cloud!')"],
    ...     schedule="@hourly",
    ... )
    >>> scheduled_job.id
    '687fb701029421ae5549d999'
    >>> scheduled_job.status.next_job_run_at
    datetime.datetime(2025, 7, 22, 17, 6, 25, 79000, tzinfo=datetime.timezone.utc)
    ```
    """

    id: str
    created_at: datetime | None
    job_spec: JobSpec
    schedule: str | None
    suspend: bool | None
    concurrency: bool | None
    status: ScheduledJobStatus
    owner: JobOwner

    def __init__(self, **kwargs) -> None:
        self.id = kwargs["id"]
        created_at = kwargs.get("createdAt") or kwargs.get("created_at")
        self.created_at = parse_datetime(created_at) if created_at else None
        self.job_spec = JobSpec(**(kwargs.get("job_spec") or kwargs.get("jobSpec", {})))
        self.schedule = kwargs.get("schedule")
        self.suspend = kwargs.get("suspend")
        self.concurrency = kwargs.get("concurrency")
        status = kwargs.get("status", {})
        self.status = ScheduledJobStatus(
