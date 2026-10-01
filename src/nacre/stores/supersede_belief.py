"""
Functionality: Supersede a contested belief by a different, active belief that the counter-evidence supports.
Owns: the supersession preconditions, the shared-decision rule (>= 2 decisions both contradicting the old belief and
  supporting the replacement), the `superseded` version with its `superseded_by` edge, and retry semantics.
Public entry: supersede_belief(), SupersessionError, SHARED_QUORUM
Decisions: D-0017
Assumptions: A-0027
Notes: MNEXA ledger 39 rules: the old head must be contested; the replacement is a different proposition (different
  key), currently active and promoted by its own evidence; a retry with the same replacement returns the head; a
  different replacement after supersession is an error. Nothing is written on the replacement (recall shows it and
  hides the superseded belief).
"""
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.promote_if_supported import Promotion
from nacre.stores.write_version import Edge, VersionRecord, edges_from_body, read_version_events, write_version

SHARED_QUORUM = 2


class SupersessionError(ValueError):
    """Supersession is not allowed in this state."""


def supersede_belief(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, old_object_id: UUID,
                     replacement_object_id: UUID, *, cycle_id: UUID | None = None) -> Promotion | None:
    """Write a superseded version of `old_object_id`; None when the shared counter-evidence is not enough."""
    versions = read_version_events(session, key_provider, stream_id)

    def head(obj):
        hs = [v for v in versions if v.body["content"]["object_id"] == str(obj) and v.body["content"]["kind"] == "belief"]
        return (hs[-1].body["content"], hs[-1].envelope) if hs else (None, None)
    old, old_env = head(old_object_id)
    new, _ = head(replacement_object_id)
    if old is None or new is None:
        raise SupersessionError("both beliefs must exist in this stream")
    if old["status"] == "superseded":
        if old["content"].get("superseded_by") == str(replacement_object_id):
            return Promotion(old_object_id, old["version"], "superseded", old["support"], False)
        raise SupersessionError("belief already superseded by a different replacement")
    if old["status"] != "contested":
        raise SupersessionError("old belief must be contested before supersession")
    if old["content"]["key"] == new["content"]["key"]:
        raise SupersessionError("replacement must be a different proposition")
    if new["status"] != "active":
        return None
    shared = set(old["content"].get("contradiction_decisions", [])) & set(new["content"]["support_decisions"])
    if len(shared) < SHARED_QUORUM:
        return None
    content = dict(old["content"], superseded_by=str(replacement_object_id), superseded_by_version=new["version"],
                   shared_decisions=sorted(shared))
    edges = edges_from_body(old["edges"]) + [Edge("superseded_by", target_object_id=replacement_object_id, target_version=new["version"])]
    write_version(session, key_provider, stream_id, VersionRecord(
        old_object_id, old["version"] + 1, "belief", "superseded", old["support"], content, tuple(edges)), cycle_id=cycle_id,
        carried_from=old_env.event_id)
    return Promotion(old_object_id, old["version"] + 1, "superseded", old["support"], True)
