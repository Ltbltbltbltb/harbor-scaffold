# harbor-scaffold

Portable benchmark generator for the [Harbor](https://github.com/laude-institute/harbor) AI agent evaluation framework.

Harbor runs AI agents inside Docker containers and scores their outputs. harbor-scaffold generates all the boilerplate so you can focus on writing tasks and tuning prompts.

## What it does

1. **`init`** — scans your project, detects its LLM integration strategy, generates `manifest.yaml`
2. **`create-bench`** — generates `agent.py`, `pyproject.toml`, and `MISSION.md` from the manifest
3. **`add-task`** — adds a complete task skeleton (instruction, verifier, Dockerfile)
4. **`doctor`** — validates the manifest and all task files

## Installation

No installation required. Copy `scaffold.py` and the `base/`, `verifiers/`, `dockerfiles/`, and `manifest/` directories to your project or use it from a central location.

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), [Harbor](https://github.com/laude-institute/harbor)

## Quickstart

```bash
# 1. Initialize benchmark for your project
python scaffold.py init /path/to/your-project

# 2. Edit harbor-bench/manifest.yaml — fill in the TODOs
# (strategy is auto-detected; you mainly need to write system_prompt)

# 3. Generate agent.py and supporting files
python scaffold.py create-bench --bench-dir /path/to/your-project/harbor-bench

# 4. Add tasks
python scaffold.py add-task my-task --verifier json_schema --bench-dir harbor-bench

# 5. Fill in instruction.md and tests/test.sh for each task

# 6. Validate everything
python scaffold.py doctor --bench-dir harbor-bench

# 7. Run the benchmark
cd harbor-bench
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:YourAgent -o jobs --job-name run1
```

## Strategies

harbor-scaffold auto-detects the appropriate strategy by scanning your project:

### `direct`

The agent receives the task instruction and sends it directly to an LLM with a configurable system prompt. Best for projects without an LLM integration, or when you want to evaluate a generic AI assistant.

**Customize**: only the `system_prompt` in `manifest.yaml`.

### `monkeypatch`

The agent imports your project's actual code, monkey-patches the LLM function to capture the prompt your code would build, then sends that exact prompt to the LLM. Changes to your project's prompts are reflected automatically — zero duplication.

**Best for**: projects that already call an LLM internally.

**Requires**: implementing `_build_prompt_via_monkey_patch()` in the generated `agent.py`.

### `context_inject`

The agent reads specified files from your project (e.g., `CLAUDE.md`, config files) and injects their content into the prompt along with the task instruction.

**Best for**: projects that use a context file to guide an external AI tool.

**Customize**: `context_files` list and `inline_context` in `manifest.yaml`.

## LLM Backends

Set `agent.backend` in `manifest.yaml`:

| Backend | Description | Requirement |
|---|---|---|
| `cli` | Runs `claude -p` as a subprocess | Claude Code CLI + Claude Max subscription |
| `api` | Anthropic Python SDK | `pip install anthropic` + `ANTHROPIC_API_KEY` |
| `openai_compat` | OpenAI-compatible API | `pip install openai` + API key + `base_url` |

### Backend configuration examples

```yaml
# cli (default) — no API key needed if you have Claude Max
agent:
  backend: "cli"
  model: "claude-sonnet-4-6"

# Anthropic API
agent:
  backend: "api"
  model: "claude-sonnet-4-6"
  api_key_env: "ANTHROPIC_API_KEY"

# OpenAI or any compatible endpoint
agent:
  backend: "openai_compat"
  model: "gpt-4o"
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"

# Local model via Ollama
agent:
  backend: "openai_compat"
  model: "llama3.2"
  base_url: "http://localhost:11434/v1"
  api_key_env: "OLLAMA_API_KEY"  # set to any non-empty string
```

## Verifier types

| Type | When to use | Output format |
|---|---|---|
| `json_schema` | Output is structured JSON | `{"field": value, ...}` |
| `structured_text` | Output has labeled fields | `Label: value` per line |
| `code_execution` | Output is executable Python | code block |
| `numerical` | Output contains numeric results | `Result: 42` |
| `markdown_sections` | Output is a long markdown document | `## Section` headings |
| `keyword_pattern` | Output must contain/avoid patterns | free text |

All verifiers write a score between 0.0 and 1.0 to `/logs/verifier/reward.txt`.

## Repository structure

```
harbor-scaffold/
  scaffold.py              # CLI entry point
  manifest/
    project.yaml.template  # manifest template
  base/
    agent_direct.py.template        # direct strategy
    agent_context_inject.py.template # context_inject strategy
    agent_monkeypatch.py.template   # monkeypatch strategy
  verifiers/
    json_schema.sh.template
    structured_text.sh.template
    code_execution.sh.template
    numerical.sh.template
    markdown_sections.sh.template
    keyword_pattern.sh.template
  dockerfiles/
    minimal.Dockerfile     # ubuntu + bc + python3
    jq.Dockerfile          # + jq
    custom.Dockerfile      # template for extra packages
  docs/
    ADAPTATION_GUIDE.md    # detailed workflow guide
  example/                 # complete working example (text-analyzer)
```

## Example

See the `example/` directory for a complete working benchmark with two tasks:

- `word-count` — structured_text verifier
- `sentiment-label` — keyword_pattern verifier

To run it (requires Harbor installed):

```bash
cd example
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:TextAnalyzerAgent -o jobs --job-name test
```

## License

MIT — see [LICENSE](LICENSE).
