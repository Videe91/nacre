# Copyright 2024 The HuggingFace Team. All rights reserved.
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
"""Contains pytorch-specific helpers."""

import importlib
import importlib.util
import json
import os
import re
from collections import defaultdict, namedtuple
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, NamedTuple, Union

from packaging import version

from .. import constants, logging
from ._base import MAX_SHARD_SIZE, StateDictSplit, split_state_dict_into_shards_factory


logger = logging.get_logger(__file__)

SAFETENSORS_EXTENSION = ".safetensors"

# A checkpoint index is plain metadata (a tensor name -> shard file mapping). 10 MB is already far beyond
# anything a real checkpoint produces, so anything bigger is not worth parsing into memory.
MAX_INDEX_FILE_SIZE = 10 * 1024 * 1024

if TYPE_CHECKING:
    import torch

# SAVING


def save_torch_model(
    model: "torch.nn.Module",
    save_directory: str | Path,
    *,
    filename_pattern: str | None = None,
    force_contiguous: bool = True,
    max_shard_size: int | str = MAX_SHARD_SIZE,
    metadata: dict[str, str] | None = None,
    safe_serialization: bool = True,
    is_main_process: bool = True,
    shared_tensors_to_discard: list[str] | None = None,
):
    """
    Saves a given torch model to disk, handling sharding and shared tensors issues.

    See also [`save_torch_state_dict`] to save a state dict with more flexibility.

    For more information about tensor sharing, check out [this guide](https://huggingface.co/docs/safetensors/torch_shared_tensors).

    The model state dictionary is split into shards so that each shard is smaller than a given size. The shards are
    saved in the `save_directory` with the given `filename_pattern`. If the model is too big to fit in a single shard,
    an index file is saved in the `save_directory` to indicate where each tensor is saved. This helper uses
    [`split_torch_state_dict_into_shards`] under the hood. If `safe_serialization` is `True`, the shards are saved as
    safetensors (the default). Otherwise, the shards are saved as pickle.

    Before saving the model, the `save_directory` is cleaned from any previous shard files.

    > [!WARNING]
    > If one of the model's tensor is bigger than `max_shard_size`, it will end up in its own shard which will have a
    > size greater than `max_shard_size`.

    > [!WARNING]
    > If your model is a `transformers.PreTrainedModel`, you should pass `model._tied_weights_keys` as `shared_tensors_to_discard` to properly handle shared tensors saving. This ensures the correct duplicate tensors are discarded during saving.

    Args:
        model (`torch.nn.Module`):
            The model to save on disk.
        save_directory (`str` or `Path`):
            The directory in which the model will be saved.
        filename_pattern (`str`, *optional*):
            The pattern to generate the files names in which the model will be saved. Pattern must be a string that
            can be formatted with `filename_pattern.format(suffix=...)` and must contain the keyword `suffix`
            Defaults to `"model{suffix}.safetensors"` or `pytorch_model{suffix}.bin` depending on `safe_serialization`
            parameter.
        force_contiguous (`boolean`, *optional*):
            Forcing the state_dict to be saved as contiguous tensors. This has no effect on the correctness of the
            model, but it could potentially change performance if the layout of the tensor was chosen specifically for
            that reason. Defaults to `True`.
        max_shard_size (`int` or `str`, *optional*):
            The maximum size of each shard, in bytes. Defaults to 5GB.
        metadata (`dict[str, str]`, *optional*):
            Extra information to save along with the model. Some metadata will be added for each dropped tensors.
            This information will not be enough to recover the entire shared structure but might help understanding
            things.
        safe_serialization (`bool`, *optional*):
            Whether to save as safetensors, which is the default behavior. If `False`, the shards are saved as pickle.
            Safe serialization is recommended for security reasons. Saving as pickle is deprecated and will be removed
            in a future version.
        is_main_process (`bool`, *optional*):
            Whether the process calling this is the main process or not. Useful when in distributed training like
            TPUs and need to call this function from all processes. In this case, set `is_main_process=True` only on
            the main process to avoid race conditions. Defaults to True.
        shared_tensors_to_discard (`list[str]`, *optional*):
            List of tensor names to drop when saving shared tensors. If not provided and shared tensors are
            detected, it will drop the first name alphabetically.

    Example:

    ```py
    >>> from huggingface_hub import save_torch_model
    >>> model = ... # A PyTorch model

    # Save state dict to "path/to/folder". The model will be split into shards of 5GB each and saved as safetensors.
    >>> save_torch_model(model, "path/to/folder")

    # Load model back
    >>> from huggingface_hub import load_torch_model  # TODO
    >>> load_torch_model(model, "path/to/folder")
    >>>
    ```
    """
    save_torch_state_dict(
        state_dict=model.state_dict(),
        filename_pattern=filename_pattern,
        force_contiguous=force_contiguous,
        max_shard_size=max_shard_size,
        metadata=metadata,
        safe_serialization=safe_serialization,
        save_directory=save_directory,
        is_main_process=is_main_process,
        shared_tensors_to_discard=shared_tensors_to_discard,
    )


def save_torch_state_dict(
    state_dict: dict[str, "torch.Tensor"],
    save_directory: str | Path,
    *,
    filename_pattern: str | None = None,
    force_contiguous: bool = True,
    max_shard_size: int | str = MAX_SHARD_SIZE,
    metadata: dict[str, str] | None = None,
    safe_serialization: bool = True,
    is_main_process: bool = True,
    shared_tensors_to_discard: list[str] | None = None,
) -> None:
    """
    Save a model state dictionary to the disk, handling sharding and shared tensors issues.

    See also [`save_torch_model`] to directly save a PyTorch model.

    For more information about tensor sharing, check out [this guide](https://huggingface.co/docs/safetensors/torch_shared_tensors).

    The model state dictionary is split into shards so that each shard is smaller than a given size. The shards are
    saved in the `save_directory` with the given `filename_pattern`. If the model is too big to fit in a single shard,
    an index file is saved in the `save_directory` to indicate where each tensor is saved. This helper uses
    [`split_torch_state_dict_into_shards`] under the hood. If `safe_serialization` is `True`, the shards are saved as
    safetensors (the default). Otherwise, the shards are saved as pickle.

    Before saving the model, the `save_directory` is cleaned from any previous shard files.

    > [!WARNING]
    > If one of the model's tensor is bigger than `max_shard_size`, it will end up in its own shard which will have a
    > size greater than `max_shard_size`.

    > [!WARNING]
    > If your model is a `transformers.PreTrainedModel`, you should pass `model._tied_weights_keys` as `shared_tensors_to_discard` to properly handle shared tensors saving. This ensures the correct duplicate tensors are discarded during saving.

    Args:
        state_dict (`dict[str, torch.Tensor]`):
            The state dictionary to save.
        save_directory (`str` or `Path`):
            The directory in which the model will be saved.
        filename_pattern (`str`, *optional*):
            The pattern to generate the files names in which the model will be saved. Pattern must be a string that
            can be formatted with `filename_pattern.format(suffix=...)` and must contain the keyword `suffix`
            Defaults to `"model{suffix}.safetensors"` or `pytorch_model{suffix}.bin` depending on `safe_serialization`
            parameter.
        force_contiguous (`boolean`, *optional*):
            Forcing the state_dict to be saved as contiguous tensors. This has no effect on the correctness of the
            model, but it could potentially change performance if the layout of the tensor was chosen specifically for
            that reason. Defaults to `True`.
        max_shard_size (`int` or `str`, *optional*):
            The maximum size of each shard, in bytes. Defaults to 5GB.
        metadata (`dict[str, str]`, *optional*):
            Extra information to save along with the model. Some metadata will be added for each dropped tensors.
            This information will not be enough to recover the entire shared structure but might help understanding
            things.
        safe_serialization (`bool`, *optional*):
            Whether to save as safetensors, which is the default behavior. If `False`, the shards are saved as pickle.
            Safe serialization is recommended for security reasons. Saving as pickle is deprecated and will be removed
            in a future version.
        is_main_process (`bool`, *optional*):
            Whether the process calling this is the main process or not. Useful when in distributed training like
            TPUs and need to call this function from all processes. In this case, set `is_main_process=True` only on
            the main process to avoid race conditions. Defaults to True.
        shared_tensors_to_discard (`list[str]`, *optional*):
            List of tensor names to drop when saving shared tensors. If not provided and shared tensors are
            detected, it will drop the first name alphabetically.

    Example:

    ```py
    >>> from huggingface_hub import save_torch_state_dict
    >>> model = ... # A PyTorch model

    # Save state dict to "path/to/folder". The model will be split into shards of 5GB each and saved as safetensors.
    >>> state_dict = model_to_save.state_dict()
    >>> save_torch_state_dict(state_dict, "path/to/folder")
    ```
    """
    save_directory = str(save_directory)

    if filename_pattern is None:
        filename_pattern = (
            constants.SAFETENSORS_WEIGHTS_FILE_PATTERN
            if safe_serialization
            else constants.PYTORCH_WEIGHTS_FILE_PATTERN
        )

    if metadata is None:
        metadata = {}
    if safe_serialization:
        try:
            from safetensors.torch import save_file as save_file_fn
        except ImportError as e:
            raise ImportError(
                "Please install `safetensors` to use safe serialization. "
                "You can install it with `pip install safetensors`."
            ) from e
        # Clean state dict for safetensors
        state_dict = _clean_state_dict_for_safetensors(
            state_dict,
            metadata,
            force_contiguous=force_contiguous,
            shared_tensors_to_discard=shared_tensors_to_discard,
        )
    else:
        from torch import save as save_file_fn  # type: ignore[assignment, no-redef]

        logger.warning(
            "You are using unsafe serialization. Due to security reasons, it is recommended not to load "
            "pickled models from untrusted sources. If you intend to share your model, we strongly recommend "
            "using safe serialization by installing `safetensors` with `pip install safetensors`."
        )
    # Split dict
    state_dict_split = split_torch_state_dict_into_shards(
        state_dict, filename_pattern=filename_pattern, max_shard_size=max_shard_size
    )

    # Only main process should clean up existing files to avoid race conditions in distributed environment
    if is_main_process:
        # Escape literal parts and use `fullmatch` so that e.g. "model.safetensors.backup" is not deleted.
        prefix, _, suffix = filename_pattern.partition("{suffix}")
        existing_files_regex = re.compile(
            re.escape(prefix) + r"(-\d{5}-of-\d{5})?" + re.escape(suffix) + r"(\.index\.json)?"
        )
        for filename in os.listdir(save_directory):
            if existing_files_regex.fullmatch(filename):
                try:
                    logger.debug(f"Removing existing file '{filename}' from folder.")
                    os.remove(os.path.join(save_directory, filename))
                except Exception as e:
                    logger.warning(
                        f"Error when trying to remove existing '{filename}' from folder: {e}. Continuing..."
                    )

    # Save each shard
    per_file_metadata = {"format": "pt"}
    if not state_dict_split.is_sharded:
        per_file_metadata.update(metadata)
    safe_file_kwargs = {"metadata": per_file_metadata} if safe_serialization else {}
    for filename, tensors in state_dict_split.filename_to_tensors.items():
        shard = {tensor: state_dict[tensor] for tensor in tensors}
        save_file_fn(shard, os.path.join(save_directory, filename), **safe_file_kwargs)  # ty: ignore[invalid-argument-type]
        logger.debug(f"Shard saved to {filename}")

    # Save the index (if any)
    if state_dict_split.is_sharded:
        index_path = filename_pattern.format(suffix="") + ".index.json"
        index = {
            "metadata": {**state_dict_split.metadata, **metadata},
            "weight_map": state_dict_split.tensor_to_filename,
        }
        with open(os.path.join(save_directory, index_path), "w") as f:
            json.dump(index, f, indent=2)
        logger.info(
            f"The model is bigger than the maximum size per checkpoint ({max_shard_size}). "
            f"Model weighs have been saved in {len(state_dict_split.filename_to_tensors)} checkpoint shards. "
            f"You can find where each parameters has been saved in the index located at {index_path}."
        )

    logger.info(f"Model weights successfully saved to {save_directory}!")


def split_torch_state_dict_into_shards(
    state_dict: dict[str, "torch.Tensor"],
    *,
    filename_pattern: str = constants.SAFETENSORS_WEIGHTS_FILE_PATTERN,
    max_shard_size: int | str = MAX_SHARD_SIZE,
) -> StateDictSplit:
    """
    Split a model state dictionary in shards so that each shard is smaller than a given size.

    The shards are determined by iterating through the `state_dict` in the order of its keys. There is no optimization
    made to make each shard as close as possible to the maximum size passed. For example, if the limit is 10GB and we
    have tensors of sizes [6GB, 6GB, 2GB, 6GB, 2GB, 2GB] they will get sharded as [6GB], [6+2GB], [6+2+2GB] and not
    [6+2+2GB], [6+2GB], [6GB].


    > [!TIP]
    > To save a model state dictionary to the disk, see [`save_torch_state_dict`]. This helper uses
    > `split_torch_state_dict_into_shards` under the hood.

    > [!WARNING]
    > If one of the model's tensor is bigger than `max_shard_size`, it will end up in its own shard which will have a
    > size greater than `max_shard_size`.

    Args:
        state_dict (`dict[str, torch.Tensor]`):
            The state dictionary to save.
        filename_pattern (`str`, *optional*):
            The pattern to generate the files names in which the model will be saved. Pattern must be a string that
            can be formatted with `filename_pattern.format(suffix=...)` and must contain the keyword `suffix`
            Defaults to `"model{suffix}.safetensors"`.
        max_shard_size (`int` or `str`, *optional*):
            The maximum size of each shard, in bytes. Defaults to 5GB.

    Returns:
        [`StateDictSplit`]: A `StateDictSplit` object containing the shards and the index to retrieve them.

    Example:
    ```py
