# Code quality analysis with Topos

AgentPanelX uses [Topos](https://github.com/krv-ai/topos) as an additional structural code-quality signal for its external Agents. It complements tests, type checks, linters, and source review. Topos does not prove functional correctness, and a failed structural rule is not automatically a confirmed defect.

## Install and register Topos

Install the Topos binary, then register its MCP server with the Codex installation used by AgentPanelX:

```bash
curl -fsSL https://docs.krv.ai/topos/install.sh | bash
topos install
codex mcp list
```

The Topos quick start recommends this two-step installation so an Agent can measure code, make a focused change, and verify the result with the same tool. `topos install` registers the `topos mcp` stdio server; installing the binary alone does not expose MCP tools to Codex.

AgentPanelX starts Codex through its SDK and binds the registered Topos server to the current Runtime worktree. The Distributor uses its Feature worktree and a Stage Executor uses the detached Candidate worktree. The MCP file root is restricted to that target so an Agent cannot accidentally analyze another project.

If `codex mcp list` does not show an enabled `topos` server, restart the Agent host after registration and check that the `topos` executable is on the service user's `PATH`. The installed Topos and GitNexus versions should be recorded when diagnosing a result.

## What Topos measures

Topos parses supported source files and evaluates four structural dimensions. It can also use a GitNexus dependency graph for cross-file coupling.

### SIMPLE

SIMPLE looks at local control-flow and syntax structure. Its signals include cyclomatic complexity, maximum function complexity, nesting, and a source-entropy approximation. A failed result points to code that may be difficult to read, test, or change. Typical follow-up work is splitting a complex function, reducing branching, or separating responsibilities.

### COMPOSABLE

COMPOSABLE looks at outward dependency and call burden using the module dependency graph. Fan-in, fan-out, coupling, and instability help identify code that coordinates too many external concerns or has an awkward boundary. The graph must be available for this dimension; without it, the result is partial rather than a pass.

### SECURE

SECURE looks for dangerous API calls and static data-flow paths from potentially untrusted input. These are review signals. A dangerous-call finding can be intentional or a false positive, so the Agent must inspect the source and record the disposition instead of calling every finding a vulnerability.

### NAVIGABLE

NAVIGABLE measures structural nesting and divergence in functions and scopes. It is a signal about how easily a reader can follow code structure, not a measure of product navigation or user experience.

The four boolean dimensions form Topos's 16-element lattice. The continuous `scores` are useful for ranking and comparing compatible measurements, while `achieved` is the dimension's gate result. They must not be treated as interchangeable. A low score can coexist with an achieved gate when an advisory metric is poor.

## Agent workflow

The shared external-Agent instructions make Topos available to every Agent without duplicating Topos-specific procedures in each role prompt.

For a quality-hardening task, the normal loop is:

1. Measure the existing code with `topos_evaluate_file` or `topos_evaluate_project`.
2. Inspect the weakest relevant function or file with `topos_inspect_code` and read the source yourself.
3. Make one focused structural change that preserves the requested behavior.
4. Verify the worktree change with the corresponding Topos assessment tool using the same scope and baseline.
5. Run the relevant project tests, type checks, or linters and record what actually ran.

Task Distributor remains responsible for deciding whether a quality-hardening Stage is justified. Stage Executor performs the change and verification when that Stage is created. Topos supplies evidence; it does not independently change Specs, create Stages, accept Candidates, or replace Runtime decisions.

## Scope and limitations

- Topos supports Python, Rust, JavaScript, TypeScript, C++, and Go source files. Unsupported files are skipped and should be reported when a scan is described as partial.
- Project evaluation is paginated. A complete project conclusion requires collecting every page or explicitly reporting the coverage limit.
- COMPOSABLE depends on GitNexus. Index generation can create `.gitnexus` data in the analyzed worktree; keep that generated data outside the Candidate's intended change and do not commit it as product code.
- Static security findings require human or Agent review. The repository has already observed a `re.compile()` false positive, so raw findings must be interpreted in context.
- Topos is not a replacement for unit/integration tests, type checking, linting, runtime security review, or business architecture decisions.
- A measurement is tied to its target worktree, scan scope, tool version, and source state. Do not compare results from different scopes or versions as if they were a single before/after change.

For the complete tool contract and current command behavior, use the [Topos documentation](https://docs.krv.ai/topos/) and the [Topos source repository](https://github.com/krv-ai/topos).
