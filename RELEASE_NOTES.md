# kernel-backport 1.0.0 (execution handoff and finish)

- Add `kernel-backport finish --results-dir DIR` to close completed execution
  phases and hand subsequent patch-series management over to Git. Refuse to
  finish with outstanding TAKEs, unprocessed or blocked execution, stale ICC,
  mismatched Git HEAD, dirty worktree, or inconsistent journal provenance.
- Write `.kernel-backport/finish.json` and `finish-review.md`: reproducible
  historical snapshots of selected commits, engineer decisions and reasons,
  applied downstream SHAs, phase journal hashes, and ICC warnings. Mark
  finalization only after successful report writes. Repeated `finish` is a
  read-only no-op.
- After handoff, block additional mutation/execution on the finished plan.
  Permit read-only status and nonbillable review even if Git history is later
  amended or rebased for MR review; historical downstream SHAs are not
  dynamically reconciled.
- Extend local Git lifecycle tests for incomplete sessions, multi-phase
  execution, finalization idempotence, post-finish Git amendments, blocked
  commands, and tampered journal entries. No changes to model selection
  policy, planner algorithms, or the Git cherry-pick engine.

# kernel-backport 0.1.38 (workflow README refresh; documentation only)

- Rewrite the top-level README as a durable, task-oriented guide to what
  kernel-backport does and how to use it, without individual release entries,
  project checkpoints, or revision-specific advice.
- Make explicit the separate questions addressed by conditioning, exploratory
  replay, execution, and validation; explain planning, guarded model-assisted
  selection, engineer decisions, ICC, and journaled application.
- Clarify that a conflict-free exploratory replay is not a prerequisite for
  execution, whereas a current blocker-free ICC is required.
- No application behavior, advisor policy, command-line interface, or kernel
  integration code changes. Only README content, VERSION, and this release
  note differ from the previous distribution.

# kernel-backport 0.1.37 (auditable out-of-target Fixes additions)

- Harden the **existing** `add` subcommand (introduced in 0.1.3); no replan,
  new advisor call, or modification to preexisting plan/worksheet entries is
  needed to import a later upstream correction.
- Require explicit `--allow-outside-target` for newly added commits not
  reachable from the original upstream target. Keep the target and immutable
  source plan unchanged; record the approved exception in the engineer overlay,
  worksheet, and ICC `engineer_target_exceptions` report. An authorized
  exception is not misreported as an unexplained warning; old overlays with
  unapproved outside-target commits still load and retain their warning.
- Add `--fixes SHA` (repeatable) for documented later fixes of commits already
  in the worksheet. Validate each relation against the **actual upstream Git
  commit's** `Fixes:` trailer before changing the plan; preserve full SHA
  provenance in the overlay and worksheet. Fixes provenance is NOT treated as
  a cherry-pick prerequisite. Use separate `--requires` or `--required-by` for
  an engineer-verified hard dependency.
- Make exact repeat `add` requests idempotent: report unchanged and retain the
  previous ICC attestation. Reject conflicting re-adds rather than updating an
  existing engineer decision implicitly. Keep original `decide` semantics.
- Preserve the 0.1.36 additions schema and all prior plan decisions, including
  auto-conditioning provenance. Invalid options, unknown parent, unrelated
  Fixes trailer, or unapproved out-of-range commit fail before mutation.
  Existing atomic worksheet/overlay rollback and execution journals remain.
- Add end-to-end local Git regression tests for out-of-target consent, verified
  Fixes relationships, fail-closed validation, old overlay compatibility,
  no-op repeat additions, ICC and exploratory replay, and actual journaled
  application. No RHEL kernel build/boot or live model API calls are claimed.

# kernel-backport 0.1.36 (guarded REVIEW disagreement adjudication)

- Include unresolved planner REVIEW disagreements in separate, feature-level
  `D-...` assessment groups when `condition --auto-skip-disagreements` is used.
  Preserve existing `F-...` TAKE group IDs, reviewer prompts, and Sol/Terra
  cache identities; no new `plan` run or redundant TAKE assessment is needed.
- Supply explicit external selected/REVIEW/SKIP consumer, provider and Fixes
  context in the disagreement dossiers; recorded edges are evidence, not
  proven hard dependencies. Do not automatically promote REVIEW to TAKE.
- With explicit `--allow-unresolved --auto-skip-disagreements
  --apply-auto-skips`, permit a validated Sol REVIEW→SKIP only for a high-
  confidence EXCLUDE/LOW OPTIONAL or CLEANUP member with no outstanding
  verification or correctness/Fixes/stable/erratum/consumer obligation.
  Other reviewer recommendations, invalid results, engineer decisions, budget
  and tool errors remain blocked for engineering review.
- Accept a tightly attributed `D-...` Sol evidence source in the existing
  worksheet automation and readiness reconciliation. Maintain full ICC after
  changes, atomic rollback on new blockers, and append-only audit provenance.
- Add regression tests for retained-consumer blocks, safety/fix gates, cache
  stability, existing optional TAKE behavior, human overrides and execution
  readiness. No live model requests, kernel build or boot are claimed.

# kernel-backport 0.1.35 (fail-closed bulk conditioning of invalid model responses)

- A single Sol response with an invalid per-member `member_roles` list no
  longer aborts an entire `condition --assess` batch. The paid raw response and
  precise validation error remain in `.kernel-backport/feature-assessments.json`.
  That feature is marked `INVALID`, receives **no automatic SKIPs**, and
  appears explicitly in the conditioning decision report and exception list.
- Independent feature assessments continue, allowing valid groups to be
  conditioned without making a new full planner run. An invalid cached
  assessment is never repurchased implicitly on the next pass; the explicit
  billable `--retry-invalid` option requests a fresh response when desired.
- The interactive `review --assess-features` command remains strict and
  fail-fast. `Fixes:` / stable provenance, unresolved consumer and engineer
  decision protections are unchanged. Neither an invalid model response nor
  a batch exception authorizes kernel-source edits or backport execution.
- Added regression coverage for invalid group isolation, paid-cache reuse,
  explicit reattempt and reporting. Existing 0.1.34 planner/ICC behavior is
  unchanged.

# kernel-backport 0.1.34 (fresh-plan conditioning with unresolved issues)

- Reconcile planner TAKE->REVIEW readiness issues against the current worksheet
  decisions, rather than permanently treating a historical REVIEW provider as
  unresolved. A provider promoted to TAKE or a consumer demoted from TAKE clears
  this obsolete readiness issue; remaining logical prerequisites continue to
  block ICC and execution. Conditioning may inspect them under explicit
  `--allow-unresolved` opt-in.
- Preserve fail-closed behavior for other planner issues, unrecognized or
  malformed prerequisite evidence, and disagreements without a separately
  authorized, evidence-guarded automated SKIP.
- Add regression tests for provider inclusion, consumer exclusion, unresolved
  prerequisites and malformed planner evidence. No model/API or kernel calls.

- New `condition --allow-unresolved` permits Sol/Terra feature assessment and
  guarded automatic SKIPs on a *fresh plan* before engineer-facing planner
  disagreements are closed. No new ICC/advisor blockers may be introduced.
  Existing Fixes, prerequisites and unresolved advisor cases retain their
  blockers; no bot decision is misrepresented as engineering approval. An incomplete logical graph may omit exploratory replay evidence.
- Explicit `--auto-skip-disagreements` may resolve a planner *disagreement*
  through a separate, cached gpt-6.1-sol feature assessment ONLY for an
  OPTIONAL/CLEANUP commit in an EXCLUDE/LOW feature, with all Fixes/stable,
  correctness, dependency, and previous-decision guards satisfied. The
  separate automatic source is recorded and engineer REVIEW reopens it.
  No error/budget disagreement, ambiguous fix or CORE feature is auto-approved.
- Enables a full fresh planner -> ICC -> conditioning workflow without
  20+ preliminary manual decisions. The tool reports remaining exceptions in
  the decisions report; these still block execution.
- Update an older integration test to tolerate the expected 80-column line
  wrapping of worksheet notes (no change to the kernel integration behavior).

# kernel-backport 0.1.33 (guarded automated feature conditioning)

- New opt-in `condition` CLI: default dry-run with no API calls; `--assess`
  enables billable Sol feature selection with cached Terra patch summaries;
  `--apply-auto-skips` authorizes only deterministic, policy-guarded SKIPs.
- Provide `--scope critical|all`, `--max-new-features`, `--max-api-requests`,
  `--max-rounds`, model overrides, `--retry-invalid`, and `--report-only`.
  All feature groups, including singleton TAKEs, are examined under `all`.
- Preserve independently applicable Fixes, potentially serious defects,
  advisor escalations, human decisions and retained consumers. Cross-check
  raw upstream Git trailers before applying any automatic SKIP. A Fixes tag
  remains a historical relation, never automatically a hard prerequisite.
- Add automated-decision worksheet fields separate from human overrides,
  without allowing automated SKIPs to resolve readiness or Omitted-fix duties.
  Manual engineer TAKE/SKIP/REVIEW takes precedence at all times.
- Store evidence-linked conditioning reports and source-plan-bound append-only
  proposal/commit/rollback events, including subsequent manual `decide` overrides. Recheck ICC with temporary worktree replay
  after every SKIP batch and rollback the worksheet if checks fail.
- Reuse model caches and patch-segment caches across iterations; report
  actual provider-reported tokens rather than invented dollar estimates.
- Add regression tests covering no-call dry-run, safe exclusion, manual
  overrides, Fixes and consumer guards, and request attempt budgets. Test
  only with disposable Git repos; no live API, kernel build or boot performed.

# kernel-backport 0.1.32 (advisor readiness enforced through execution)

- Fix a 0.1.31 gap where `check --no-replay` reported zero logical ICC blockers
  even though the source planner marked 19 Terra/Sol escalation disagreements
  unresolved, allowing old executable ICC checks to ignore those reviews.
- Independently surface `advisor_readiness_blockers` alongside graph blockers
  in ICC JSON, console, worksheet integration header and return status.
  Do not conflate Git replay conflicts with advisor-selection disagreements.
- Accept only an explicit engineer TAKE or SKIP *with nonempty rationale* to
  resolve an individual escalation disagreement, error or budget deferral;
  REVIEW remains unresolved. Expand aggregate budget issues from their
  recorded per-commit statuses and fail closed if such details are missing.
- Preserve other planner execution blockers as hard readiness failures,
  not automatically waivable by unrelated worksheet decisions. Keep full
  exploratory replay available when only advisor readiness is blocked.
- Recheck planner blockers in `apply`/`resume` instead of trusting an old ICC.
  Pre-0.1.32 ICC attestations require a new `check`. Never change source plan
  or worksheet decisions automatically; no AI calls or replanning necessary.
- Add 10 fixture regression tests and verify the preserved real plan 05
  yields 19 blockers, decreasing to 18 after one explicit engineer decision.
  No live kernel build/boot or live API inference performed.

# kernel-backport 0.1.31 (selective dual-model planning escalation)

- Add explicit opt-in `plan --advisor-escalation-model`,
  `--advisor-escalation-policy selective`, and independent secondary provider,
  timeout, context, request-size and maximum-escalation settings. Terra remains
  the first-pass planner; use Sol for rule-selected difficult planning commits.
- Route undecided model REVIEW cases, optional/uncertain TAKEs, risky SKIPs of
  potential fixes/providers and source-backed dependency/scope disputes. A
  replay conflict alone does not establish ambiguity or trigger escalation.
- Batch secondary assessments after primary selection/closure so expensive
  model switching is not performed for every candidate. Use the same Git
  evidence/structured response validators and a versioned independent skeptical
  policy instruction, without passing the first model's vote to the second.
- Preserve the primary and secondary decisions, rationales, model IDs,
  prerequisites and routing reasons in the planner JSON; record routing totals,
  deferred candidates and per-model telemetry. Secondary cache identities
  include the alternate model, provider and routing-policy version.
- Never promote a Sol TAKE or accept a Sol SKIP automatically on disagreement:
  mark disputed selections or prerequisite/binding dispositions REVIEW with
  an explicit engineer-readiness issue. Failed and budget-deferred second-opinion
  cases are visible; all original fix/dependency checks remain in force.
- Preserve default single-model planning, legacy fallback routing, existing
  feature assessment, immutable worksheets and backport execution logic.
  `--advisor-fallback-model` and selective escalation are mutually exclusive.
- Add targeted mocked routing, disagreement, dependency, failure, budget and
  CLI tests. No live Sol/Terra API inference or kernel build/boot validation.

# kernel-backport 0.1.30 (independent reviewer models and skeptical selection)

- Retain Terra as the planning advisor (`plan --advisor-model`) and choose Sol
  (`gpt-6.1-sol`) by default for final feature assessments. Introduce
  `review --feature-model`, `review --feature-summary-model`, and optional
  corresponding environment defaults. If no summary model override is given,
  use the model recorded in the existing plan (normally Terra), so stage-one
  patch summaries are reused and Sol is reserved for final decisions.
- Make the feature reviewer explicitly skeptical. Require separate
  `selection_decision` (INCLUDE/EXCLUDE/INVESTIGATE), `inclusion_basis`, and
  `exclusion_impact` in the strict output schema. Treat absent positive product
  need as reason to exclude *optional* optimizations/refactoring and cleanup,
  while protecting independently applicable fixes, security, errata and
  unresolved retained-consumer requirements. Keep implementation advice
  distinct from the primary feature-selection decision.
- Retain all original engineer worksheet and ICC safeguards. No autonomous
  TAKE/SKIP changes. Extend feature reports with selection grounds and both
  model names so reviewer provenance is auditable.
- Cache decisions by final reviewer, patch-summary model, and stable skeptical
  policy/prompt/schema identity. Preserve 0.1.29 Terra review records for audit
  without treating them as current Sol skeptical assessments; retain and reuse
  existing Terra patch-segment cache entries. Cache variants coexist.
- Remove `(paid)` markers from segment progress and omit `paid` from new-review
  progress labels. Calls are still billed by the API and remain opt-in.
- Increase final feature response token headroom for explicit selection
  reasoning and long member lists. No modification to planning, application,
  or kernel sources. No live Sol or Terra API call was made in testing.

# kernel-backport 0.1.29 (feature evidence validation and paid-response cache)

- Fix a second live strict-schema/local-validator mismatch: feature output
  evidence lists could be schema-valid but rejected due to an undisclosed
  40-item limit. Remove this limit and enforce only an explicit bounded
  serialized-size safety restriction.
- Constrain each model-generated evidence SHA, member SHA, and minimum set
  to the exact current feature's selected SHAs directly in the strict API
  schema, avoiding an otherwise paid invalid citation.
- Replace `invalid feature evidence` with field-specific indexed diagnostics.
- Save the raw paid feature response to the private, atomic feature assessment
  cache BEFORE local validation. Reuse the saved response on retry, without
  trusting it for worksheet decisions until validation succeeds. Provide
  explicit `--retry-invalid` to request a new billable response if needed.
- No changes to feature grouping, ICC, kernel tree, engineer decisions,
  credentials, or applicability of previous valid feature assessments.

# kernel-backport 0.1.28 (feature segment validation fix)

- Fix a live 0.1.27 failure: a paid Terra segment summary could satisfy the
  strict JSON schema yet be rejected by tighter, undocumented local limits
  (2,500 characters in a prose field, 35 symbols, or 160 characters per symbol).
  Validate the response against the same structure the API was asked to return.
- Retain a generous bounded total summary size, but **do not silently trim**
  evidence. Malformed results now report the specific offending field and
  segment; no false complete-coverage certification is issued.
- Raise the segment's output-token allowance modestly for complex source
  patches. Successful segment summaries are still cached individually and
  reused; 0.1.27's existing valid cache entries remain compatible.
- No change to the worksheet, kernel tree, feature grouping, model-selection
  policy, or plan/ICC requirements. Live Terra was not available for testing.

# kernel-backport 0.1.27 (complete feature evidence and downstream baselines)

- Replace the 0.1.26 feature review's fixed 80,000-character upstream patch
  budget with **lossless per-commit coverage**: complete small patches travel
  inline; large patches are divided into numbered, hash-addressed segments.
  A separate Terra request summarizes each segment, with every segment accounted
  for. There is no silent omission of trailing hunks. Stage summaries include
  original file/hunk context, exact segment hashes and coverage declarations.
  Chunked analysis is derived evidence, not direct full-source inspection;
  recommendations acknowledge that distinction.
- Add read-only **downstream baseline evidence** from the frozen plan's Git
  commit, rather than the current working-tree HEAD. Sample relevant changed
  paths and ICC conflict paths, capture bounded symbol-centered code excerpts,
  record full-source SHA-256 and sample coverage, and run scoped Git symbol
  searches to surface candidates for equivalent implementations. No source
  absence or symbol hit is treated as proof of behavioral equivalence.
- Collect cross-group TAKE and REVIEW consumers and relevant Fixes relations
  when deciding whether a grouped commit can be excluded. Prevent confident
  EXCLUDE for an externally required provider without further investigation.
  Feature assessments identify a primary capability, member roles (core,
  prerequisite, fix, cleanup, optional, unknown), candidate minimum commit set
  and the conditions under which that set is valid.
- Preserve engineer control, source plan, worksheet, ICC evidence, Git HEAD,
  worktree and backport journal. The ordinary `review --features` path performs
  no model calls. `review --assess-features` remains the only billable opt-in.
- Cache individual paid patch-segment summaries separately at
  `.kernel-backport/feature-evidence-chunks.json` using model, prompt and patch
  content digest; partial sessions resume without repeating completed segment
  calls. Keep feature decisions in `feature-assessments.json`, now schema 2.
  Existing 0.1.26 results are retained but marked **stale** until deliberately
  re-assessed with the improved evidence. Reports include evidence coverage.
- Fail visibly rather than silently truncate if a feature exceeds conservative
  request/chunk limits or if Git objects cannot be read. Review output is
  explicit that this is not build, boot or functional validation.
- Regression tests exercise lossless patch splitting, staged summary caching,
  immutable downstream baseline reads, scoped symbol search, cross-group
  obligation guards, legacy cache migration and unchanged Git tree/worksheet.
  Live model calls have not been tested.

# kernel-backport 0.1.26 (feature-chain assessment and shared credentials)

- Add `review --features` for a read-only feature view of selected TAKE commits
  connected by documented prerequisite claims, candidate/provenance links,
  upstream series or Fixes: relationships. Links retain their evidence level;
  incidental file overlap does not prove dependency. A P0 group may include
  non-P0 TAKE consumers and providers.
- Add **explicit billable opt-in** `review --assess-features` and
  `--feature-limit N` to evaluate an entire feature and its potential minimum
  required subset. Terra sees actual patch excerpts for every member,
  product platform policy, selected consumers, downstream status, previously
  cached patch assessments, later-fix obligations and replay outcomes.
  Returns group-level and per-commit RETAIN/ADAPT/EXCLUDE/INVESTIGATE advice,
  value, confidence, evidence, risks and verification actions.
- Preserve engineer authority: no automated SKIP, TAKE edits, Git commits,
  or worksheet mutation. Block unsupported firm exclusions when documented
  consumer or Fixes: obligations remain. No successful replay claim is
  treated as a build/boot result.
- Store evidence-keyed, private feature assessments in
  `.kernel-backport/feature-assessments.json`; report via
  `review-features.txt` and `review-features.json`. Cached results are reused
  unless relevant evidence or model changes. Existing per-commit cache stays
  independent. Plain `--features` has zero API cost.
- Consolidate OpenAI key discovery for plan, per-commit assessment and
  feature assessment. Explicit `OPENAI_API_KEY` has precedence over
  `~/.config/kernel-backport/openai.env`. Parse simple literal .env
  assignments without sourcing shell code or logging credentials. Update
  the example launcher to rely on the common loader.
- Regression coverage includes real Git patch extraction, feature links,
  selected prerequisites, Fixes guardrails, cache reuse/invalidation,
  read-only behavior, terminal/report formatting, and credential precedence.
  Live OpenAI calls are not covered by local tests.

# kernel-backport 0.1.25 (patch-grounded value review)

- Add an explicit, **billable opt-in** `review --assess` workflow. The command
  uses the plan's original OpenAI advisor model (or `--assess-model`) to inspect
  each selected problematic TAKE commit's **actual upstream Git patch**,
  supported-platform matches/exclusions, existing advisor rationale, recorded
  selected TAKE prerequisite consumers, downstream match evidence, later
  `Fixes:` relationships, and current exploratory replay outcome. If a commit
  is missing from the specified local Git repository, assessment stops with an
  error; it will not fabricate patch content.
- Report the independently derived **backport value** (HIGH/MEDIUM/LOW/
  UNCERTAIN) and **integration difficulty indicator** (exploratory replay
  status). Recommend RETAIN / ADAPT / EXCLUDE / INVESTIGATE with confidence,
  technical rationale, source-tagged evidence, next action, and risks.
  Recommendations never override engineer TAKE/REVIEW/SKIP choices.
- Keep the existing REQUIRED / INTEGRATION / OPTIONAL / UNCLASSIFIED and
  P0-P3 priorities as separate, legacy dimensions. Git conflicts alone do
  not decide engineering value. Validate model output, downgrade confidence
  for truncated patches, and prevent unqualified EXCLUDE recommendations
  on commits with known selected TAKE prerequisite claims or Fixes: links.
  Any omission of an applicable later upstream fix still requires proper
  Omitted-fix: justification in the eventual MR.
- Cache assessments individually in `.kernel-backport/review-assessments.json`,
  keyed to commit evidence and model. Resume after interruption without
  repeating unchanged paid calls. Reassess when relevant plan facts change.
  Saved `review-p*.txt` and `review-queues.json` display cached results even
  when `review` runs without `--assess`. JSON schema increments to 4.
- No change to the planner's selection, kernel worktree, ICC, backporter,
  execution journal, Git commit message, or worksheet engineer decisions.
  No automatic external API calls from `plan`, `check`, or ordinary `review`.
- Added regression coverage for patch extraction, evidence, assessment cache,
  invalidation, guardrails, structured output, output wrapping, and read-only
  behavior. Live OpenAI responses are not exercised in local unit tests.

# kernel-backport 0.1.24 (preserve copyable terminal paths)

- Keep terminal and saved progress-log pathnames and URLs intact as one logical
  line, even when they exceed 80 columns. The terminal may soft-wrap them;
  the tool no longer inserts hard newlines that break selection or copy/paste.
- Continue to wrap ordinary progress prose and human-facing review reports to
  80 columns with hanging indentation. Report JSON, patches, commit messages,
  and structured worksheet records remain unchanged.
- Add streaming-output regression coverage for planning results, result
  directories, result archives, nested relative paths, URLs, and prose wrapping.
- No changes to planner selection, ICC, worksheet decisions, or backporter
  execution. Existing plans and journals remain valid.

# kernel-backport 0.1.23 (engineering triage and 80-column text)

- Add evidence-based REQUIRED, INTEGRATION, OPTIONAL and UNCLASSIFIED triage
  annotations to the existing P0-P3 review priorities. Triage does not change
  TAKE/REVIEW/SKIP decisions, ICC obligations, or executor behavior.
  REQUIRED means an engineering obligation must be resolved, not that a
  specific commit is automatically mandatory. Do not infer hard dependencies
  from `Fixes:` or from a replay conflict.
- Show triage confidence, reason and next action in review reports. Link
  related P0 findings by upstream series or exact changed-file overlap,
  explicitly without declaring them prerequisites. Add triage fields and
  per-priority totals to the additive JSON review schema (now schema 3).
- Wrap generated human-facing review reports and terminal diagnostics at
  80 columns with hanging indentation, including both planner and executor
  human console output and their human-readable reports/logs.
- Refresh the worksheet's generated ICC integration header using short
  `take-integration:` and `take-integration-counts:` lines. Maintain the
  parsing grammar and preserve all commit entries and engineer overrides;
  replace old generated summaries atomically on `check`.
- Deliberately do **not** reflow machine JSON/JSONL, Git patches or commit
  messages, source-code snapshots, or parser-sensitive worksheet fields.
  Extremely long structural identifiers in saved worksheets are preserved.
- Include new triage, width, report-writing, and dependency-safety tests.
  Existing plans can be reviewed without rerunning the advisor.

# kernel-backport 0.1.22 (CentOS Stream Jira commit trailer)

- In the backend commit-message formatter used by `apply`, `resume`, and
  direct legacy backports, emit `JIRA: https://issues.redhat.com/browse/<PROJECT_ID.upper()>`
  instead of `Project-ID:` when the downstream kernel source is CentOS Stream.
- Recognize CentOS Stream by a `centos-stream-N` working-tree directory or a
  matching configured downstream URL / Git remote, independent of branch.
- Preserve `Project-ID: <original ID>` for non-CentOS Stream kernels.
- Keep upstream author/header, upstream provenance and downstream signoff
  unchanged. Existing commit history and execution journals remain intact;
  no plan regeneration is required.
- Add tests for CentOS Stream path and remote detection, proper casing,
  generic-source fallback, and the apply/resume format in one journal.

# kernel-backport 0.1.21 (best-effort multi-conflict ICC replay)

- Continue the ICC exploratory cherry-pick pass after conflicts and empty
  picks by resetting only the disposable worktree, preserving earlier clean
  picks. Collect every detectable independent application difficulty in one
  `check` run rather than stopping at the first known conflict.
- Do not attempt TAKEs whose explicit advisor/engineer prerequisites failed or
  were left untested. Record each skipped SHA as `not-tested`, with
  `reason=failed-prerequisite` and `blocked_by` information. Propagate those
  skipped dependencies transitively without treating `Fixes:` provenance as a
  cherry-pick dependency.
- Mark later successful probes **and** later conflicts as `conditional` after
  any failed or skipped commit. Their results reflect a partial temporary
  backport and are not proof of full-sequence applicability or independence.
- Stop on unexpected Git tool errors rather than treating them as normal patch
  conflicts; preserve known evidence and indicate that the pass is incomplete.
  Missing Git objects and logical blockers continue to suppress replay.
- Update the worksheet's generated `take-integration` lines with all observed
  conflict/empty SHAs, conditional counts, prerequisite-blocked counts and
  notes. The ICC JSON retains detailed per-commit diagnosis; the CLI now
  prints every observed application difficulty. Engineer worksheet decisions,
  rationales, advisor evidence, and immutable planner JSON are unaffected.
- Add disposable-repository regression tests for multiple conflicts within one
  pass, recovery and continuation, successful commits across failures, empty
  picks, explicitly blocked dependents, transitive blockers, and preservation
  of the real checked-out kernel tree.
- Existing plans, including `backport_plan_07`, do not require regeneration.
  Run `kernel-backport check --results-dir "$PLAN"` with 0.1.21 to refresh.

# kernel-backport 0.1.20 (live worksheet integration summary)

- `kernel-backport check` now atomically refreshes the existing worksheet's
  generated `take-integration:` header and accompanying `integration-blocker:`
  and `take-integration-note:` lines from its **current** ICC attestation.
  This corrects stale planner-time TAKE counts and conflict diagnostics after
  engineer edits. No advisor rerun or plan regeneration is necessary.
- Report total TAKE, already-applied, replay-clean, conflict, empty-pick, and
  untested counts. Replay halts at its first difficulty: remaining TAKEs are
  explicitly **not tested**, not silently assumed clean. A logical-only
  `check --no-replay` shows `NOT-RUN`, not the previous full replay result.
- Modify no worksheet commit blocks, advisor evidence, engineer decisions, or
  rationales; leave `backport_plan.json` and the checked-out kernel tree
  untouched. Preserve worksheet file permissions and use an atomic write with
  a concurrent-edit check. Existing plans, including `backport_plan_07`, work
  without regeneration.
- Add end-to-end tests for stale-summary replacement, idempotence, preserving
  engineer edits and source JSON, replay conflicts with untested remainder,
  logical blockers, and no-replay refreshes.

# kernel-backport 0.1.19 (user-selected branch names)

- Remove the `--allow-main` option from `apply` and `resume`.
- Accept any checked-out local Git branch, including `main` and `master`,
  without a branch-name-specific restriction. Branch selection belongs to
  the engineer.
- Preserve the existing clean-worktree, exact-baseline (first execution),
  current-ICC, project-ID, repository/branch binding, and journal checks.
- Update the README and regression tests to exercise application and resume
  on `main`, application on `master`, and rejection of stale ICC and invalid
  initial baselines. Existing plans remain usable without regeneration.
- Package entry-point scripts with executable permissions for direct use after
  extraction.
- No changes to planner/advisor logic, commit decisions, or backport ordering.

# kernel-backport 0.1.18 (explicit main-branch execution opt-in)

- Permit the engineer to apply or resume an ICC-checked plan on a deliberately
  dedicated local `main` or `master` branch with `--allow-main`. The prior
  unconditional branch-name refusal was inappropriate for a dedicated fork.
- Keep the default safety refusal without the flag, and retain existing
  exact-baseline, clean-tree, current-ICC, project-ID, and execution-journal
  protections. The opt-in is required for each apply/resume invocation.
- Add integration tests for default refusal, successful application and resume
  on `main`, and rejection of stale ICC even with the explicit opt-in.
- No changes to advisor selection, worksheet decisions, or backport ordering.

# kernel-backport 0.1.17 (reverse-Fixes series discovery and code providers)

- Seed patch-series indexing with missing historical originals newly discovered
  through `Fixes:` relationships *before* independently advising those originals.
  Previously, the original was only marked actionable after the index was built,
  so its earlier series peers and missing source-provider evidence were omitted
  from Terra's packet. Seeding is investigative only: no automatic TAKE or hard
  prerequisite is inferred from a `Fixes:` edge or series membership.
- Alongside struct-member evidence, detect missing definitions of C functions
  modified by selected hunks. Require an actual added hunk in the enclosing
  function, absence of its definition downstream, and an added definition in an
  earlier posted series peer's same source file. Give these evidence IDs to
  Terra for independent minimal-chain/adaptation/selection assessment.
- Bump the advisor packet/cache prompt version to 36. Keep worksheet decisions,
  platform scope rules, ICC `Omitted-fix:` obligations, and execution journal
  semantics unchanged. Add regression tests for both discovery paths.

# kernel-backport 0.1.16 (source prerequisites and integration P0)

- Add bounded missing C-struct-member provider discovery across earlier posted
  series peers. Require an actual new member use, a peer header declaration,
  and absence of that declaration from the downstream header. Pass these
  source-backed candidates to the existing evidence-ID-constrained Terra
  prerequisite assessment. Never infer a hard dependency solely from a name
  match or from series ordering. Bump advisor prompt/cache version to 35.
- Ask Terra to weigh retaining the minimal prerequisite chain, adapting the
  patch for downstream, or reconsidering TAKE when code infrastructure is
  missing. The existing selected-platform, mixed-scope, and `Fixes:` policies
  remain authoritative; no model is called during `check` or `review`.
- Capture bounded original-patch/conflicted-merge excerpts, recorded evidence,
  and next-step guidance for the first full-ICC cherry-pick failure, without
  modifying `main`. Store the diagnosis in `.kernel-backport/icc.json`.
- Escalate current full-ICC replay failures to P0 Critical in the *derived*
  review report even when logical ICC blockers are zero. Keep the diagnosis
  distinct from verified missing prerequisites. Preserve all worksheet choices.
- Include structured preliminary TAKE replay diagnosis in the planner plan
  JSON; keep incomplete replay clearly labeled. Regression tests cover member
  introduction, downstream presence, misleading peer uses, P0 escalation,
  and an actual Git cherry-pick conflict in a disposable repository.

# kernel-backport 0.1.15 (complete historical Fixes: evaluation)

- Include historical original commits that predate `--base` in the reverse
  `Fixes:` index, using verified Git commit identities and ancestry. The
  configured upstream range continues to limit primary candidate discovery,
  not investigation of explicit historical relationships.
- When Terra independently recommends TAKE for an older original, reconsider
  previously REVIEW/SKIP later fixes using the selected original's patch and
  commit context. Refresh stale advisor rationales and preserve both commits'
  independently reasoned decisions. Avoid repeated calls unless the set of
  selected originals has changed; bound reconsiderations by the existing
  fixes-evaluation budget.
- Preserve the earlier fix-completeness/ICC rules, the distinction between
  `Fixes:` and verified hard prerequisites, architecture and excluded-hardware
  safeguards, and downstream presence checking. No YAML or CLI changes.
- Regression tests cover originals older than `--base`, in-range fixes of
  those originals, out-of-scope historical originals, stable no-repeat model
  evaluation, and inclusion of selected older commits in application order.
- Bump the advisor prompt/cache version to 34 and the unified release to
  0.1.15, so stale advisor decisions cannot be reused as if they had the
  complete historical relationship context.

# kernel-backport 0.1.14 (P0-P3 review priorities)

- Replace the three review queues and ambiguous `--queue actionable` label
  with **P0 CRITICAL**, **P1 HIGH**, **P2 NORMAL**, and **P3 DEFERRED**.
  `kernel-backport review` shows P0/P1 by default, summarizes P2/P3, and
  accepts `--priority high|normal|critical|deferred` or `--all`.
- Keep TAKE/REVIEW/SKIP and the engineer worksheet authoritative. P0 is
  deterministic consistency work: missing TAKE prerequisites, unresolved later
  fixes, current ICC logical blockers. P1/P2 are review *importance*, not ICC
  failures; P3 remains unmatched hardware REVIEW, not automatic SKIP.
- Extend the structured advisor response with HIGH/NORMAL review importance
  and an evidence-based rationale. Persist these fields in the plan JSON, use
  them only for P1/P2, and bump advisor prompt/cache version to 33. Existing
  plans without these fields remain readable and use documented fallback rules.
- Provide auditable `priority`, `priority_reason`, and `priority_basis` per
  commit; issue `review-p0.txt` through `review-p3.txt` and `review-queues.json`.
  Remove obsolete generated queue text files on report refresh. A stale ICC is
  flagged explicitly; it cannot certify a zero-P0 result.
- No change to the platform YAML schema, the ICC algorithm, or the backporter
  execution journal.

# kernel-backport 0.1.13 (correct terminology: Internal Consistency Check / ICC)

- Correct the acronym to **ICC**, consistently across the CLI help, diagnostics,
  review reports, internal identifiers, and documentation. The public command
  remains `kernel-backport check`; no planner or advisor selection rules change.
- Rename the generated check attestation to `.kernel-backport/icc.json` and the
  review-queue freshness field to `icc_current`. Existing plan data and engineer
  worksheet overrides remain unchanged; rerun `check --no-replay` once for an
  ICC attestation in existing plan directories.
- Update automated tests for the corrected terminology and output paths.

# kernel-backport 0.1.12 (terminology, circular Fixes: review, SoC-aware queues)

- Updated the `check` command help to expand the Internal Consistency Check
  name. Version 0.1.13 subsequently corrected its abbreviation and report path.
- Resolve circular `Fixes:` evaluation: an in-scope REVIEW fix can now cause
  its missing, relevant original to be independently evaluated. If Terra
  selects that original TAKE, the follow-up pass reconsiders the fix with the
  original's patch and commit context, without creating a hard prerequisite.
  Existing limits and foreign-architecture/unsupported hardware safeguards
  remain in force. Increment advisor prompt/cache version to 32.
- Improve the *derived* review queues with specific SoC identity detection
  across DT binding YAML, per-SoC headers, SoC Device Tree files and narrow
  defconfig-only changes. Defer only when there is no supported-platform
  match, conflicting scope, or required TAKE consumer. Shared/generic binding
  schemas and real source changes remain engineering reviews.
- Embed active positive platform entries in new plan metadata to protect
  in-scope hardware identities even when a specific change lacks direct
  matching evidence. Older plans without this optional metadata continue to
  work using recorded positive match evidence. No YAML schema change.
- Preserve the worksheet's TAKE/REVIEW/SKIP decisions, ICC later-fix
  completeness rules, CLI arguments, and execution journal format.

# kernel-backport 0.1.11 (prioritized engineering review queues)

- Add `kernel-backport review --results-dir PLAN --queue actionable|all|action-required|engineering|deferred-hardware [--format text|json]`.
  Separate engineering attention priority from TAKE/REVIEW/SKIP decisions;
  do not rewrite immutable planner JSON or engineer worksheet.
- Automatically defer REVIEW commits that touch only unmatched board/SoC DT
  source (optionally accompanied by a shared DTS Makefile). Unchanged DTS
  includes are not changes to common code. Mixed source/driver changes and
  positive platform matches remain in engineering review.
- Escalate known later fixes, unsatisfied selected TAKE prerequisites, advisor
  failures, contradictory scope, and current ICC logical blockers to an
  action-required queue. A reviewed SKIP cannot hide a selected dependency.
- Group deferred hardware by discovered patch-series ID, with mixed-series
  shared-code members separated rather than auto-deferred.
- Produce review-action-required.txt, review-engineering.txt,
  review-deferred-hardware.txt, and review-queues.json alongside planning
  results. Create reports before automatic ZIP packaging, and refresh after
  engineer decisions, additions, ICC, or a direct `review` command.
- Keep advisor prompt/cache version 31 (selection policy unchanged), schema-v2
  YAML and all active product scopes, `Fixes:` completeness rules, CLI plan
  arguments, and execution journal format unchanged.

# kernel-backport 0.1.10 (bidirectional Fixes: review and Kaanapali scope)

- Add reverse Fixes: evaluation for relevant missing original commits named by
  a selected TAKE fix. Terra receives the original patch plus selected-fix
  context and decides TAKE/REVIEW/SKIP independently. No `Fixes:` edge becomes
  an automatic hard prerequisite or mandatory original TAKE.
- Skip evaluation for demonstrably foreign-architecture-only or explicitly
  excluded original commits. Reevaluate previously reviewed relevant originals
  in light of a selected fix. Repeat reverse and later-fix evaluation to a
  bounded fixed point so selecting an original also evaluates its other later
  corrections. Preserve existing CKI/MR later-fix completeness enforcement.
- Avoid needlessly reevaluating an already selected TAKE fix during reverse
  closure. Track advisor evaluation budgets across rounds, expose incomplete
  closure as an execution-readiness issue, and retain verified prerequisites.
- Filter standalone ICC `FIXES_TARGET_NOT_SELECTED` warnings for original
  commits confined to foreign `arch/<other>/` trees when the plan explicitly
  selects a target architecture. Mixed/common-code originals still warn.
- Ship an updated schema-v2 `rhel-10-platforms.yaml` with all four user-selected
  product scopes enabled and one additional explicit Kaanapali Device Tree
  exclusion. The Makefile-only safeguard and mixed-file protection remain.
  YAML schema, public CLI, execution journal, and worksheet format are unchanged.
- Increment the advisor prompt/cache version from 30 to 31; no live model
  evaluation is performed as part of release tests.

# kernel-backport 0.1.9 (uniform later-fix policy for every plan)

- Remove the advisor prompt-version check from standalone ICC. Every plan is
  now subject to the same `UNRESOLVED_LATER_FIX` MR-readiness rule, whether
  it was generated by an earlier release, contains old/malformed advisor
  metadata, or has no advisor version metadata at all.
- Known missing fixes for selected TAKE commits must be selected TAKE,
  established as already downstream, or explicitly marked engineer-SKIP
  with a nonempty technical rationale. Justified omissions continue to
  generate `Omitted-fix:` entries in `.kernel-backport/icc.json`.
- `Fixes:` remains historical provenance, not a hard application prerequisite.
  Preserve that provenance when a candidate-only fix from the immutable plan
  is manually added to the worksheet for an explicit omission. Commit identity,
  execution journal and report schema validation remain intact; no
  version-driven plan compatibility exceptions remain.
- No planner selection policy, prompt/cache version, YAML schema, CLI options,
  or backporter behavior changed. Existing worksheets and plan JSON files are
  never rewritten by ICC. Adds regression coverage for plans with old,
  missing, and malformed prompt-version metadata and candidate-only fixes.

# kernel-backport 0.1.8 (dependency value and later-fix completeness)

- **Scope-aware dependency tradeoff:** When a selected TAKE commit requires a
  whole-cherry-pick of an unmatched/excluded board or SoC provider, the planner
  supplies Terra with both patches, overlapping files, source relevance and
  whether the consumer is whitespace-only. Terra reconsiders whether the
  consumer's benefit justifies the out-of-scope prerequisite. It can recommend
  SKIP for nonessential cleanup, REVIEW for a partial manual backport, or TAKE
  for essential functionality/security. If the consumer changes from TAKE,
  orphaned providers are reconsidered rather than retained automatically.
  Real prerequisites remain evidence-backed; no silent partial cherry-picks.
- **Follow-up fixes:** After the initial recommendations, evaluate *all known*
  later upstream commits with `Fixes:` referencing a selected TAKE, including
  corrections to newly selected fixes. Previously informational-only leads
  become independently evaluated advisor candidates, not hard dependency
  edges. Downstream-present fixes are excluded. Bounded rounds and work
  limits surface unresolved candidates instead of silently ignoring them.
- **MR completeness in standalone ICC:** For plans generated with advisor
  prompt version 30 or newer, an unselected, missing later fix is an
  `UNRESOLVED_LATER_FIX` MR-readiness blocker until it is selected TAKE,
  verified present downstream, or explicitly engineer-SKIPped with a technical
  rationale. Explicit omissions appear in ICC as `Omitted-fix:` trailers and
  `omitted_fixes` JSON entries. A `Fixes:` relationship does *not* become a
  hard application prerequisite. Existing 0.1.7/older plans retain their
  prior warning-only policy.
- **ICC revision safety:** Changing an engineer's omission rationale now
  invalidates the previous ICC decision revision, even when the SKIP choice
  itself has not changed.
- **Compatibility:** No changes to platform-profile schema v2, backporter
  execution-journal schema, or normal launcher options. Keep your locally
  enabled RHIVOS and RHEL for NVIDIA product scopes. Prompt/cache version
  increases from 29 to 30 to avoid reusing earlier recommendations.
- Includes regression cases for Kaanapali/Lemans tradeoffs, first- and
  second-order fixes, and explicit omitted-fix ICC approval.

# kernel-backport 0.1.7 (target architecture and SoC relevance)

- `--architecture` now applies to *this backport plan*, not every supported
  RHEL architecture. A foreign-architecture-only commit stays SKIP unless
  verified TAKE dependency evidence requires evaluation; a foreign-architecture
  commit also touching generic code becomes REVIEW without target-specific
  proof. Neither a generic file name nor hunk-preimage provenance proves TAKE.
- During dependency discovery and conflict follow-up, suspect foreign-arch
  dependencies are recorded as informational `architecture-scope-candidate`
  nodes rather than automatically promoted into recursive dependency replay.
  Verified shared symbol providers used by target-architecture code and exact
  DT source include prerequisites retain their existing evaluation paths.
- New `UNMATCHED_SOC` relevance status for commits confined to SoC `.dtsi`
  infrastructure without a positive platform-profile match. They become
  REVIEW instead of being mistaken for generic architectural infrastructure.
  Independently verified prerequisites and explicit platform matches remain
  eligible for TAKE.
- Extends architecture source ownership for clear `drivers/platform/<arch>/`
  and `drivers/s390/` code, without inferring architecture from ordinary
  vendor driver trees.
- Updates the advisor instructions and bumps the prompt/cache version from 28
  to 29. Adds regression cases for x86 CFI plus generic header, unmatched
  Qualcomm/Allwinner/Rockchip SoC DTSI, required DT includes, and discovery.
- No plan/worksheet/journal schema migration. No additional CLI arguments.
  RHEL platform YAML schema v2 remains unchanged; keep your locally enabled
  RHIVOS and RHEL for NVIDIA scopes.

# kernel-backport 0.1.6

- **Platform profile schema v2:** new required `product_scope`, `support_basis`,
  and `match_scope` per entry, plus explicit `active_product_scopes` top-level.
  Inactive RHIVOS/RHEL for NVIDIA product profiles remain documented, but do
  not automatically assert standard RHEL 10 scope.
- **Relevance evidence:** positive path/identifier matches carry provenance
  categories into the advisor packet, including weaker RHEL 9 and upstream
  reference-board proxies. The advisor is reminded that a source match is not
  proof of certified hardware.
- **Safe exclusions:** a DTS Makefile-only change cannot trigger a negative
  board-only match. Full-path negative matching for actual excluded board or
  driver changes remains intact.
- **Azure guest match:** restrict `drivers/hv/*` to guest-side VMBus/service
  paths to avoid selecting host-side `mshv` code as Azure guest evidence.
- **RHEL profile:** preserves 24 platform entries and 29 negative board entries,
  their research caveats, and `unmatched_board: REVIEW`. Activates standard
  RHEL 10 and engineer-selected targets by default. File supplied separately
  and in `profiles/rhel-10-platforms.yaml`.
- **Versioning/tests:** single 0.1.6 version; prompt cache version 28 and
  new regression tests. No live OpenAI calls or full CS10 Git run performed.

# kernel-backport 0.1.5 (supported-platform upstream convergence)

- Rebalances advisor policy: favor upstream convergence for relevant shared
  architecture and subsystem code and explicitly selected RHEL platforms.
  Bug fixes, optimizations, cleanup, and feature improvements do not need a
  separate downstream bug report to be recommended TAKE.
- Makes unsupported hardware the principal exclusion criterion. Respect
  explicitly excluded board/driver-only changes (all changed paths must be
  excluded), and unsupported architecture-only changes;
  preserve profile-controlled REVIEW for unfamiliar boards and never infer
  RHEL support merely from a similar SoC name.
- Prefer carrying the complete applicable original/fix sequence for in-scope
  changes. `Fixes:` remains informational provenance, not proof of a hard
  prerequisite or an automatic order/selection constraint.
- Enriches `commit.fixes` and `commit.fixed_by` advisor evidence with downstream
  status and actionable-candidate status so the model can distinguish absent,
  already-present, and non-selected peers without guessing. Unindexed peers
  are explicitly marked unknown.
- Updates the deterministic direct-focus default bias to
  `FAVOR_IN_SCOPE_CONVERGENCE`, while preserving strict board/architecture
  policy overrides and independently proven prerequisite checks.
- Changes policy rechecks to use the same convergence/scope rules and bumps
  advisor prompt version to 27, invalidating model answers cached under 0.1.4.
- Adds policy regression coverage for arm64, x86, powerpc, s390, board
  exclusions, uncertain boards, unsupported architectures, and Fixes context.
- No plan-schema migration, execution journal rewrite, or new command-line
  arguments. Existing worksheets remain valid. This is a recommendation-policy
  update, not a claim of model-evaluated quality on a full CS10 planning run.

# kernel-backport 0.1.4 (Fixes provenance is not a hard prerequisite)

- Corrects planner dependency semantics: `Fixes:` tags and reverse `fixed-by`
  relationships are informational regression provenance, not proof that the
  referenced commit must be backported. They no longer manufacture
  `required_by` edges, force dependent candidate selection, or constrain
  cherry-pick ordering solely on that basis.
- Preserves `Fixes:` context in the worksheet (`fixes-reference`, `fixed-by`)
  and JSON (`fixes`, `fixes_referenced_by`, semantics field). Independent,
  evidence-backed prerequisites retain their hard dependency behavior.
- Adds two non-blocking standalone ICC diagnostics: a selected fix with a
  missing unselected target and a selected patch with a known unselected fix.
  Both prompt engineer review without blocking controlled execution.
- Removes the bundled backporter's independent `Fixes:` target auto-selection
  and conflict-candidate score bonus; real hunk/symbol/build evidence can still
  promote that commit when warranted. Direct-commit reports retain Fixes refs.
- Updates advisor instructions and increments the prompt/cache version to
  prevent the previous prerequisite interpretation from being reused.
- Adds local-Git regression tests for Fixes-only, proven hard prerequisites,
  both ICC warnings, and direct backporter preflight behavior.
- Does not modify existing plans, worksheets or execution journals. New planner
  semantics take effect when a fresh plan is generated.

# kernel-backport 0.1.3 (conservative selection and engineer additions)

- Revises the advisor's policy to prefer a minimal, evidence-supported backport
  set. Upstream convergence, path matching, or clean cherry-picks are not by
  themselves sufficient for TAKE; optional board and peripheral enablement is
  normally SKIP absent a stated target requirement.
- Retains REVIEW for material unresolved questions, and preserves verified
  prerequisites of TAKE selections instead of silently dropping them.
- Bumps the planner prompt/cache version so previous TAKE-biased decisions
  are not inadvertently reused on a new planning run.
- Adds `kernel-backport add COMMIT --reason ... [--decision TAKE|SKIP|REVIEW]
  [--required-by SHA] [--requires SHA]`, which introduces a locally available
  upstream commit without rewriting the advisor's original JSON or paying for
  a new model run.
- Stores engineer additions and dependency edges in a plan-digest-bound
  overlay and makes them visible to the worksheet, ICC ordering, and executor.
- Supports adding commits after previous phases have completed, with a fresh
  ICC revision and a new immutable execution phase on resume.
- Loads new engineer commits from the local kernel Git object database into
  a stale backporter reference clone without contacting the network.
- Warns if an engineer-added commit lies outside the original upstream target;
  the explicit engineer selection is required to permit its backport.
- Adds Git-based regressions for manual additions and iterative ICC/execution.

# kernel-backport 0.1.2 (one public release version)

- Uses one authoritative `VERSION` file for the consolidated executable and both bundled engines.
- `kernel-backport --version`, `kernel-backport plan --version`, and the legacy engine entry points now all display `kernel-backport 0.1.2`.
- Planner progress logs, performance event metadata, and newly generated plan `version` fields use the same version as the application.
- Removes the planner/executor version suffix from the unified `--version` output. Historic engine version numbers are kept only as source provenance in earlier release notes.
- Makes no intentional changes to planner heuristics, Git operations, plan schema, advisor parameters, or executor behavior.
- Adds version consistency regression coverage.

# kernel-backport 0.1.1 (generic project IDs and environment integration)

- Replaces `--jira` with generic `--project-id` in both the unified CLI and bundled backporter engine; defaults to `$PROJECT_ID`.
- Writes a generic `Project-ID: <id>` in applied downstream commit messages instead of a hard-coded Jira URL.
- Binds project ID to execution state, individual phase snapshots, and journal validation; refuses cross-project resume.
- Validates project IDs to prevent malformed commit metadata or multiline injection.
- Updates the launcher to use `$KERNEL_BACKPORT` from the user's environment and documents the source installation layout.
- Adds regression tests for environment defaults, commit metadata and project mismatch handling.

# kernel-backport 0.1.0 (initial unified workflow)

Source imports: user's `kernel-backport-planner-0.4.37(1).zip` and
`kernel-backporter.zip`, both provided for consolidation.

- Retains the exact planner 0.4.37 and backporter 0.2.9 implementations and
  their test suites; no unreviewed edits to their backport heuristics.
- Adds the `kernel-backport plan|decide|check|apply|resume|status` CLI.
- Adds standalone, repeatable ICC on effective engineer TAKE decisions.
- Adds worksheet overrides for TAKE, SKIP and REVIEW, without overwriting
  original advisor recommendations.
- Adds immutable per-revision execution phases and original backporter journals;
  supports re-promoting a previously skipped commit after another phase.
- Adds versioned attestation, stale-ICC checks, branch protection, lock,
  write-atomic state updates and SHA/HEAD baseline guards.
- Adds disposable-repo regression tests for successful execution,
  blocked-commit revision, skip->take promotion, and stale/contradictory plans.

Important: this release is **integration-test validated**, not yet proven on a
full real CS10 v6.19..v7.0 run. Apply on a dedicated branch, begin with
`--max-attempts 1`, review each resulting downstream commit, and use normal
build/boot/regression validation. This is not an unattended kernel backporter.
