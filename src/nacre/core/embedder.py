"""
Functionality: The embedder interface (Protocol only): turn texts into unit-normalised float32 vectors.
Owns: the contract every embedder meets; no logic.
Public entry: Embedder
Decisions: D-0024
Assumptions: A-0034
Notes: `embedder_id` names the exact model (name@revision#weights-sha256). It is recorded on every index entry and
  every ContextFrame, and a generation of the index holds one embedder only, so vectors from different models are
  never compared (D-0024 owner decision 4). `embed` returns a numpy array of shape (len(texts), dim), dtype float32,
  each row of unit L2 norm; an empty input returns shape (0, dim).
"""
from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    embedder_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> "numpy.ndarray": ...  # noqa: F821
