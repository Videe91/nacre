"""
Functionality: The VerifiedClaims value type and the authority-bearing-section reading of a write (types and one pure
  helper only).
Owns: nothing but the shape shared by interface/check_claims.py (the ONLY constructor) and ledger/append_event.py (which
  writes trust_basis = verified only when a VerifiedClaims describes the request exactly).
Public entry: VerifiedClaims, authority_sections()
Decisions: D-0026, D-0012, D-0018
Assumptions: A-0040
Notes: Lives in core/ so the ledger never imports the interface layer. A structure test asserts that only
  src/nacre/interface/check_claims.py constructs VerifiedClaims.
  authority_sections(): (has_correction, has_failing_evaluation) for a write: a `correction` event, or an outcome whose
  sections include role `correction`; and an outcome with success = false holding an `evaluation` section. These are
  exactly the sections capture/section_authority.py may treat as authoritative (D-0018).
"""
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class VerifiedClaims:
    principal_id: UUID
    stream_id: UUID
    source: str
    authorship: str
    actor_kind: str
    payload_structured: bool
    has_correction: bool
    has_failing_evaluation: bool
    on_behalf_of: UUID | None
    authoritative_allowed: bool


def authority_sections(event_type: str, content) -> tuple[bool, bool]:
    if event_type == "correction":
        return True, False
    if event_type != "outcome" or not isinstance(content, dict):
        return False, False
    roles = {s.get("role") for s in content.get("sections", []) if isinstance(s, dict)}
    return "correction" in roles, ("evaluation" in roles and content.get("success") is False)
