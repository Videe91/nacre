"""
Functionality: Work out the contributors of a derived record from its source events, and get or create the
  contributor-set key for them.
Owns: resolving source events to (subject, month) contributors (recursively through derived keys), the 256-member
  cap (fail closed), the derived key's identity, and recording key membership.
Public entry: contributors_of(), derived_key(), Contributor, ContributorError, ContributorCapExceeded, MAX_CONTRIBUTORS
Decisions: D-0023, D-0004
Assumptions: A-0030, A-0031
Notes: D-0023 (owner-accepted): a derived record is encrypted under one key per (stream, contributor set, month),
  stored as an ordinary keys.data_keys row (subject = uuid5 of the canonical set), so D-0014 rotation needs no change.
  Contributors = the data-key subject and month of each source event; a source encrypted under a derived key passes
  on that key's members (the union), so derivations of derivations still name every person. The stream's system
  subject is a member too (non-person), so forgetting a month reaches derived copies of system content.
  Fail closed: a source that is missing, outside the stream, or already shredded (its key is gone) cannot be derived
  from (ContributorError), and more than MAX_CONTRIBUTORS members is refused (ContributorCapExceeded). There is never
  a fallback to the stream key.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import psycopg

from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.get_or_create_key import DataKey, get_or_create_key

MAX_CONTRIBUTORS = 256
_NS_DERIVED = uuid.UUID("9d2f7a1e-3c4b-4d5e-8f6a-7b8c9d0e1f2a")


class ContributorError(ValueError):
    """The sources cannot be resolved to contributors (missing, foreign or shredded)."""


class ContributorCapExceeded(ContributorError):
    """More than MAX_CONTRIBUTORS contributors: the derived write is refused (D-0023 decision 4)."""


@dataclass(frozen=True, order=True)
class Contributor:
    subject: UUID
    month: date
    is_person: bool


def contributors_of(conn: psycopg.Connection, stream_id: UUID, event_ids: list[UUID]) -> frozenset[Contributor]:
    """The contributors of `event_ids` (all must be committed, readable events of `stream_id` with live keys)."""
    ids = sorted(set(event_ids))
    rows = conn.execute("SELECT e.event_id, e.key_id, d.subject_id, d.month FROM ledger.events e "
                        "LEFT JOIN keys.data_keys d ON d.key_id = e.key_id "
                        "WHERE e.stream_id = %s AND e.event_id = ANY(%s)", (stream_id, ids)).fetchall()
    if len(rows) != len(ids):
        raise ContributorError("every source must be a committed, readable event of this stream")
    out = set()
    for _event, key_id, subject, month in rows:
        if subject is None:
            raise ContributorError("a source's key has been destroyed: shredded content cannot be derived from")
        members = conn.execute("SELECT member_subject, member_month, member_is_person FROM keys.key_contributors "
                               "WHERE key_id = %s", (key_id,)).fetchall()
        if members:
            out.update(Contributor(s, m, p) for s, m, p in members)
        else:
            out.add(Contributor(subject, month, subject != stream_id))
    return frozenset(out)


def derived_key(conn: psycopg.Connection, provider: RootKeyProvider, stream_id: UUID,
                contributors: frozenset[Contributor], month: date) -> DataKey:
    """The contributor-set key of `stream_id` for `contributors` in `month` (created and registered if new)."""
    if not contributors:
        raise ContributorError("a derived key needs at least one contributor")
    if len(contributors) > MAX_CONTRIBUTORS:
        raise ContributorCapExceeded(f"{len(contributors)} contributors > {MAX_CONTRIBUTORS}: derived write refused")
    canonical = "|".join(f"{c.subject}:{c.month.isoformat()}" for c in sorted(contributors))
    subject = uuid.uuid5(_NS_DERIVED, f"{stream_id}|{canonical}")
    key = get_or_create_key(conn, provider, stream_id, subject, month)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO keys.key_contributors (key_id, stream_id, member_subject, member_month, member_is_person) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(key.key_id, stream_id, c.subject, c.month, c.is_person) for c in sorted(contributors)])
    return key
