# kernel-backport

`kernel-backport` is a consolidated tool for planning, reviewing, and executing
Linux kernel backports. It discovers upstream changes relevant to a downstream
kernel, helps an engineer select a defensible set of commits, checks the
selection for internal consistency, identifies likely integration problems,
and applies approved patches with an auditable execution journal.

The objective is **the smallest technically sound backport for the supported
product**, not the largest upstream patch series that can be cherry-picked.
The tool assists engineering judgment; it does not replace it.

The distribution provides one `kernel-backport` entry point around the bundled
planning and backport execution engines. Paths, target architecture, platform
scope, upstream range, downstream baseline, and advisor configuration are
inputs, not hard-coded ARM64 assumptions. It can be used for RHEL/CentOS
Stream work and adapted to other supported Linux kernel targets.

Kernel compilation and runtime validation are **outside** this tool. Use a
separate build workflow, such as `kernel-build`, and the product's normal
qualification process.

## Why kernel-backport instead of a general-purpose AI agent?

An AI coding agent can inspect Git history, propose commits, resolve conflicts,
and run builds. For a small backport, that may be enough. For a large or
long-running backport, the challenge is also to maintain a defensible selection
and a reproducible record of what was planned, decided, applied, adapted, and
validated.

`kernel-backport` provides deterministic scaffolding for that work. Git-based
analysis, explicit product scope, recorded dependency and `Fixes:` evidence,
frozen plans, consistency checks, and guarded execution journals persist
independently of any AI model or conversation. The journal can reconcile an
engineer's manually adapted commit and resume at the next pending change
without relying on an AI agent to reconstruct execution history.

AI advisors contribute where semantic judgment is useful, such as assessing
platform relevance, prerequisites, subsequent fixes, and integration risks.
Their recommendations and rationales are reviewable evidence, not instructions
to apply blindly. Engineers retain authority over the selected changes,
conflict resolutions, and final validation. The underlying process does not
depend on a particular model or its ability to remember earlier sessions.

The objective is **not to replace kernel engineers with AI**, but to give them
a controlled, resumable, and auditable process in which AI can contribute
without becoming the sole authority. A complete execution journal is not a
proof of kernel correctness; builds, boot tests, kABI checks, and product
qualification remain essential.

## The workflow and its questions

Keep these stages distinct:

| Stage | Question | What it establishes |
| --- | --- | --- |
| **Planning** | Which upstream changes might matter to this downstream kernel? | A candidate plan and supporting evidence. |
| **Conditioning** | **Should we backport this commit?** | Whether the capability belongs in the product and which commits are actually needed. |
| **Exploratory replay** | **What difficulties should we anticipate?** | Likely cherry-pick conflicts, empty picks, and adaptation work in a disposable checkout. |
| **Execution** | **How do we actually integrate the required changes?** | Real downstream commits, conflict resolution, and an auditable journal. |
| **Validation** | **Does the resulting kernel work correctly?** | Evidence from builds, boot and runtime tests, platform coverage, kABI review, and other required checks. |

Conditioning asks whether a change **belongs** in the backport. Replay asks
whether and how it **applies**. A clean cherry-pick does not justify inclusion;
a conflict does not justify exclusion. A successful application is not proof
that the downstream kernel builds or behaves correctly.

The practical sequence is:

1. **Plan:** Identify candidate changes, supported platforms, dependencies,
   and later upstream fixes. Save the original plan and advisor evidence.
2. **Condition:** Review the selected functionality skeptically. Preserve
   necessary fixes, consumers and prerequisites while excluding unsupported
   enablement, orphaned providers, and unnecessary refactors.
3. **Adjudicate:** Resolve remaining advisor disagreements and engineering
   exceptions with explicit, justified TAKE/SKIP decisions.
4. **Check:** Run the Internal Consistency Check (ICC). Optionally perform
   exploratory replay to estimate integration difficulty.
5. **Apply:** Execute the approved selection on the real downstream branch,
   resolving conflicts and resuming through the execution journal.
6. **Finish:** Once every TAKE is accounted for, verify and freeze the
   execution record, handing the resulting patch series over to Git.
7. **Validate and review:** Build, test, inspect kABI and downstream
   integration, and document outstanding obligations for the MR.

These activities can be iterative. An engineering decision changes the
*effective* TAKE set and makes the previous ICC attestation stale. Recheck
before execution. Do not modify kernel source merely to quiet selection or
advisor warnings; source adaptations belong in the execution phase.

## Installation and prerequisites

Install the **entire** `kernel-backport/` directory from the distribution,
including its bundled engines, profiles, and Python modules. The launcher
finds its companion files relative to itself, including when reached through
a symlink. The tool requires Python 3, Git, and the planner's Python
dependencies, including PyYAML.

For example, after extracting the archive:

```bash
mkdir -p "$HOME/.local/opt" "$HOME/bin"
cp -a kernel-backport "$HOME/.local/opt/kernel-backport"
ln -s "$HOME/.local/opt/kernel-backport/kernel-backport" \
    "$HOME/bin/kernel-backport"
python3 -c 'import yaml'
kernel-backport --version
kernel-backport --help
```

Inspect existing destinations before replacing an installation or symlink.
The root `VERSION` file is the authoritative version for the consolidated CLI
and bundled engines; individual engines do not need separate installation.
The older entry points remain available for compatibility:

```bash
kernel-backport plan --help
kernel-backport legacy-backport --help
```

Do **not** mix direct `legacy-backport` execution with consolidated
`apply`/`resume` on the same branch. They maintain different journals.

### Project workspace

Set a project identity, kernel repository, and results location. For a
CentOS Stream workspace, a shell configuration might contain:

```bash
export KERNEL_BACKPORT="$HOME/.local/opt/kernel-backport/kernel-backport"
export PROJECT_ID="my-kernel-project"
export KERNEL_WORKSPACE_DIR="$HOME/projects/$PROJECT_ID"
export KERNEL_REPO="$KERNEL_WORKSPACE_DIR/centos-stream-10"

FILE_LIST="$KERNEL_WORKSPACE_DIR/file_list.txt"
PLATFORMS="$KERNEL_WORKSPACE_DIR/rhel-10-platforms.yaml"
```

`PROJECT_ID` binds execution phases and supplies project attribution. Do not
change it in the middle of an execution journal. The example paths and names
are illustrative; configure them for the actual kernel project.

Supply two planning inputs:

- **`file_list.txt`:** Focus paths for discovering commits. This is **not**
  a strict allowlist: real prerequisites and subsequent fixes can touch
  files outside the initial path list.
- **`rhel-10-platforms.yaml` (or another platform profile):** Positive and
  excluded platform evidence, product scope, and supported hardware. The
  bundled `profiles/rhel-10-platforms.yaml` is a starting example, not an
  assertion that every listed system is supported by your downstream product.

The platform profile's v2 schema uses `active_product_scopes` and, for each
positive or excluded entry, `product_scope`, `support_basis`, and
`match_scope`. A matching path, board, or SoC is a relevance signal, **not**
proof that a commit belongs in the backport. Validate active product scopes
and exclusions against the actual support policy before planning.

### OpenAI API credentials

OpenAI-backed planning and explicitly requested model assessments use an API
key and incur API charges **separately from any ChatGPT subscription**. An
already exported `OPENAI_API_KEY` takes precedence. Otherwise, the tool
reads a literal assignment from
`~/.config/kernel-backport/openai.env` automatically:

```bash
install -d -m 700 "$HOME/.config/kernel-backport"
${EDITOR:-vi} "$HOME/.config/kernel-backport/openai.env"
# Enter this in the file, replacing the placeholder:
# OPENAI_API_KEY='your-api-key'
chmod 600 "$HOME/.config/kernel-backport/openai.env"
```

Do not `source` this file or commit it to Git. Logical ICC, local replay,
local decision changes, status, and journaled execution do not require model
calls. A request-count limit controls **requests**, not dollar expenditure;
reusable valid assessments and patch summaries are cached.

## 1. Generate a plan

The planner discovers changes between an upstream base and target, examines
what the downstream already contains, evaluates product relevance, and records
TAKE, REVIEW, or SKIP recommendations. It also looks for prerequisites,
downstream equivalents, and applicable subsequent upstream `Fixes:` commits.
The original plan is evidence, not an unchallengeable final decision.

One example for an ARM64 CentOS Stream backport is:

```bash
cd "$KERNEL_REPO"
"$KERNEL_BACKPORT" plan \
    --repo "$KERNEL_REPO" \
    --update --upstream linux-stable --downstream cs10 \
    --downstream-ref HEAD \
    --base v6.19 --target v7.0 \
    --file-list "$FILE_LIST" --platforms "$PLATFORMS" \
    --architecture arm64 \
    --advisor openai --advisor-model gpt-6.1-terra \
    --advisor-escalation-model gpt-6.1-sol \
    --advisor-escalation-policy selective \
    --advisor-escalation-limit 96 \
    --advisor-trace
```

Change the remotes, tags, architecture, product profile, and model settings
for your project. With the example model policy, Terra makes the initial
recommendations and selected ambiguous or risky cases receive an independent
Sol review. A Sol disagreement or failed/deferred escalation is **not** an
automatic override; unresolved advisor readiness issues remain visible for
the engineer. The planner retains model evidence and rationales.

An example project-specific wrapper is provided at
`examples/run-backport-planner`; adapt it rather than assuming its paths,
remotes, or architecture match yours. The planner normally creates numbered
runs and snapshots under `$KERNEL_WORKSPACE_DIR/backport-plans/`.

Select the actual results directory returned by planning:

```bash
PLAN="$KERNEL_WORKSPACE_DIR/backport-plans/backport_plan_NN"
"$KERNEL_BACKPORT" status --results-dir "$PLAN"
```

Keep the frozen source plan (`backport_plan.json`) and its evidence. Later
engineer choices are overlaid in the worksheet or associated state, rather
than silently rewriting the original planner result.

## 2. Condition the backport

Conditioning is a **product and feature selection** exercise, not an attempt
to make every selected patch apply cleanly. For each related group of commits:

- Decide whether the capability is useful or necessary on supported
  hardware, shared subsystems, and guest environments.
- Separate functional changes, independent correctness/security fixes,
  adapters, true prerequisites, optimization, and incidental cleanup.
- Compare the upstream changes with the frozen downstream sources and
  existing equivalent functionality. Avoid partial migrations that remove
  functionality or hardware acceleration.
- Determine the *smallest sound implementation*. An unsupported board
  enablement or an unrelated refactor does not become mandatory merely
  because it appears in the same upstream series.
- Check later upstream corrections, consumers, and known dependency edges.
  Patch overlap and `Fixes:` provenance alone do not establish a hard
  prerequisite.

Start with local, nonbillable diagnostics and review queues:

```bash
"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
"$KERNEL_BACKPORT" review --results-dir "$PLAN" --all
"$KERNEL_BACKPORT" review --results-dir "$PLAN" --all --features
```

`check` exits with status 2 when logical or advisor readiness blockers remain.
That is an engineering issue to resolve, not permission to execute. The
`review` priorities are P0 critical, P1 high, P2 normal, and P3 deferred;
these are **review queues**, not additional TAKE/SKIP decisions. P0 may be
empty while important questions remain in other queues.

### Optional, guarded feature assessment

The `condition` command can obtain skeptical feature-level assessments and
produce auditable candidate SKIPs. It uses Sol by default for final feature
review and the recorded planning model (often Terra) for oversized patch
summaries. It does **not** make an API request without explicit `--assess`,
and it does **not** change TAKE/SKIP decisions without explicit
`--apply-auto-skips`.

For a small, reviewable assessment batch:

```bash
# Billable; assessment only. Allow inspection before all initial blockers
# are resolved. Existing blockers are not waived.
"$KERNEL_BACKPORT" condition --results-dir "$PLAN" \
    --scope all --assess --allow-unresolved \
    --max-new-features 5 --max-api-requests 20

# Read the cached decision dossier; no new API call.
"$KERNEL_BACKPORT" condition --results-dir "$PLAN" --report-only

# Optional: commit only safe, cached automatic SKIPs to the worksheet.
"$KERNEL_BACKPORT" condition --results-dir "$PLAN" \
    --scope all --allow-unresolved --apply-auto-skips
```

The default scope covers critical groups; `--scope all` includes every
selected TAKE feature group. `--max-new-features` and `--max-api-requests`
limit new assessment work; `0` means unlimited. A combined
`--assess --apply-auto-skips` pass is also supported. New decisions prompt
rechecking and can change the feature evidence, so repeated conditioning
may be appropriate.

Automatic SKIPs are deliberately conservative. They must satisfy role,
product-value, provenance, retained-consumer, and ICC safety constraints.
Existing explicit engineer selections, applicable fixes, correctness and
security concerns, ambiguous roles, invalid model output, and outstanding
verification obligations cannot be waved away by a confident-sounding model.
Automatic decisions have their **own provenance**, distinct from engineer
approval. Invalid paid responses and their reasons are retained for review;
retrying them is a separate, explicitly billable `--retry-invalid` action.

For unresolved **Terra/Sol REVIEW disagreements**, the optional
`--auto-skip-disagreements` switch (with `--allow-unresolved`) adds separate
review groups. Only a validated, high-confidence Sol recommendation to
exclude a LOW-value OPTIONAL/CLEANUP item, without outstanding obligations,
can yield an automated REVIEW-to-SKIP disposition. Other disagreements,
errors, and budget deferrals stay as engineering exceptions. The tool never
automatically promotes such a REVIEW to TAKE.

For deeper investigation of a particular priority queue, the independent
feature reviewer is also available through `review`:

```bash
"$KERNEL_BACKPORT" review --results-dir "$PLAN" \
    --priority high --assess-features --feature-limit 1
"$KERNEL_BACKPORT" review --results-dir "$PLAN" --all --features
```

Feature assessments require current full-ICC replay evidence; `condition`
refreshes it when needed. These assessments are advisory and are cached;
`review` does not rewrite selections. Read `review-features.txt` and
`review-features.json`, as well as the conditioner reports.

## 3. Resolve engineering exceptions

The engineer owns the final selection. Inspect actual upstream diffs, frozen
downstream code, supported consumers, prerequisites, and later fixes before
changing a recommendation. Record a factual, one-line reason for every
explicit override:

```bash
"$KERNEL_BACKPORT" decide --results-dir "$PLAN" COMMIT_SHA TAKE \
    --reason 'Required by a supported downstream consumer'
"$KERNEL_BACKPORT" decide --results-dir "$PLAN" OTHER_SHA SKIP \
    --reason 'No required supported-platform consumer'
"$KERNEL_BACKPORT" review --results-dir "$PLAN" --all
"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
```

These SHAs and reasons are illustrative; do not use them without examining
the specific commit. `decide` records decisions in the worksheet, preserves
original advisor judgments, and invalidates stale ICC attestations. A model
disagreement remains an advisor readiness blocker until the engineer resolves
it with a justified TAKE or SKIP (or an eligible guarded automatic SKIP).

### Add a missing prerequisite or later fix

If planning missed a genuinely needed commit, first make its upstream Git
object available in the local kernel repository. Add it to the existing plan
with an explicit reason and optional verified dependency relationships:

```bash
"$KERNEL_BACKPORT" add --results-dir "$PLAN" NEW_SHA \
    --decision TAKE \
    --reason 'Needed by a retained supported-platform change' \
    --required-by SELECTED_CONSUMER_SHA
"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
```

`--required-by` means an existing consumer needs the new commit;
`--requires` means the new commit needs an existing prerequisite. These
represent actual implementation dependencies, not adjacent commits in a
series. The operation adds a worksheet entry and a structured engineer
overlay (`.kernel-backport/additions.json`), preserving the frozen source plan.

A later upstream fix can be added **without changing the original planning
target**, provided the engineer explicitly authorizes an out-of-target
addition and its Git message verifies the claimed `Fixes:` trailer:

```bash
"$KERNEL_BACKPORT" add --results-dir "$PLAN" LATER_FIX_SHA \
    --decision TAKE --allow-outside-target \
    --fixes EARLIER_SELECTED_SHA \
    --reason 'Applicable later upstream correction to a selected change'
"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
```

`--fixes` records correction provenance; it is **not** a cherry-pick
prerequisite. The tool rejects a claimed `Fixes:` relationship that the
actual upstream Git message does not support. Repeating an identical `add`
is a no-op; changing an existing addition requires deliberate engineering
review rather than a silent replacement. Both `--requires` and
`--required-by` can be repeated when evidence supports multiple edges.

When a relevant subsequent fix is intentionally omitted, record the technical
reason and provide the appropriate `Omitted-fix:` explanation in the CentOS
Stream merge request when that workflow requires it. An unexplained omission
is not equivalent to a reviewed exclusion.

## 4. Run ICC and exploratory replay

The **Internal Consistency Check** inspects the current effective TAKE set,
including engineer changes and already applied commits. It checks known
prerequisites, dependency ordering and cycles, selected later-fix obligations,
and unresolved advisor readiness. ICC reports both logical blockers and
advisor readiness blockers; a count of zero in one category says nothing
about the other.

A fast consistency check avoids cherry-pick simulation:

```bash
"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
"$KERNEL_BACKPORT" status --results-dir "$PLAN"
```

A full check adds **exploratory replay** in a disposable Git worktree based
on the verified downstream baseline (or current journaled execution point):

```bash
"$KERNEL_BACKPORT" check --results-dir "$PLAN"
```

Replay tries the pending commits in a stable prerequisite-aware order. It
records clean, conflicting, empty and untested results. When an earlier
commit fails, later independent attempts may be made on a partial replay
tree; such outcomes are **conditional**. The real development branch is not
modified. A reported conflict may require source adaptation, a missing
prerequisite, or a genuinely inapplicable change, and needs investigation.
It is **not**, by itself, a reason to SKIP.

A current **logical-only ICC with zero logical and advisor blockers** is
sufficient for `apply`/`resume`; a conflict-free exploratory replay is not
an additional execution gate. A replay *tool error*, however, must be
corrected. Full replay is important for integration planning and for
feature-assessment evidence. ICC is not a build, boot, test, kABI audit,
or guarantee of semantic correctness.

ICC writes `.kernel-backport/icc.json` and refreshes the *generated*
`take-integration` summary in `backport_worksheet.txt`. It does not alter
manual worksheet decisions or the original source plan. When the TAKE set,
engineer rationale, or dependency overlay changes, rerun ICC before applying
or resuming.

## 5. Execute the approved changes

Execution changes the **real downstream Git branch**. Begin at the exact
baseline recorded by the plan with a clean working tree, or at the correctly
journaled HEAD when resuming. The tool binds execution to the repository,
project identity, branch, current ICC revision, and phase history. It does
not require a special branch name: a dedicated fork may intentionally use
local `main`.

```bash
cd "$KERNEL_REPO"
git branch --show-current
git status --short

"$KERNEL_BACKPORT" check --results-dir "$PLAN" --no-replay
"$KERNEL_BACKPORT" status --results-dir "$PLAN"

# Start with one attempted commit per invocation.
"$KERNEL_BACKPORT" apply --results-dir "$PLAN" --max-attempts 1
"$KERNEL_BACKPORT" status --results-dir "$PLAN"
"$KERNEL_BACKPORT" resume --results-dir "$PLAN" --max-attempts 1
```

`apply` initializes a journaled execution phase and `resume` continues an
interrupted or pending phase. Follow the conflict dossier when a pick cannot
be applied directly. Necessary source adaptation happens here. Manually
resolved patches must be recorded as downstream commits with the expected
upstream provenance so the execution engine can reconcile the journal on
`resume`. Avoid resetting or rewriting completed journaled history.

If an unprocessed selection changes during execution, recheck ICC; the tool
can start a new phase for the remaining TAKEs without falsifying the older
phase's history. Previously applied commits cannot be retroactively marked
SKIP or REVIEW. Do not attach a newly added prerequisite to a commit already
applied in a completed phase.

Execution is guarded even when the branch name is `main`: a mismatched
baseline, incorrect project identity, stale ICC, dirty worktree, or divergent
journal state is not silently accepted.

## 6. Finish construction and hand off to Git

Once all selected TAKEs have been integrated or verified already present, and
all execution phases are complete, finalize the tool's role:

```bash
"$KERNEL_BACKPORT" status --results-dir "$PLAN"
"$KERNEL_BACKPORT" finish --results-dir "$PLAN"
```

`finish` requires the same clean kernel repository, branch, and HEAD recorded
by the execution journal. It checks the current approved selection, ICC
readiness, all phase snapshots and journal entries, and upstream/downstream
provenance. It refuses to finish while a selected TAKE is pending or a live
phase is blocked. It does **not** require a conflict-free replay, a completed
CKI pipeline, or a successful boot. The command never changes Git commits.

A successful handoff writes:

- `.kernel-backport/finish.json`: the completed selection, downstream SHAs at
  handoff, source and worksheet hashes, ICC warnings, phase and journal hashes,
  and original engineer/automatic/advisor decision provenance.
- `.kernel-backport/finish-review.md`: a readable list of retained commits and
  explicit engineering decisions, suitable as a reference during MR review.
- A `finished` marker in `.kernel-backport/state.json`. Repeating `finish` is
  harmless and does not rewrite the archive.

After finishing, `apply`, `resume`, `check`, `add`, `decide`, and `condition` are
closed for **that plan**. Read-only `status` and nonbillable `review` remain
available. The tool no longer insists that Git HEAD match the old journal;
ordinary Git amend, rebase, reorder, squash, and fixup operations are yours.
The recorded downstream SHAs are historical construction evidence, not a
requirement to keep a particular patch-series history forever. The archived
worksheet and plan remain usable to explain TAKE/SKIP decisions during MR
review. To do *new* automated backport work afterward, start a new execution
plan rather than attempting to reopen a completed journal.

**Archive the entire plan directory including `.kernel-backport/`, not just
`backport_worksheet.txt`.** Preserve Git commits separately in your fork or a
patch series. Plan and journal files describe what was done; they are not a
replacement for the resulting kernel patch series.

## 7. Validate and prepare the backport for review

After integrating the selected patches, perform the appropriate engineering
validation, including the relevant subset of:

- Kernel build and configuration checks (for example, using `kernel-build`).
- Boot, functional, regression, and supported-platform tests.
- RHEL kABI, exported symbol, module, and downstream compatibility review.
- CKI or other required CI and product-specific qualification.
- Final review of applied commit provenance, retained downstream-equivalent
  functionality, and any `Omitted-fix:` explanations.

A completed or finished journal means the chosen commits were integrated,
**not** that the resulting kernel is safe or correct. Keep the planning snapshot, engineer
rationales, ICC report, and execution journals alongside the downstream merge
request's validation evidence.

## Results, reports, and audit trail

A typical plan directory contains:

```text
backport_plan_NN/
  backport_plan.json                 # frozen planner source
  backport_worksheet.txt            # engineer decisions and generated summary
  review-p0.txt ... review-p3.txt   # prioritized engineering queues
  review-queues.json                # structured queue details
  review-features.txt               # cached feature assessment summary
  review-features.json              # structured feature assessments
  conditioning-decisions.txt        # proposed/applied guarded decisions
  conditioning-decisions.json       # structured conditioning report
  .kernel-backport/
    additions.json                  # engineer-added commits, when used
    icc.json                        # current ICC attestation, when run
    state.json                      # execution state and phase bindings
    phase_001/
      execution_plan.json           # immutable phase snapshot
      journal.json                  # actual execution journal
    finish.json                     # final provenance/decision snapshot
    finish-review.md                # readable MR review record
```

Some reports and state files are produced only after the corresponding
command runs. The original advisor responses, immutable plan, explicit
engineer decisions, approved additions, and historical execution phases
remain separately identifiable. `status` summarizes progress, but the
underlying reports are the audit record.

## Boundaries and limitations

- This tool cannot independently establish full semantic dependencies,
  hardware support relevance, downstream source equivalence, or absence of
  regressions. Model judgments are evidence, not proof.
- Selected upstream `Fixes:` references and related patch-series history
  require interpretation; they do not automatically justify importing the
  commits they mention.
- Exploratory replay uses locally available Git objects and does not fetch
  missing commits silently. A successful replay is not build validation.
- The source plan, downstream baseline, and journaled execution history are
  intentionally bound and auditable. Do not assume that rebasing or switching
  projects can be merged into an active execution journal automatically.
- Automated exclusions are designed to be conservative. Decisions blocked
  by correctness concerns, unsupported evidence, invalid model output, or
  unresolved dependencies must be handled by an engineer.

## Testing and further reference

The distribution includes local-Git and mocked-advisor tests. They do not
make live OpenAI requests or perform full kernel boot validation:

```bash
python3 -m unittest discover -s engines/planner -p 'test*.py' -q
python3 -m unittest discover -s engines/backporter -p 'test*.py' -q
python3 -m unittest discover -s . -p 'test*.py' -q
```

Use `kernel-backport <command> --help` for the current flags. For historical
changes, consult [RELEASE_NOTES.md](RELEASE_NOTES.md). The bundled engines'
older READMEs remain under `engines/` as implementation and compatibility
references, not as separate installation instructions.
