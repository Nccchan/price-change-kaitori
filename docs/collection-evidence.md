# Optional collection evidence (schema version 2)

This module adds local shadow evidence for the org repository's collection
supervision. It does not change fetched categories, historical JSON output,
price comparisons, approval flags, Supabase writes, or command exit codes.
There are no additional network requests, including requests to Supabase.

## Activation

The org-side launcher creates a unique local directory for each attempt and
sets these variables for the existing `main.py --fetch-web` command:

| Variable | Meaning |
| --- | --- |
| `COLLECTION_ATTEMPT_ID` | UUID for this attempt; every retry gets a new UUID |
| `COLLECTION_ATTEMPT_DIR` | Existing absolute path on a local filesystem |
| `COLLECTION_COLLECTOR_COMMIT` | Commit verified by the launcher |
| `COLLECTION_COLLECTOR_DIRTY` | `0` or `1`, verified by the launcher |

Without the first two variables, evidence is disabled. Partial or malformed
configuration logs an evidence warning and keeps the normal price command
running. Unknown commit/dirty values are represented as null, never invented.
`collector_version.code_sha256` hashes the actual relevant source files as
additional evidence, including local modifications.

The caller enables this only for an intended Homura attempt. An external-file
(`--from-json`) or image invocation cannot attest a fresh live collection.

## Primary artifact

One immutable file, `homura-<game>.json`, is written after collection finishes
or throws. It is published by atomic link from a temporary file. An existing
file is never replaced. The org recorder validates its attempt ID and SHA256
before independently batch-uploading the attempt/results to the database.

The envelope contains:

- `schema_version=2`, `attempt_id`, `source=homura`, `game`;
- `started_at`, `generated_at`, first page's `observed_at`;
- `collector_version={commit,dirty,code_sha256}`;
- `fetch_succeeded`, original `error_type` if collection raised;
- `categories`, `candidates`, and observational `mapping_inputs`;
- `mapping_inputs_status` and `mapping_observed_at`;
- `artifact_sha256`, the SHA256 of canonical JSON excluding that field.

Canonical JSON is UTF-8, sorted keys, no ASCII escaping, no NaN, and compact
`,` / `:` separators. The sender additionally stores the hash of the complete
file bytes, including the envelope's hash field.

Each category includes its `category_id`, `game`, `external_unit`, timestamps,
`complete`, `reason_code`, and pages. A page has its URL, response-body SHA256,
observation timestamp, card/parsed counts, terminal flag, and parse coverage.
The source returned prices are observed before `_merge` can overwrite duplicate
keys. Candidates retain the raw name/code/price, resolved external reference,
explicit category unit, URL, page URL, observation time, and independent parse
validity. Exact repeated markup is deduplicated; different URLs or prices remain
separate observations even when their resolved external reference is equal.

Current direct categories are:

| Game | Direct categories |
| --- | --- |
| pokemon | 128 BOX, 129 NS |
| onepiece | 132 BOX, 133 CARTON |
| dragonball | 171 BOX |
| yugioh | 159 BOX, 172 CARTON, only if already invoked |

No Pokémon CARTON or Dragon Ball CARTON evidence is generated from `price_2`.
No category is added to the normal collector by this module.

## Conservative completeness and classification

`complete=true` requires every recognized card to have a valid positive integer
price, all observed pages to parse, pagination to end, and candidate identities
to resolve with the already-loaded mapping. Empty/unrecognized pages, malformed
prices, unresolved aliases, or exceptions leave the category incomplete. An
empty historical JSON file is therefore not evidence of `not_listed`.

The existing parser is not changed: for example, it may historically accept a
numeric prefix from a malformed price string. The independent observer rejects
that as valid supervision evidence while preserving the existing price result.

`fetch_succeeded` describes whether the fetch function returned. It does not
override each category/candidate's completeness or validity. Partial page facts
survive a later category request failure, and the original exception propagates.

The org-side recorder is responsible for mapping evidence to its fixed manifest
and classifying `success`, `not_listed`, `match_failed`, `parse_failed`,
`processing_failed`, and `not_processed`. A positive price for a different unit,
ambiguous external reference, or another attempt must never become success.

`mapping_inputs` records rows returned by the existing active-Homura ledger query
before its BOX-only transform. Each row has `external_ref`, `search_hint`, and
`products:{sku,category}`. This read happens during the existing merge step,
after page acquisition. It is explicitly not a start-of-attempt manifest.
`ledger_complete`, `config_fallback`, and `not_observed` distinguish the mapping
source actually used. The org recorder compares this evidence with its frozen
expectation and exposes any contract difference.

If an earlier category succeeds but a later request fails before `_merge`, the
page facts are still saved, but the existing mapping query has not yet run.
Those raw rows cannot be promoted to a confidently mapped SKU merely to improve
the success count. The org recorder reports the missing mapping stage explicitly.

## Downstream annotations

Separate immutable files retain observations of existing pricing stages:

- `homura-<game>.comparison.json`: `stage=comparison`,
  `decisions[{external_ref,external_unit,reason_code,current_price,proposed_price}]`.
  Reasons include `same_value`, actual `guard_hold`, and `comparison_only`.
- `homura-<game>.payloads.json`: actual pair-price guard violations, with
  `reason_code=guard_hold` and `guard=price_guard`. A batch stop records all
  stopped payload units with `guard=price_guard_batch`, then re-raises the
  original `BatchPriceGuardError` unchanged.
- `homura-<game>.writer.json`: actual same-day manual-price holds by
  `product_id` and `canonical_unit`, with `guard=same_day_manual`.

These annotations describe stages reached, not proof of publication. They use
the same attempt/source/game envelope and are independently hashed. The primary
acquisition artifact is not rewritten by downstream guards or writer failures.

## Failure isolation and deployment

Evidence errors are caught within the observer and print only their error class.
They do not change the fetched result, network request order, price payload,
original exception, or exit code. There is no synchronous database upload and no
retry loop in the price command. Missing/corrupt files become recording issues
for the independent org-side monitor; no synthetic success is generated.

Deploy the complete metadata change before enabling its org-side opt-in flag.
The org-side launcher must assign distinct directories to concurrent attempts.
Keep the verified collector commit and dirty flag in each launch's metadata.
To roll back, disable the org-side opt-in flag and revert this metadata change;
already generated artifacts and previously recorded attempt history remain.

Tests use fake HTTP responses and no production credentials. The collector test
executes the untouched `1d8aa8a697998b4389d5f91a9f35606629b109dd` implementation beside the instrumented collector
and compares outputs plus request order. Disk failures, parsing problems,
duplicate refs, units, same-value observations and guarded prices are covered.
The baseline commit must exist in the local checkout; use full git history in CI.
