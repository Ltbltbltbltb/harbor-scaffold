# harbor-scaffold

Portable benchmark generator for the [Harbor](https://github.com/laude-institute/harbor)
AI agent evaluation framework.

`harbor-scaffold` generates the boilerplate for Harbor benchmarks so you can focus
on prompt tuning, task design, and verifier quality instead of rebuilding the
same structure for each project.

The updated version is not only a benchmark scaffold. It can also generate an
optional memory loop around the benchmark, so the same project can accumulate
knowledge, reuse that knowledge as context, and turn that accumulated knowledge
into new benchmark tasks.

## LLM backends

For `direct` and `context_inject`, the scaffold supports:

- `cli` - runs `claude -p`
- `api` - uses the Anthropic Python SDK
- `openai_compat` - uses an OpenAI-compatible endpoint

Set this in `manifest.yaml` with:

```yaml
agent:
  backend: "cli"
```

## What it does

1. `init` scans a target project, detects the likely integration strategy, and
   creates `harbor-bench/manifest.yaml`
2. `create-bench` generates `agent.py`, `pyproject.toml`, `MISSION.md`, and
   supporting files from the manifest
3. `add-task` creates complete task skeletons with verifier and Dockerfile
4. `doctor` validates the manifest, generated files, and task structure
5. Optional: generate a portable wiki loop for compiled memory and `wiki-recall`
   benchmark tasks

## Why this is interesting

Most public projects in this area focus on one layer only:

- benchmark and eval infrastructure
- persistent memory
- compiled wiki workflows

`harbor-scaffold` is designed to connect those layers inside one portable
project template.

The core loop is:

1. benchmark the agent on explicit tasks
2. compile useful project knowledge into a local wiki
3. query or inject that wiki back as context
4. convert wiki pages into paired `no-wiki` vs `with-wiki` benchmark tasks
5. use the benchmark results to improve prompts, tasks, and memory strategy

That makes the scaffold useful not only for static evaluation, but also for
iterative improvement. In practice, it becomes a loop on top of another loop:
the project produces knowledge, the wiki compiles that knowledge, the benchmark
tests whether that knowledge helps, and the next benchmark iteration improves
the project again.

## Quickstart

```bash
# 1. Initialize a benchmark for your project
python scaffold.py init /path/to/your-project

# 2. Edit harbor-bench/manifest.yaml and fill the TODOs

# 3. Generate the benchmark files
python scaffold.py create-bench --bench-dir /path/to/your-project/harbor-bench

# 4. Add tasks
python scaffold.py add-task my-task --verifier json_schema --bench-dir /path/to/your-project/harbor-bench

# 5. Validate
python scaffold.py doctor --bench-dir /path/to/your-project/harbor-bench

# 6. Run Harbor
cd /path/to/your-project/harbor-bench
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:YourAgent -o jobs --job-name run1
```

## Strategies

### `direct`

The benchmark agent sends the task instruction directly to `claude -p` with a
configurable system prompt.

Best for:
- projects without an internal LLM integration
- generic assistant-style evaluation

### `context_inject`

The benchmark agent reads selected project files such as `CLAUDE.md`, config
files, or conventions docs, injects that context into the prompt, and then sends
the task to `claude -p`.

Best for:
- projects guided by context files
- projects that do not expose a clean internal LLM call boundary

### `monkeypatch`

The benchmark agent imports the real project code and monkey-patches the LLM
call site to capture the exact prompt the project would have built.

Best for:
- projects that already call an LLM internally
- benchmarks that should track prompt drift automatically

Note: you still need to implement `_build_prompt_via_monkey_patch()` in the
generated `agent.py`.

## Optional memory loop

If `memory.enabled: true` in `manifest.yaml`, the scaffold also generates:

- `wiki.py`
- `wiki/SCHEMA.md`
- `wiki/index.md`
- `wiki/log.md`
- `wiki/pages/`
- `scripts/sync_wiki_recall.py`

This creates a portable markdown wiki loop inspired by the Karpathy-style LLM
wiki pattern:

- runtime knowledge can be compiled into a local wiki
- the wiki can be queried or injected back as context
- wiki pages can be converted into paired `no-wiki` vs `with-wiki` benchmark tasks
- the wiki uses the same configured backend family as the benchmark (`cli`, `api`, or `openai_compat`)

The goal is not just to store notes. The wiki loop exists to make project
knowledge measurable. Once pages can be turned into paired tasks, you can check
whether the compiled memory is actually improving the agent or just creating
more files.

If `memory.runtime_adapter.enabled: true`, the scaffold also generates:

- `scripts/export_runtime_events.py`
- `scripts/sync_runtime_to_wiki.py`

Those files define the adapter boundary for project-specific runtime ingestion.
The scaffold keeps that part explicit and optional so the core stays portable.

## Why this version

This scaffold is designed around portability and practical iteration:

- no required external dependencies for manifest parsing
- direct support for `direct`, `context_inject`, and `monkeypatch`
- reusable verifier templates
- optional memory loop without coupling to any single project
- explicit boundary between the portable core and the project-specific runtime adapter

## Repository structure

```text
harbor-scaffold/
  scaffold.py
  README.md
  manifest/
    project.yaml.template
  base/
    agent_direct.py.template
    agent_context_inject.py.template
    agent_monkeypatch.py.template
    wiki.py.template
    sync_wiki_recall.py.template
    export_runtime_events.py.template
    sync_runtime_to_wiki.py.template
    wiki_SCHEMA.md.template
    wiki_index.md.template
    wiki_log.md.template
  verifiers/
    json_schema.sh.template
    structured_text.sh.template
    code_execution.sh.template
    numerical.sh.template
    markdown_sections.sh.template
    keyword_pattern.sh.template
  dockerfiles/
    minimal.Dockerfile
    jq.Dockerfile
    custom.Dockerfile
  docs/
    ADAPTATION_GUIDE.md
  example/
    README.md
    manifest.yaml
    agent.py
    wiki.py
    scripts/
    tasks/
    wiki/
```

## Example

The `example/` directory is a complete runnable benchmark called `text-analyzer`.
It demonstrates both:

- a normal Harbor benchmark with simple tasks
- the optional wiki loop layout, including seeded wiki files and `sync_wiki_recall.py`

See `example/README.md` for the exact commands.

## Notes

- `monkeypatch` continues to use the real project prompt path and `claude -p`.
- `doctor` validates the generated wiki loop too when it is enabled.
- The YAML fallback parser handles nested dicts, lists, and multiline strings
  without requiring `PyYAML`.
