"""
Build the frozen routine/adversarial episode set v2 (routine_episodes_v2.json).

Built by a separate session that was blind to the write-gate code, its tests, the v1
episode set and its generator, and the gate-scoring ADR. The data describe software and
IT operations work (database maintenance, mobile releases, infrastructure plans,
data-quality checks, accessibility audits, dependency upgrades, certificate renewals,
backup restores, feature-flag rollouts, documentation builds, scheduled jobs, CI caching).

Classes:
  routine               200 episodes; correct, specific predictions (60 expected failures)
  adversarial_vague      25 episodes; failure predicted but no check named
  adversarial_mismatch   25 episodes; named check A, outcome fails B (15) or A and B (10)
  adversarial_late       25 episodes; correct check, but prediction recorded after the action

Deterministic: stdlib only, random.Random(SEED). Running twice yields identical bytes.
Usage: python3 scripts/make_routine_episodes_v2.py
Writes tests/regression/routine/routine_episodes_v2.json and its .sha256.
"""
import hashlib
import json
import random
from pathlib import Path

SEED = 730214
ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "tests" / "regression" / "routine" / "routine_episodes_v2.json"
OUT_SHA = ROOT / "tests" / "regression" / "routine" / "routine_episodes_v2.sha256"

N_ROUTINE = 200
N_ROUTINE_FAIL = 60
N_ADV = 25
N_MISMATCH_EXTRA = 10  # of the 25 mismatch episodes, outcome lists [A, B]

# Each domain: subjects, pass items and fail items.
# pass item: (decision, check, action_kind, action_desc, expectation, pass_text)
# fail item: (decision, check, action_kind, action_desc, reason, fail_text)
# "{s}" is replaced with a subject from the domain.
DOMAINS = [
    {
        "subjects": ["the inventory_snapshots table", "the shipment_events partitions",
                     "the customer_notes table", "the pricing_history table"],
        "pass": [
            ("Run the scheduled vacuum and analyze on {s} in tonight's quiet window.",
             "test_vacuum_preserves_row_counts", "run maintenance job",
             "Started vacuum and analyze on {s} under the maintenance role.",
             "row counts stay identical and dead tuples drop",
             "Row counts before and after match; dead tuples on {s} fell sharply."),
            ("Apply the additive migration that adds a nullable region column to {s}.",
             "migration-lint", "apply migration",
             "Applied the additive migration to the staging copy of {s}.",
             "the migration is additive and has no table rewrite",
             "migration-lint found no blocking operations; no table rewrite was detected."),
            ("Rebuild the bloated secondary index on {s} concurrently.",
             "test_reindex_completes_within_window", "rebuild index",
             "Triggered a concurrent index rebuild on {s}.",
             "the concurrent rebuild finishes inside the forty minute window",
             "The rebuild finished in about twenty-six minutes, inside the window."),
            ("Compare the live schema of {s} against the declared schema before the release.",
             "schema-drift-check", None, None,
             "the declared and live schemas agree",
             "No drift between the declared and live schema of {s}."),
        ],
        "fail": [
            ("Write a regression test showing the nightly reindex skips partial indexes on {s}, before changing the reindex script.",
             "test_reindex_covers_partial_indexes", "add failing regression test",
             "Committed the new test against the unpatched reindex script.",
             "the current script still skips partial indexes",
             "The test failed as intended: two partial indexes on {s} were left untouched."),
            ("Run the migration guard against a deliberately destructive migration that drops a column from {s}.",
             "migration-lint", "submit guard probe",
             "Pushed a throwaway branch with the column-dropping migration for {s}.",
             "the guard must reject column drops without a deprecation step",
             "migration-lint rejected the change: dropping a column without a prior deprecation step is blocked."),
            ("Add a test proving the archive job loses rows when {s} has duplicate timestamps, then fix it.",
             "test_archive_keeps_duplicate_timestamp_rows", "add failing regression test",
             "Added the reproduction test with two rows sharing one timestamp.",
             "the archive cursor compares timestamps with a strict greater-than",
             "The test failed: one of the two rows sharing a timestamp was not archived."),
        ],
    },
    {
        "subjects": ["the Android release 4.12", "the iOS release 7.3", "the tablet build of the field app",
                     "the beta channel build"],
        "pass": [
            ("Cut the release candidate for {s} from the stabilised branch.",
             "test_onboarding_screen_renders", "trigger release build",
             "Started the release candidate pipeline for {s}.",
             "the onboarding flow renders on all three device profiles",
             "Onboarding screen tests passed on all device profiles."),
            ("Check {s} against the download size budget before submission.",
             "app-size-budget", "run size report",
             "Ran the size report on the signed artifact of {s}.",
             "the image compression change keeps the bundle under budget",
             "Bundle is under the size budget with room to spare."),
            ("Validate the privacy manifest for {s} after the analytics library bump.",
             "lint:privacy-manifest", None, None,
             "every data category the analytics library uses is declared",
             "All data categories are declared; the manifest lint is clean."),
            ("Run the offline sync suite on {s} before promoting it to the store track.",
             "test_offline_queue_flushes_on_reconnect", "run device suite",
             "Dispatched the offline sync suite to the device farm for {s}.",
             "queued edits flush once the network returns",
             "Queued edits flushed on reconnect on every device in the farm."),
        ],
        "fail": [
            ("Reproduce the crash reported for {s} when the camera permission is revoked mid-scan, with a failing UI test first.",
             "test_scanner_handles_revoked_permission", "add failing regression test",
             "Added the UI test that revokes camera permission during a scan on {s}.",
             "the scanner does not observe permission changes yet",
             "The UI test failed with the same crash seen in the field reports."),
            ("Feed the size budget check an intentionally unoptimised asset pack for {s} to confirm it blocks.",
             "app-size-budget", "submit guard probe",
             "Built {s} with the uncompressed asset pack on a scratch branch.",
             "the uncompressed pack pushes the bundle well over the limit",
             "app-size-budget blocked the build: the bundle exceeded the budget by a wide margin."),
            ("Add a test showing that {s} shows the wrong currency symbol for the Swiss locale before fixing the formatter.",
             "test_price_label_uses_locale_currency", "add failing regression test",
             "Committed the locale test ahead of the formatter fix.",
             "the formatter ignores the region part of the locale",
             "The test failed: the price label showed the default symbol instead of the Swiss one."),
        ],
    },
    {
        "subjects": ["the staging network module", "the shared storage module", "the queue workers module",
                     "the reporting cluster stack"],
        "pass": [
            ("Validate the plan for {s} after renaming input variables.",
             "tf-validate", "run plan",
             "Ran validate and plan for {s} in the staging workspace.",
             "the rename is purely cosmetic and validates cleanly",
             "Validation passed and the plan shows no resource changes."),
            ("Apply the tag-only change to {s}.",
             "plan-diff-guard", "apply infrastructure change",
             "Applied the tagging change to {s}.",
             "the plan touches tags only, so the diff guard allows it",
             "plan-diff-guard allowed the change: only tags were modified."),
            ("Run the storage policy checks against {s} after adding a log bucket.",
             "policy:no-public-buckets", None, None,
             "the new log bucket is private by default",
             "No public buckets found; the new log bucket is private."),
            ("Upgrade the provider plugin pin used by {s} by one minor version.",
             "test_module_outputs_unchanged", "run plan",
             "Planned {s} with the new provider pin.",
             "module outputs stay identical across the minor upgrade",
             "Module outputs are identical before and after the provider bump."),
        ],
        "fail": [
            ("Dry-run the subnet resize for {s} so the diff guard reports the replacement before anyone applies it.",
             "plan-diff-guard", "run plan",
             "Ran the plan for the subnet resize on {s} without applying.",
             "a subnet resize forces replacement, which the guard refuses",
             "plan-diff-guard flagged the plan: three resources would be destroyed and recreated."),
            ("Probe the policy check with a bucket in {s} that is intentionally marked public.",
             "policy:no-public-buckets", "submit guard probe",
             "Opened a scratch change adding a public bucket to {s}.",
             "the policy must refuse any public bucket",
             "policy:no-public-buckets rejected the change and named the public bucket."),
            ("Write a test proving that {s} drops the retention setting when the optional input is omitted, then fix the default.",
             "test_retention_default_applied", "add failing regression test",
             "Added the module test that omits the optional retention input.",
             "the default is never wired into the resource block",
             "The test failed: retention came out unset when the input was omitted."),
        ],
    },
    {
        "subjects": ["the nightly shipment feed", "the supplier price import", "the warehouse events stream",
                     "the store visits extract"],
        "pass": [
            ("Run the data-quality suite on {s} after the upstream schema change.",
             "dq:null-ratio", "run data-quality suite",
             "Ran the null-ratio and range checks on today's load of {s}.",
             "the null ratio stays below the two percent threshold",
             "Null ratio is well below the threshold on every monitored column."),
            ("Enable deduplication on {s} using the composite natural key.",
             "test_dedupe_drops_exact_duplicates", "deploy pipeline change",
             "Deployed the dedupe step for {s}.",
             "exact duplicates are dropped and distinct rows are kept",
             "Exact duplicates were removed and every distinct row survived."),
            ("Confirm freshness of {s} after moving its schedule an hour earlier.",
             "freshness-check", None, None,
             "the load lands before the reporting cutoff",
             "Freshness is within limits; the load landed before the cutoff."),
            ("Backfill last week's partitions of {s} after the parser fix.",
             "test_backfill_row_parity", "run backfill",
             "Started the seven-day backfill for {s}.",
             "backfilled row counts match the source counts per day",
             "Per-day row counts match the source for all seven partitions."),
        ],
        "fail": [
            ("Point the range check at a sample of {s} with known negative quantities to confirm it catches them.",
             "dq:quantity-range", "submit guard probe",
             "Loaded the sample with negative quantities into the scratch schema.",
             "negative quantities are outside the allowed range",
             "dq:quantity-range failed and listed the rows with negative quantities."),
            ("Add a reproduction test for the timezone shift that moves late-evening rows of {s} into the next day.",
             "test_partition_uses_source_timezone", "add failing regression test",
             "Committed the timezone reproduction test before the fix.",
             "partitioning still uses the server timezone",
             "The test failed: late-evening rows landed in the following day's partition."),
            ("Run the schema contract check on {s} against the supplier's new file that renames a column, expecting a block.",
             "contract:column-names", None, None,
             "the supplier renamed a required column without notice",
             "contract:column-names failed because a required column is missing under its old name."),
        ],
    },
    {
        "subjects": ["the checkout form", "the account settings page", "the onboarding wizard",
                     "the order history table"],
        "pass": [
            ("Audit {s} for colour contrast after the palette refresh.",
             "lint:a11y-contrast", "run accessibility audit",
             "Ran the contrast audit across {s} in both themes.",
             "every text pair meets the AA ratio",
             "All text and background pairs meet the AA ratio in both themes."),
            ("Add visible labels to the inputs on {s} and rerun the label audit.",
             "axe:label-missing", "run accessibility audit",
             "Ran the automated label audit on {s}.",
             "every input now has an associated label",
             "No unlabelled inputs remain on {s}."),
            ("Check keyboard focus order in the confirmation dialog on {s}.",
             "test_focus_order_modal", None, None,
             "focus moves from title to actions and is trapped inside the dialog",
             "Focus order is correct and stays trapped in the dialog."),
            ("Review heading structure on {s} after the layout rework.",
             "lint:a11y-heading-order", "request review",
             "Asked the accessibility reviewer to check {s}.",
             "headings descend one level at a time",
             "Reviewer confirmed headings descend without skipped levels."),
        ],
        "fail": [
            ("Write a failing test showing the error message on {s} is not announced to screen readers, before adding the live region.",
             "test_error_message_announced", "add failing regression test",
             "Committed the announcement test ahead of the live-region change.",
             "the error container has no live region",
             "The test failed: no announcement was emitted when the error appeared."),
            ("Run the contrast lint on the disabled-button style of {s}, which design already flagged as too faint.",
             "lint:a11y-contrast", "run accessibility audit",
             "Ran the contrast lint against the disabled-button style.",
             "the disabled grey sits below the AA ratio",
             "lint:a11y-contrast failed on the disabled button text."),
            ("Add a test that tabs through {s} with the date picker open, reproducing the focus trap bug.",
             "test_date_picker_releases_focus", "add failing regression test",
             "Added the keyboard test that opens the date picker and tabs onward.",
             "the picker never returns focus to the page",
             "The test failed: focus stayed stuck inside the date picker."),
        ],
    },
    {
        "subjects": ["the date parsing library", "the web request library", "the YAML loader",
                     "the image resizing library"],
        "pass": [
            ("Bump {s} by one patch version to pick up the upstream bug fix.",
             "test_parse_iso_week_dates", "update dependency",
             "Updated the lockfile with the patched release of {s}.",
             "the patch release is backward compatible",
             "All parsing tests pass with the patched release."),
            ("Run the licence scan after upgrading {s}.",
             "license-scan", "run licence scan",
             "Ran the licence scan on the updated dependency tree.",
             "the new release keeps the same permissive licence",
             "No licence changes detected in the dependency tree."),
            ("Regenerate the lockfile after upgrading {s} and confirm it is consistent.",
             "lockfile-consistency", None, None,
             "the manifest and lockfile agree after regeneration",
             "Manifest and lockfile are consistent."),
            ("Upgrade {s} to the next minor release and rerun the integration suite.",
             "test_client_retries_on_timeout", "update dependency",
             "Merged the minor upgrade of {s} into the integration branch.",
             "retry behaviour is unchanged in the minor release",
             "Integration suite passed, including the timeout retry tests."),
        ],
        "fail": [
            ("Add a pinned-behaviour test showing {s} changed its default for empty input in the new major, before adapting callers.",
             "test_empty_input_returns_none", "add failing regression test",
             "Committed the behaviour test against the new major release.",
             "the new major returns an empty object instead of none",
             "The test failed: an empty object came back where callers expect none."),
            ("Check the lockfile guard with a manifest edit for {s} made without regenerating the lockfile.",
             "lockfile-consistency", "submit guard probe",
             "Pushed the manifest-only edit for {s} to a scratch branch.",
             "the lockfile was intentionally left stale",
             "lockfile-consistency failed: the manifest and lockfile disagree on {s}."),
            ("Run the vulnerability audit on the old version of {s} that the advisory names, to confirm the audit catches it.",
             "dependency-audit", "run dependency audit",
             "Ran the audit against the branch still pinned to the advised version.",
             "the pinned version is listed in the advisory",
             "dependency-audit failed and cited the advisory for {s}."),
        ],
    },
    {
        "subjects": ["the internal gateway certificate", "the metrics endpoint certificate",
                     "the partner upload certificate", "the admin console certificate"],
        "pass": [
            ("Renew {s} three weeks ahead of expiry.",
             "cert-expiry-window", "renew certificate",
             "Requested and installed the renewed {s}.",
             "the new expiry is more than sixty days out",
             "New expiry is comfortably beyond the sixty-day window."),
            ("Verify that the renewed {s} serves the full chain.",
             "test_chain_includes_intermediate", None, None,
             "the intermediate is bundled with the leaf",
             "The served chain includes the intermediate."),
            ("Probe the TLS handshake for {s} from each region after rotation.",
             "tls-handshake-probe", "run handshake probe",
             "Ran the handshake probe from all regions against {s}.",
             "every region completes the handshake with the new certificate",
             "Handshakes succeeded from every region."),
            ("Rotate {s} onto the new key size and confirm clients still connect.",
             "test_legacy_clients_connect", "rotate certificate",
             "Rotated {s} onto the larger key.",
             "all supported clients accept the larger key",
             "All supported client versions connected successfully."),
        ],
        "fail": [
            ("Run the expiry check against the old copy of {s} still on the standby host, expecting it to be flagged.",
             "cert-expiry-window", "run certificate scan",
             "Scanned the standby host that still holds the old {s}.",
             "the standby copy expires in nine days",
             "cert-expiry-window failed: the standby copy expires inside the window."),
            ("Add a test that loads {s} without its intermediate, reproducing the mobile client failures.",
             "test_chain_includes_intermediate", "add failing regression test",
             "Committed the chain test pointed at the leaf-only bundle.",
             "the bundle deployed last week omits the intermediate",
             "The test failed: the served chain stops at the leaf."),
            ("Probe the handshake for {s} with a client limited to the retired protocol version, to confirm it is refused.",
             "tls-handshake-probe", "run handshake probe",
             "Ran the probe with the client forced to the retired protocol version.",
             "the retired protocol version is disabled",
             "tls-handshake-probe failed the connection, as the retired version is disabled."),
        ],
    },
    {
        "subjects": ["the weekly snapshot of the catalog database", "last night's backup of the ticketing database",
                     "the monthly archive of the reports store", "the hourly snapshot of the scheduling database"],
        "pass": [
            ("Restore {s} into the drill environment for the quarterly restore test.",
             "test_point_in_time_restore_row_parity", "start restore drill",
             "Started the restore of {s} into the drill environment.",
             "restored row counts match the source at the snapshot time",
             "Restored row counts match the source for every table."),
            ("Verify checksums of {s} after moving it to cold storage.",
             "restore-checksum-verify", "run checksum verification",
             "Ran checksum verification on {s} in cold storage.",
             "every file checksum matches the manifest",
             "All file checksums match the manifest."),
            ("Time a full restore of {s} to confirm we meet the recovery time objective.",
             "restore-duration-budget", None, None,
             "the restore completes inside the two hour objective",
             "Restore completed in well under two hours."),
            ("Confirm encryption at rest for {s} after the storage class change.",
             "test_backup_encrypted_at_rest", "run storage audit",
             "Audited the storage class settings for {s}.",
             "the new storage class keeps server-side encryption on",
             "Encryption at rest is enabled on the new storage class."),
        ],
        "fail": [
            ("Feed the restore tool a deliberately truncated copy of {s} to confirm it refuses to restore.",
             "test_restore_rejects_corrupt_archive", "submit guard probe",
             "Ran the restore against the truncated copy in a scratch environment.",
             "the archive footer is missing",
             "The restore was refused: the archive failed integrity validation before any data was written."),
            ("Add a reproduction test for the restore script skipping sequences when restoring {s}.",
             "test_restore_resets_sequences", "add failing regression test",
             "Committed the sequence test before patching the restore script.",
             "the script restores tables but not sequence positions",
             "The test failed: sequences restarted at one after the restore."),
            ("Run checksum verification on the copy of {s} known to have been altered during the bad transfer.",
             "restore-checksum-verify", "run checksum verification",
             "Verified checksums on the copy from the interrupted transfer.",
             "one file was rewritten during the interrupted transfer",
             "restore-checksum-verify failed on one data file whose checksum no longer matches."),
        ],
    },
    {
        "subjects": ["the new_cart_summary flag", "the faster_search_ranking flag", "the dark_mode_default flag",
                     "the bulk_export_v2 flag"],
        "pass": [
            ("Move {s} from five to twenty-five percent of users.",
             "rollout-percentage-guard", "update flag rollout",
             "Raised {s} to twenty-five percent.",
             "a step from five to twenty-five is within the allowed increment",
             "Rollout guard accepted the step; error rates stayed flat."),
            ("Add {s} to the flag configuration with the default turned off.",
             "flag-config-schema", None, None,
             "the new entry matches the configuration schema",
             "Flag configuration validates against the schema."),
            ("Enable {s} for the internal staff cohort only.",
             "test_flag_default_off_for_unknown_cohort", "update flag rollout",
             "Enabled {s} for the staff cohort.",
             "unknown cohorts still see the flag off",
             "Unknown cohorts still evaluate {s} to off."),
            ("Remove the dead code path behind {s} now that it is fully rolled out.",
             "test_flag_removal_keeps_behaviour", "open cleanup change",
             "Opened the change removing the old path behind {s}.",
             "behaviour matches the fully-on state",
             "Behaviour after removal matches the fully-on state."),
        ],
        "fail": [
            ("Attempt to jump {s} straight from ten to one hundred percent to confirm the rollout guard stops it.",
             "rollout-percentage-guard", "submit guard probe",
             "Submitted the oversized rollout step for {s}.",
             "the step exceeds the maximum allowed increment",
             "rollout-percentage-guard rejected the step as larger than the allowed increment."),
            ("Write a test showing {s} evaluates to on for users with no cohort assigned, before fixing the default.",
             "test_flag_default_off_for_unknown_cohort", "add failing regression test",
             "Committed the cohort test against the current evaluator.",
             "the evaluator falls through to the last rule when cohort is missing",
             "The test failed: users without a cohort saw {s} turned on."),
            ("Validate a configuration where {s} is missing its owner field, to confirm the schema check blocks it.",
             "flag-config-schema", None, None,
             "the owner field is required by the schema",
             "flag-config-schema failed: the owner field is missing for {s}."),
        ],
    },
    {
        "subjects": ["the developer handbook site", "the public API reference", "the operations runbook site",
                     "the onboarding guide"],
        "pass": [
            ("Rebuild {s} after the navigation restructure.",
             "docs:build-strict", "trigger docs build",
             "Triggered a strict build of {s}.",
             "the strict build has no warnings after the restructure",
             "Strict build finished without warnings."),
            ("Run the link checker on {s} after moving pages into sections.",
             "docs:linkcheck", "run link checker",
             "Ran the link checker across {s}.",
             "redirects cover every moved page",
             "No broken links; redirects cover the moved pages."),
            ("Execute the code samples in {s} after the client rename.",
             "test_code_samples_execute", None, None,
             "every sample uses the renamed client",
             "Every code sample executed successfully."),
            ("Spell-check {s} before the quarterly publish.",
             "docs:spelling", "request review",
             "Asked the docs reviewer to sign off on {s}.",
             "the glossary covers the product terms",
             "Spelling check is clean and the reviewer approved."),
        ],
        "fail": [
            ("Run the link checker on {s} before adding redirects, to list exactly which links the move breaks.",
             "docs:linkcheck", "run link checker",
             "Ran the link checker on the branch without redirects.",
             "moved pages have no redirects yet",
             "docs:linkcheck failed and listed eleven broken internal links."),
            ("Add a test that runs the pagination sample in {s}, reproducing the reader report that it errors.",
             "test_pagination_sample_runs", "add failing regression test",
             "Committed the sample-execution test before fixing the sample.",
             "the sample still calls the removed page cursor argument",
             "The test failed: the sample raised an error on the removed argument."),
            ("Build {s} in strict mode with the page that has an unresolved cross-reference, expecting the build to stop.",
             "docs:build-strict", "trigger docs build",
             "Triggered a strict build including the page with the broken reference.",
             "strict mode treats unresolved references as errors",
             "docs:build-strict failed on the unresolved cross-reference."),
        ],
    },
    {
        "subjects": ["the log rotation job", "the stale session cleanup job", "the report mailer job",
                     "the temp file sweeper"],
        "pass": [
            ("Change {s} to run at two in the morning instead of midnight.",
             "cron-syntax-lint", "update schedule",
             "Updated the schedule entry for {s}.",
             "the new expression is valid and fires once a day",
             "Schedule expression is valid and fires once daily."),
            ("Raise the retention of {s} from five to seven days.",
             "test_rotation_keeps_seven_days", "deploy job change",
             "Deployed the new retention setting for {s}.",
             "files newer than seven days are kept",
             "Files from the last seven days were kept; older ones removed."),
            ("Add a lock to {s} so overlapping runs cannot start.",
             "test_job_skips_when_lock_held", None, None,
             "a second run exits early while the lock is held",
             "Second run exited early while the first held the lock."),
            ("Move {s} to the shared scheduler host.",
             "job-heartbeat-check", "migrate job",
             "Moved {s} onto the shared scheduler host.",
             "the heartbeat arrives on schedule from the new host",
             "Heartbeat received on schedule from the new host."),
        ],
        "fail": [
            ("Write a test proving {s} deletes files from today when the host clock is behind, before fixing the age calculation.",
             "test_sweeper_ignores_future_dated_files", "add failing regression test",
             "Committed the clock-skew test before the fix.",
             "the age calculation goes negative for future-dated files",
             "The test failed: a file stamped slightly in the future was deleted."),
            ("Lint the malformed schedule expression a teammate proposed for {s}, expecting it to be rejected.",
             "cron-syntax-lint", None, None,
             "the expression has six fields where five are expected",
             "cron-syntax-lint failed: the expression has too many fields."),
            ("Stop {s} on purpose for an hour to confirm the heartbeat check raises.",
             "job-heartbeat-check", "pause job",
             "Paused {s} for one hour.",
             "no heartbeat will arrive during the pause",
             "job-heartbeat-check failed after the missed heartbeat interval."),
        ],
    },
    {
        "subjects": ["the main build pipeline", "the mobile build pipeline", "the docs pipeline",
                     "the nightly integration pipeline"],
        "pass": [
            ("Key the dependency cache in {s} on the lockfile hash.",
             "test_cache_key_includes_lockfile_hash", "update pipeline config",
             "Updated the cache step in {s}.",
             "the cache key changes whenever the lockfile changes",
             "Cache key changes with the lockfile and hits otherwise."),
            ("Split the slow test stage in {s} into four parallel shards.",
             "pipeline-duration-budget", "update pipeline config",
             "Pushed the sharded stage configuration to {s}.",
             "wall-clock time drops under the fifteen minute budget",
             "Pipeline finished well inside the fifteen minute budget."),
            ("Validate the configuration file for {s} after the runner image bump.",
             "pipeline-config-lint", None, None,
             "the configuration parses and references valid stages",
             "Configuration lint is clean."),
            ("Pin the runner image used by {s} to a fixed digest.",
             "test_runner_image_pinned", "request review",
             "Requested review of the pinned runner image change.",
             "every job references the pinned image",
             "Reviewer confirmed every job uses the pinned image."),
        ],
        "fail": [
            ("Add a test showing {s} reuses a stale cache after the lockfile changes, before fixing the cache key.",
             "test_cache_key_includes_lockfile_hash", "add failing regression test",
             "Committed the cache-key test ahead of the key change.",
             "the current key only includes the branch name",
             "The test failed: the cache key stayed the same after the lockfile changed."),
            ("Lint the {s} configuration with a job that references an undefined stage, expecting the lint to stop it.",
             "pipeline-config-lint", "submit guard probe",
             "Pushed the configuration with the undefined stage to a scratch branch.",
             "the job points at a stage that does not exist",
             "pipeline-config-lint failed on the reference to the undefined stage."),
            ("Run {s} without sharding to record the baseline overrun before the split.",
             "pipeline-duration-budget", "trigger pipeline run",
             "Triggered an unsharded run of {s}.",
             "the unsharded run takes around twenty-five minutes",
             "pipeline-duration-budget failed: the run took about twenty-five minutes."),
        ],
    },
]

PRED_PASS = [
    "Expect {check} to pass: {exp}.",
    "{check} should come back green, since {exp}.",
    "The run succeeds with {check} passing; {exp}.",
    "Should pass. {check} is the check that matters, and {exp}.",
    "{exp_cap}, so {check} passes.",
    "Green on {check}; {exp}.",
]
PRED_FAIL = [
    "Expect {check} to fail, because {reason}.",
    "{check} will fail: {reason}.",
    "This should go red on {check}, since {reason}.",
    "{check} is expected to fail until the fix lands; {reason}.",
    "Failure expected in {check}. Reason: {reason}.",
    "{reason_cap}, so {check} fails.",
]
PRED_VAGUE = [
    "Expect this to fail somewhere.",
    "Something in the run will probably go red.",
    "I think this will not pass.",
    "Likely a failure, though I cannot say where.",
    "Probably breaks; we will see what comes back.",
    "This is not going to be clean.",
    "Expect at least one problem to show up.",
    "Some check will likely object to this.",
]
STATUS_PASS = {
    "ci": ["All checks passed.", "Pipeline green.", "Build succeeded; all jobs passed.", "CI run passed."],
    "review": ["Review approved.", "Approved by reviewer.", "Reviewer signed off.", "Change approved."],
    "tool": ["Completed with exit code 0.", "Tool run finished successfully.", "Command succeeded.", "Job completed without errors."],
}
STATUS_FAIL = {
    "ci": ["1 check failed.", "Pipeline red on one job.", "Build failed.", "CI run failed."],
    "review": ["Changes requested.", "Reviewer blocked the change.", "Review not approved.", "Blocked in review."],
    "tool": ["Exited with non-zero status.", "Tool run failed.", "Command failed.", "Job ended with an error."],
}
STATUS_FAIL_MULTI = {
    "ci": ["2 checks failed.", "Pipeline red on two jobs.", "Build failed on multiple checks."],
    "review": ["Changes requested on two points.", "Reviewer blocked the change for two reasons."],
}
DIAG = [
    "Failure reproduced on the first attempt; no flakiness observed.",
    "Logs attached to the run; the failing assertion is the only error.",
    "Rerun gave the same result.",
    "No other jobs were affected.",
    "Duration was in the normal range for this job.",
]
NOTE_ROUTINE_FAIL = [
    "As planned; the fix follows in the next change.",
    "Expected result, recorded before the fix.",
    "Guard behaved as intended.",
    "This is the baseline we wanted on record.",
]
NOTE_PASS = [
    "Nothing further needed.",
    "Closing the task.",
    "Will watch the next scheduled run as usual.",
]
B_FAIL_TEXT = [
    "{b} also reported a failure that the change did not anticipate.",
    "{b} failed unexpectedly on this change.",
    "An unrelated looking failure in {b} showed up on this run.",
    "{b} went red; it was not part of the plan for this change.",
]
MISMATCH_ONLY_B = [
    "{b} failed. {a} passed.",
    "{b} failed while {a} came back clean.",
    "The failure was in {b}; {a} did not fail.",
]


def _cap(text):
    return text[:1].upper() + text[1:]


def _fill(text, subject):
    return text.replace("{s}", subject) if text else text


def _domain_checks(domain):
    names = [p[1] for p in domain["pass"]] + [f[1] for f in domain["fail"]]
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen


def _prediction(text, success, check, conf):
    return {"type": "prediction", "expected_outcome": text, "expected_success": success,
            "expected_failing_check": check, "confidence_pct": conf}


def _action(kind, desc):
    return {"type": "action", "action_kind": kind, "description": desc}


def _outcome(success, source, sections, failing):
    return {"type": "outcome", "success": success, "source": source,
            "sections": sections, "failing_checks": failing}


def _pick_fail_item(rng):
    domain = rng.choice(DOMAINS)
    item = rng.choice(domain["fail"])
    subject = rng.choice(domain["subjects"])
    return domain, item, subject


def _fail_action(rng, item, subject):
    kind = item[2] or rng.choice(["run check", "dispatch check run", "start verification run"])
    desc = _fill(item[3], subject) if item[3] else "Dispatched the run for " + subject + "."
    return _action(kind, desc)


def _routine_pass(rng, source):
    domain = rng.choice(DOMAINS)
    decision, check, kind, desc, exp, pass_text = rng.choice(domain["pass"])
    s = rng.choice(domain["subjects"])
    exp = _fill(exp, s)
    pred = _prediction(rng.choice(PRED_PASS).format(check=check, exp=exp, exp_cap=_cap(exp)),
                       True, None, rng.randint(70, 97))
    sections = [{"role": "status", "text": rng.choice(STATUS_PASS[source])},
                {"role": "evaluation", "text": _fill(pass_text, s)}]
    if rng.random() < 0.2:
        sections.append({"role": "operator_note", "text": rng.choice(NOTE_PASS)})
    events = [pred]
    if kind is not None and rng.random() < 0.9:
        events.append(_action(kind, _fill(desc, s)))
    events.append(_outcome(True, source, sections, []))
    return _fill(decision, s), events


def _routine_fail(rng, source, vary_case):
    _domain, item, s = _pick_fail_item(rng)
    decision, check, kind, desc, reason, fail_text = item
    reason = _fill(reason, s)
    pred = _prediction(rng.choice(PRED_FAIL).format(check=check, reason=reason, reason_cap=_cap(reason)),
                       False, check, rng.randint(70, 97))
    sections = [{"role": "status", "text": rng.choice(STATUS_FAIL[source])},
                {"role": "evaluation", "text": _fill(fail_text, s)}]
    if rng.random() < 0.35:
        sections.append({"role": "diagnostic", "text": rng.choice(DIAG)})
    if rng.random() < 0.3:
        sections.append({"role": "operator_note", "text": rng.choice(NOTE_ROUTINE_FAIL)})
    reported = check
    if vary_case == 1:
        reported = check.upper()
    elif vary_case == 2:
        reported = " " + check + " "
    elif vary_case == 3:
        reported = _cap(check)
    events = [pred]
    if kind is not None:
        events.append(_action(kind, _fill(desc, s)))
    events.append(_outcome(False, source, sections, [reported]))
    return _fill(decision, s), events


def _other_check(rng, domain, check):
    return rng.choice([c for c in _domain_checks(domain) if c.lower() != check.lower()])


def _adv_vague(rng):
    domain, item, s = _pick_fail_item(rng)
    decision, check = item[0], item[1]
    source = rng.choice(["ci", "review"])
    pred = _prediction(rng.choice(PRED_VAGUE), False, None, rng.randint(55, 90))
    sections = [{"role": "status", "text": rng.choice(STATUS_FAIL[source])},
                {"role": "evaluation", "text": _fill(item[5], s)}]
    events = [pred, _fail_action(rng, item, s), _outcome(False, source, sections, [check])]
    return _fill(decision, s), events


def _adv_mismatch(rng, extra):
    domain, item, s = _pick_fail_item(rng)
    decision, check_a, reason = item[0], item[1], _fill(item[4], s)
    check_b = _other_check(rng, domain, check_a)
    source = rng.choice(["ci", "review"])
    pred = _prediction(rng.choice(PRED_FAIL).format(check=check_a, reason=reason, reason_cap=_cap(reason)),
                       False, check_a, rng.randint(60, 95))
    if extra:
        sections = [{"role": "status", "text": rng.choice(STATUS_FAIL_MULTI[source])},
                    {"role": "evaluation", "text": _fill(item[5], s)},
                    {"role": "evaluation", "text": rng.choice(B_FAIL_TEXT).format(b=check_b)}]
        failing = [check_a, check_b]
    else:
        sections = [{"role": "status", "text": rng.choice(STATUS_FAIL[source])},
                    {"role": "evaluation", "text": rng.choice(MISMATCH_ONLY_B).format(a=check_a, b=check_b)}]
        failing = [check_b]
    events = [pred, _fail_action(rng, item, s), _outcome(False, source, sections, failing)]
    return _fill(decision, s), events


def _adv_late(rng):
    domain, item, s = _pick_fail_item(rng)
    decision, check, reason = item[0], item[1], _fill(item[4], s)
    source = rng.choice(["ci", "review"])
    pred = _prediction(rng.choice(PRED_FAIL).format(check=check, reason=reason, reason_cap=_cap(reason)),
                       False, check, rng.randint(60, 97))
    sections = [{"role": "status", "text": rng.choice(STATUS_FAIL[source])},
                {"role": "evaluation", "text": _fill(item[5], s)}]
    events = [_fail_action(rng, item, s), pred, _outcome(False, source, sections, [check])]
    return _fill(decision, s), events


def _episode(cls, n, decision, events):
    return {"id": "v2-%s-%03d" % (cls, n), "class": cls, "expected_flag": cls != "routine",
            "decision_text": decision, "events": events}


def build():
    rng = random.Random(SEED)
    episodes = []
    # Routine: fixed sources for the failures, then shuffle the pass/fail mix.
    fail_sources = ["ci"] * 27 + ["review"] * 21 + ["tool"] * 12
    vary = [1, 2, 3, 1, 2, 3] + [0] * (N_ROUTINE_FAIL - 6)
    rng.shuffle(vary)
    plan = [("fail", src, v) for src, v in zip(fail_sources, vary)]
    plan += [("pass", rng.choice(["ci", "ci", "review", "tool"]), 0) for _ in range(N_ROUTINE - N_ROUTINE_FAIL)]
    rng.shuffle(plan)
    for i, (kind, src, v) in enumerate(plan, start=1):
        if kind == "pass":
            decision, events = _routine_pass(rng, src)
        else:
            decision, events = _routine_fail(rng, src, v)
        episodes.append(_episode("routine", i, decision, events))
    for i in range(1, N_ADV + 1):
        episodes.append(_episode("adversarial_vague", i, *_adv_vague(rng)))
    extras = [True] * N_MISMATCH_EXTRA + [False] * (N_ADV - N_MISMATCH_EXTRA)
    rng.shuffle(extras)
    for i, extra in enumerate(extras, start=1):
        episodes.append(_episode("adversarial_mismatch", i, *_adv_mismatch(rng, extra)))
    for i in range(1, N_ADV + 1):
        episodes.append(_episode("adversarial_late", i, *_adv_late(rng)))
    return {
        "version": "routine_episodes_v2",
        "seed": SEED,
        "definition": ("A routine episode is one where the agent's prediction, recorded before acting, "
                       "correctly and specifically states the outcome, including the exact failing check "
                       "when a failure is expected; adversarial episodes break that in one declared way."),
        "built_by": "separate session, blind to gate code",
        "episodes": episodes,
    }


def main():
    data = build()
    text = json.dumps(data, indent=1) + "\n"
    OUT_JSON.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    OUT_SHA.write_text(digest + "\n", encoding="utf-8")
    print(digest)


if __name__ == "__main__":
    main()
