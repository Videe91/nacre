import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from huggingface_hub.errors import InferenceEndpointError, InferenceEndpointTimeoutError

from .utils import get_session, logging, parse_datetime


if TYPE_CHECKING:
    from .hf_api import HfApi
    from .inference._client import InferenceClient
    from .inference._generated._async_client import AsyncInferenceClient

logger = logging.get_logger(__name__)


def _build_endpoint_image_payload(
    custom_image: dict,
    *,
    container_registry_username: str | None = None,
    container_registry_password: str | None = None,
) -> dict:
    """Build the `model.image` payload of an Inference Endpoint from a user-provided image dict.

    `model.image` is a union keyed by variant (`{"vLLM": {...}}`, `{"custom": {...}}`, ...). Only a flat
    container dict has a top-level `url` (required server-side, and no variant is named `url`), so dicts with
    one are wrapped in `{"custom": ...}`. Everything else is forwarded as-is, so variants added to the API
    later work without a release.
    """
    image = {"custom": custom_image} if "url" in custom_image else custom_image

    if container_registry_password is not None and container_registry_username is None:
        raise ValueError("`container_registry_password` requires `container_registry_username`.")
    if container_registry_username is None:
        return image

    custom_container = image.get("custom")
    if not isinstance(custom_container, dict):
        raise ValueError("Container registry credentials can only be set for a custom container image.")

    credentials = {"username": container_registry_username}
    if container_registry_password is not None:
        credentials["password"] = container_registry_password
    return {**image, "custom": {**custom_container, "credentials": credentials}}


# Image variants that declare `tensorParallelSize` / `dataParallelSize`. The API ignores a field a variant doesn't
# declare instead of rejecting it, so these only drive a warning: a wrong engine gets a visible no-op rather than a
# silent one, and an engine the API adds later still works without a `huggingface_hub` release.
_TENSOR_PARALLEL_IMAGE_KEYS = ("sGLang", "vLLM")
_DATA_PARALLEL_IMAGE_KEYS = ("vLLM",)


def _set_parallelism_in_image(
    image: dict,
    *,
    tensor_parallel_size: int | None = None,
    data_parallel_size: int | None = None,
) -> dict:
    """Write the parallelism sizes into a `model.image` payload.

    They are engine settings, so they live inside the engine config (`{"vLLM": {"url": ..., "tensorParallelSize": 8}}`)
    rather than at the top level. Returns a new image dict, the input is left untouched.
    """
    image_key = next(iter(image), None)
    if image_key is None:
        raise ValueError("Cannot set the parallelism sizes: the image payload is empty.")

    for value, name, supported in (
        (tensor_parallel_size, "tensor_parallel_size", _TENSOR_PARALLEL_IMAGE_KEYS),
        (data_parallel_size, "data_parallel_size", _DATA_PARALLEL_IMAGE_KEYS),
    ):
        if value is not None and image_key not in supported:
            warnings.warn(
                f"`{name}` is not a known setting of the '{image_key}' image: the API will silently drop it."
                f" Engines that support it: {', '.join(supported)}.",
                UserWarning,
            )

    image = {image_key: {**image[image_key]}}
    if tensor_parallel_size is not None:
        image[image_key]["tensorParallelSize"] = tensor_parallel_size
    if data_parallel_size is not None:
        image[image_key]["dataParallelSize"] = data_parallel_size
    return image


class InferenceEndpointStatus(str, Enum):
    PENDING = "pending"
    INITIALIZING = "initializing"
    UPDATING = "updating"
    UPDATE_FAILED = "updateFailed"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    SCALED_TO_ZERO = "scaledToZero"


class InferenceEndpointType(str, Enum):
    PUBlIC = "public"
    PROTECTED = "protected"  # deprecated, use AUTHENTICATED instead
    AUTHENTICATED = "authenticated"
    PRIVATE = "private"


class InferenceEndpointScalingMetric(str, Enum):
    PENDING_REQUESTS = "pendingRequests"
    HARDWARE_USAGE = "hardwareUsage"


@dataclass
class InferenceEndpoint:
    """
    Contains information about a deployed Inference Endpoint.

    Args:
        name (`str`):
            The unique name of the Inference Endpoint.
        namespace (`str`):
            The namespace where the Inference Endpoint is located.
        repository (`str`):
            The name of the model repository deployed on this Inference Endpoint.
        status ([`InferenceEndpointStatus`]):
            The current status of the Inference Endpoint.
        url (`str`, *optional*):
            The URL of the Inference Endpoint, if available. Only a deployed Inference Endpoint will have a URL.
        framework (`str`):
            The machine learning framework used for the model.
        revision (`str`):
            The specific model revision deployed on the Inference Endpoint.
        task (`str`):
            The task associated with the deployed model.
        created_at (`datetime.datetime`):
            The timestamp when the Inference Endpoint was created.
        updated_at (`datetime.datetime`):
            The timestamp of the last update of the Inference Endpoint.
        type ([`InferenceEndpointType`]):
            The type of the Inference Endpoint (public, authenticated, private).
        raw (`dict`):
            The raw dictionary data returned from the API.
        token (`str` or `bool`, *optional*):
            Authentication token for the Inference Endpoint, if set when requesting the API. Will default to the
            locally saved token if not provided. Pass `token=False` if you don't want to send your token to the server.

    Example:
        ```python
        >>> from huggingface_hub import get_inference_endpoint
        >>> endpoint = get_inference_endpoint("my-text-to-image")
        >>> endpoint
        InferenceEndpoint(name='my-text-to-image', ...)

        # Get status
        >>> endpoint.status
        'running'
        >>> endpoint.url
        'https://68a0f3b5c6ee5c9dcc0e0a01.endpoints.huggingface.cloud'

        # Run inference
        >>> endpoint.client.text_to_image(...)

        # Pause endpoint to save $$$
        >>> endpoint.pause()

        # ...
        # Resume and wait for deployment
        >>> endpoint.resume()
        >>> endpoint.wait()
        >>> endpoint.client.text_to_image(...)
        ```
    """

    # Field in __repr__
    name: str = field(init=False)
    namespace: str
    repository: str = field(init=False)
    status: InferenceEndpointStatus = field(init=False)
    health_route: str = field(init=False)
    url: str | None = field(init=False)

    # Other fields
    framework: str = field(repr=False, init=False)
    revision: str = field(repr=False, init=False)
    task: str = field(repr=False, init=False)
    created_at: datetime = field(repr=False, init=False)
    updated_at: datetime = field(repr=False, init=False)
    type: InferenceEndpointType = field(repr=False, init=False)

    # Raw dict from the API
    raw: dict = field(repr=False)

    # Internal fields
    _token: str | bool | None = field(repr=False, compare=False)
    _api: "HfApi" = field(repr=False, compare=False)

    @classmethod
    def from_raw(
        cls, raw: dict, namespace: str, token: str | bool | None = None, api: Optional["HfApi"] = None
    ) -> "InferenceEndpoint":
        """Initialize object from raw dictionary."""
        if api is None:
            from .hf_api import HfApi

            api = HfApi()
        if token is None:
            token = api.token

        # All other fields are populated in __post_init__
        return cls(raw=raw, namespace=namespace, _token=token, _api=api)

    def __post_init__(self) -> None:
        """Populate fields from raw dictionary."""
        self._populate_from_raw()

    @property
    def client(self) -> "InferenceClient":
        """Returns a client to make predictions on this Inference Endpoint.

        Returns:
            [`InferenceClient`]: an inference client pointing to the deployed endpoint.

        Raises:
            [`InferenceEndpointError`]: If the Inference Endpoint is not yet deployed.
        """
        if self.url is None:
            raise InferenceEndpointError(
                "Cannot create a client for this Inference Endpoint as it is not yet deployed. "
                "Please wait for the Inference Endpoint to be deployed using `endpoint.wait()` and try again."
            )
        from .inference._client import InferenceClient

        return InferenceClient(
            model=self.url,
            token=self._token,  # type: ignore # boolean token shouldn't be possible. In practice it's ok.
        )

    @property
    def async_client(self) -> "AsyncInferenceClient":
        """Returns a client to make predictions on this Inference Endpoint.

        Returns:
            [`AsyncInferenceClient`]: an asyncio-compatible inference client pointing to the deployed endpoint.

        Raises:
            [`InferenceEndpointError`]: If the Inference Endpoint is not yet deployed.
        """
        if self.url is None:
            raise InferenceEndpointError(
                "Cannot create a client for this Inference Endpoint as it is not yet deployed. "
                "Please wait for the Inference Endpoint to be deployed using `endpoint.wait()` and try again."
            )
        from .inference._generated._async_client import AsyncInferenceClient

        return AsyncInferenceClient(
            model=self.url,
            token=self._token,  # type: ignore # boolean token shouldn't be possible. In practice it's ok.
        )

    def wait(self, timeout: int | None = None, refresh_every: int = 5) -> "InferenceEndpoint":
        """Wait for the Inference Endpoint to be deployed.

        Information from the server will be fetched every 1s. If the Inference Endpoint is not deployed after `timeout`
        seconds, a [`InferenceEndpointTimeoutError`] will be raised. The [`InferenceEndpoint`] will be mutated in place with the latest
        data.

        Args:
            timeout (`int`, *optional*):
                The maximum time to wait for the Inference Endpoint to be deployed, in seconds. If `None`, will wait
                indefinitely.
            refresh_every (`int`, *optional*):
                The time to wait between each fetch of the Inference Endpoint status, in seconds. Defaults to 5s.

        Returns:
            [`InferenceEndpoint`]: the same Inference Endpoint, mutated in place with the latest data.

        Raises:
            [`InferenceEndpointError`]
                If the Inference Endpoint ended up in a failed state.
            [`InferenceEndpointTimeoutError`]
                If the Inference Endpoint is not deployed after `timeout` seconds.
        """
        if timeout is not None and timeout < 0:
            raise ValueError("`timeout` cannot be negative.")
        if refresh_every <= 0:
            raise ValueError("`refresh_every` must be positive.")

        start = time.time()
        while True:
            if self.status == InferenceEndpointStatus.FAILED:
                raise InferenceEndpointError(
                    f"Inference Endpoint {self.name} failed to deploy. Please check the logs for more information."
                )
            if self.status == InferenceEndpointStatus.UPDATE_FAILED:
                raise InferenceEndpointError(
                    f"Inference Endpoint {self.name} failed to update. Please check the logs for more information."
                )
            if self.status == InferenceEndpointStatus.RUNNING and self.url is not None:
                # Verify the endpoint is actually reachable
                _health_url = f"{self.url.rstrip('/')}/{self.health_route.lstrip('/')}"
                response = get_session().get(_health_url, headers=self._api._build_hf_headers(token=self._token))
                if response.status_code == 200:
                    logger.info("Inference Endpoint is ready to be used.")
                    return self

            if timeout is not None:
                if time.time() - start > timeout:
                    raise InferenceEndpointTimeoutError("Timeout while waiting for Inference Endpoint to be deployed.")
            logger.info(f"Inference Endpoint is not deployed yet ({self.status}). Waiting {refresh_every}s...")
            time.sleep(refresh_every)
            self.fetch()

    def fetch(self) -> "InferenceEndpoint":
        """Fetch latest information about the Inference Endpoint.

        Returns:
            [`InferenceEndpoint`]: the same Inference Endpoint, mutated in place with the latest data.
        """
        obj = self._api.get_inference_endpoint(name=self.name, namespace=self.namespace, token=self._token)  # type: ignore [arg-type]
        self.raw = obj.raw
        self._populate_from_raw()
        return self

    def update(
        self,
        *,
        # Compute update
        accelerator: str | None = None,
        instance_size: str | None = None,
        instance_type: str | None = None,
        min_replica: int | None = None,
        max_replica: int | None = None,
        scale_to_zero_timeout: int | None = None,
        # Model update
        repository: str | None = None,
        framework: str | None = None,
        revision: str | None = None,
        task: str | None = None,
        custom_image: dict | None = None,
        container_command: list[str] | None = None,
        container_args: list[str] | None = None,
        tensor_parallel_size: int | None = None,
        data_parallel_size: int | None = None,
        secrets: dict[str, str] | None = None,
    ) -> "InferenceEndpoint":
        """Update the Inference Endpoint.

        This method allows the update of either the compute configuration, the deployed model, or both. All arguments are
        optional but at least one must be provided.

        This is an alias for [`HfApi.update_inference_endpoint`]. The current object is mutated in place with the
        latest data from the server.

        Args:
            accelerator (`str`, *optional*):
                The hardware accelerator to be used for inference (e.g. `"cpu"`).
            instance_size (`str`, *optional*):
                The size or type of the instance to be used for hosting the model (e.g. `"x4"`).
            instance_type (`str`, *optional*):
                The cloud instance type where the Inference Endpoint will be deployed (e.g. `"intel-icl"`).
            min_replica (`int`, *optional*):
                The minimum number of replicas (instances) to keep running for the Inference Endpoint.
            max_replica (`int`, *optional*):
                The maximum number of replicas (instances) to scale to for the Inference Endpoint.
            scale_to_zero_timeout (`int`, *optional*):
                The duration in minutes before an inactive endpoint is scaled to zero.

            repository (`str`, *optional*):
                The name of the model repository associated with the Inference Endpoint (e.g. `"gpt2"`).
            framework (`str`, *optional*):
                The machine learning framework used for the model (e.g. `"custom"`).
            revision (`str`, *optional*):
                The specific model revision to deploy on the Inference Endpoint (e.g. `"6c0e6080953db56375760c0471a8c5f2929baf11"`).
            task (`str`, *optional*):
                The task on which to deploy the model (e.g. `"text-classification"`).
            custom_image (`dict`, *optional*):
                The container image to run. Either a dict keyed by image variant (e.g.
                `{"vLLM": {"url": "vllm/vllm-openai:v0.23.0", "port": 8000}}`, also `sGLang`, `tgi`, `tei`,
                `llamacpp`, `hfServe`, ...), which is forwarded as-is, or a flat dict describing a custom
                container (e.g. `{"url": ..., "port": ...}`), which is sent as `{"custom": ...}`.
            container_command (`list[str]`, *optional*):
