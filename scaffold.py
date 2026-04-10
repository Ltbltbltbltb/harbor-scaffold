#!/usr/bin/env python3
"""harbor-scaffold - gerador portatil de benchmarks Harbor.

Uso:
    scaffold.py init <project-dir>              Escaneia projeto, gera manifest.yaml
    scaffold.py create-bench                    Gera agent.py + estrutura a partir do manifest
    scaffold.py add-task <name> --verifier TYPE Adiciona task skeleton
    scaffold.py doctor                          Valida manifest + estrutura gerada
"""

import argparse
import ast
import os
import re
import shutil
import sys
from pathlib import Path

SCAFFOLD_DIR = Path(__file__).parent
VALID_STRATEGIES = ("direct", "monkeypatch", "context_inject")
VALID_BACKENDS = ("cli", "api", "openai_compat")
VALID_VERIFIERS = (
    "json_schema",
    "structured_text",
    "code_execution",
    "numerical",
    "markdown_sections",
    "keyword_pattern",
)
VALID_DOCKERFILES = ("minimal", "jq", "custom")


# ============================================================
# YAML simplificado (sem deps externas)
# ============================================================
def _strip_inline_comment(text: str) -> str:
    in_single = False
    in_double = False
    escaped = False

    for idx, char in enumerate(text):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if idx == 0 or text[idx - 1].isspace():
                return text[:idx].rstrip()
        escaped = False

    return text.rstrip()


def _parse_scalar(raw_value: str):
    value = _strip_inline_comment(raw_value).strip()
    if value == "":
        return ""

    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]

    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        parts = [part.strip() for part in inner.split(",")]
        return [_parse_scalar(part) for part in parts]
    return value


def _line_indent(raw_line: str) -> int:
    return len(raw_line) - len(raw_line.lstrip(" "))


def _skip_yaml_noise(lines: list[str], index: int) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            break
        index += 1
    return index


def _parse_yaml_multiline(lines: list[str], index: int, parent_indent: int) -> tuple[str, int]:
    content_indent = parent_indent + 2
    buffer: list[str] = []

    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            buffer.append("")
            index += 1
            continue

        indent = _line_indent(raw)
        if indent <= parent_indent:
            break

        if len(raw) >= content_indent:
            buffer.append(raw[content_indent:])
        else:
            buffer.append("")
        index += 1

    return "\n".join(buffer).rstrip("\n"), index


def _parse_yaml_dict(lines: list[str], index: int, indent: int) -> tuple[dict, int]:
    result: dict = {}

    while True:
        index = _skip_yaml_noise(lines, index)
        if index >= len(lines):
            break

        raw = lines[index]
        current_indent = _line_indent(raw)
        if current_indent < indent:
            break
        if current_indent != indent:
            break

        stripped = raw.strip()
        if stripped.startswith("- "):
            break

        match = re.match(r"^([\w.-]+):\s*(.*)$", stripped)
        if not match:
            index += 1
            continue

        key = match.group(1)
        value_text = _strip_inline_comment(match.group(2).strip())

        if value_text in ("|", ">"):
            value, index = _parse_yaml_multiline(lines, index + 1, current_indent)
        elif value_text == "":
            next_index = _skip_yaml_noise(lines, index + 1)
            if next_index < len(lines) and _line_indent(lines[next_index]) > current_indent:
                value, index = _parse_yaml_block(
                    lines, next_index, _line_indent(lines[next_index])
                )
            else:
                value = {}
                index += 1
        else:
            value = _parse_scalar(value_text)
            index += 1

        result[key] = value

    return result, index


def _parse_yaml_list(lines: list[str], index: int, indent: int) -> tuple[list, int]:
    result: list = []

    while True:
        index = _skip_yaml_noise(lines, index)
        if index >= len(lines):
            break

        raw = lines[index]
        current_indent = _line_indent(raw)
        if current_indent < indent:
            break
        if current_indent != indent:
            break

        stripped = raw.strip()
        if not stripped.startswith("- "):
            break

        item_text = _strip_inline_comment(stripped[2:].strip())

        if item_text == "":
            next_index = _skip_yaml_noise(lines, index + 1)
            if next_index < len(lines) and _line_indent(lines[next_index]) > current_indent:
                item, index = _parse_yaml_block(
                    lines, next_index, _line_indent(lines[next_index])
                )
            else:
                item = ""
                index += 1
            result.append(item)
            continue

        key_match = re.match(r"^([\w.-]+):\s*(.*)$", item_text)
        if key_match:
            item: dict = {}
            key = key_match.group(1)
            value_text = _strip_inline_comment(key_match.group(2).strip())

            if value_text in ("|", ">"):
                item[key], index = _parse_yaml_multiline(lines, index + 1, current_indent)
            elif value_text == "":
                next_index = _skip_yaml_noise(lines, index + 1)
                if next_index < len(lines) and _line_indent(lines[next_index]) > current_indent:
                    item[key], index = _parse_yaml_block(
                        lines, next_index, _line_indent(lines[next_index])
                    )
                else:
                    item[key] = {}
                    index += 1
            else:
                item[key] = _parse_scalar(value_text)
                index += 1

            next_index = _skip_yaml_noise(lines, index)
            if next_index < len(lines):
                next_indent = _line_indent(lines[next_index])
                if next_indent > current_indent and not lines[next_index].strip().startswith("- "):
                    extra, index = _parse_yaml_dict(lines, next_index, next_indent)
                    item.update(extra)

            result.append(item)
            continue

        result.append(_parse_scalar(item_text))
        index += 1

    return result, index


def _parse_yaml_block(lines: list[str], index: int, indent: int):
    index = _skip_yaml_noise(lines, index)
    if index >= len(lines):
        return {}, index

    stripped = lines[index].strip()
    if stripped.startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_dict(lines, index, indent)


def _parse_yaml(path: Path) -> dict:
    """Parser YAML minimalista para manifests simples."""
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    lines = path.read_text(encoding="utf-8").splitlines()
    parsed, _ = _parse_yaml_block(lines, 0, 0)
    return parsed if isinstance(parsed, dict) else {}


def _dump_yaml_simple(data: dict, indent: int = 0) -> str:
    """Serializa dict para YAML simples."""
    lines = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dump_yaml_simple(value, indent + 1))
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{prefix}{key}: |")
            for vline in value.split("\n"):
                lines.append(f"{prefix}  {vline}")
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for item_key, item_value in item.items():
                        if first:
                            lines.append(
                                f"{prefix}  - {item_key}: {_quote_if_needed(item_value)}"
                            )
                            first = False
                        else:
                            lines.append(
                                f"{prefix}    {item_key}: {_quote_if_needed(item_value)}"
                            )
                else:
                    lines.append(f"{prefix}  - {_quote_if_needed(item)}")
        else:
            lines.append(f"{prefix}{key}: {_quote_if_needed(value)}")
    return "\n".join(lines)


def _quote_if_needed(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and (" " in value or ":" in value or value == ""):
        return f'"{value}"'
    return str(value)


# ============================================================
# Deteccao de estrategia
# ============================================================
def _detect_strategy(project_dir: Path) -> str:
    """Escaneia o projeto por padroes de chamada LLM."""
    llm_patterns = [
        r"_ask_claude",
        r"call_llm",
        r"ask_llm",
        r"openai\.chat",
        r"anthropic\.",
        r"client\.messages\.create",
        r"client\.chat\.completions",
    ]
    pattern = "|".join(llm_patterns)

    py_files = list(project_dir.rglob("*.py"))
    py_files = [
        path
        for path in py_files
        if not any(
            part in path.parts
            for part in ("venv", ".venv", "__pycache__", ".git", "node_modules")
        )
    ]

    hits = []
    for py_file in py_files[:200]:
        try:
            content = py_file.read_text(errors="ignore")
            for match in re.finditer(pattern, content):
                hits.append((py_file.relative_to(project_dir), match.group()))
        except OSError:
            continue

    if hits:
        return "monkeypatch"
    if (project_dir / "CLAUDE.md").exists():
        return "context_inject"
    return "direct"


def _render_template(template_path: Path, replacements: dict[str, str]) -> str:
    content = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"%%{key}%%", str(value))
    return content


def _memory_defaults(agent: dict, memory_cfg: dict | None) -> dict:
    cfg = dict(memory_cfg or {})
    defaults = {
        "enabled": False,
        "wiki_dir": "wiki",
        "language": "pt-BR",
        "selector_model": agent.get("model", "claude-sonnet-4-6"),
        "writer_model": agent.get("model", "claude-sonnet-4-6"),
        "max_context_chars": 4000,
        "sync_wiki_recall": True,
        "min_page_chars": 500,
        "max_pairs_per_page": 2,
    }
    for key, value in defaults.items():
        cfg.setdefault(key, value)

    runtime_cfg = dict(cfg.get("runtime_adapter") or {})
    runtime_cfg.setdefault("enabled", False)
    runtime_cfg.setdefault("export_script", "scripts/export_runtime_events.py")
    cfg["runtime_adapter"] = runtime_cfg
    return cfg


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _create_memory_loop_assets(
    bench_dir: Path,
    project: dict,
    agent: dict,
    memory: dict,
) -> None:
    wiki_dir_name = memory["wiki_dir"]
    wiki_dir = bench_dir / wiki_dir_name
    pages_dir = wiki_dir / "pages"
    scripts_dir = bench_dir / "scripts"
    data_dir = bench_dir / "data"

    wiki_dir.mkdir(exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    _write_if_missing(pages_dir / ".gitkeep", "")
    _write_if_missing(data_dir / ".gitkeep", "")

    replacements = {
        "PROJECT_SLUG": project.get("name", "myproject"),
        "CLASS_NAME": agent.get("class_name", "ProjectBenchAgent"),
        "WIKI_DIR": wiki_dir_name,
        "WIKI_LANGUAGE": memory["language"],
        "WIKI_SELECTOR_MODEL": memory["selector_model"],
        "WIKI_WRITER_MODEL": memory["writer_model"],
        "WIKI_MAX_CONTEXT_CHARS": str(memory["max_context_chars"]),
        "WIKI_MIN_PAGE_CHARS": str(memory["min_page_chars"]),
        "WIKI_MAX_PAIRS_PER_PAGE": str(memory["max_pairs_per_page"]),
        "RUNTIME_EXPORT_SCRIPT": memory["runtime_adapter"]["export_script"],
        "BACKEND": agent.get("backend", "cli"),
        "API_KEY_ENV": agent.get("api_key_env", "ANTHROPIC_API_KEY"),
        "BASE_URL": agent.get("base_url", ""),
    }

    (bench_dir / "wiki.py").write_text(
        _render_template(SCAFFOLD_DIR / "base" / "wiki.py.template", replacements),
        encoding="utf-8",
    )

    _write_if_missing(
        wiki_dir / "SCHEMA.md",
        _render_template(SCAFFOLD_DIR / "base" / "wiki_SCHEMA.md.template", replacements),
    )
    _write_if_missing(
        wiki_dir / "index.md",
        _render_template(SCAFFOLD_DIR / "base" / "wiki_index.md.template", replacements),
    )
    _write_if_missing(
        wiki_dir / "log.md",
        _render_template(SCAFFOLD_DIR / "base" / "wiki_log.md.template", replacements),
    )

    if memory["sync_wiki_recall"]:
        sync_wiki_path = scripts_dir / "sync_wiki_recall.py"
        sync_wiki_path.write_text(
            _render_template(
                SCAFFOLD_DIR / "base" / "sync_wiki_recall.py.template",
                replacements,
            ),
            encoding="utf-8",
        )
        os.chmod(sync_wiki_path, 0o755)

    if memory["runtime_adapter"]["enabled"]:
        export_path = bench_dir / memory["runtime_adapter"]["export_script"]
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(
            _render_template(
                SCAFFOLD_DIR / "base" / "export_runtime_events.py.template",
                replacements,
            ),
            encoding="utf-8",
        )
        os.chmod(export_path, 0o755)

        sync_runtime_path = scripts_dir / "sync_runtime_to_wiki.py"
        sync_runtime_path.write_text(
            _render_template(
                SCAFFOLD_DIR / "base" / "sync_runtime_to_wiki.py.template",
                replacements,
            ),
            encoding="utf-8",
        )
        os.chmod(sync_runtime_path, 0o755)


# ============================================================
# Subcomando: init
# ============================================================
def cmd_init(args):
    project_dir = Path(args.project_dir).resolve()
    if not project_dir.is_dir():
        print(f"ERRO: {project_dir} nao eh um diretorio valido")
        sys.exit(1)

    bench_dir = project_dir / "harbor-bench"
    bench_dir.mkdir(exist_ok=True)
    (bench_dir / "tasks").mkdir(exist_ok=True)
    (bench_dir / "jobs").mkdir(exist_ok=True)

    manifest_path = bench_dir / "manifest.yaml"

    strategy = _detect_strategy(project_dir)
    slug = project_dir.name.replace(" ", "-").lower()

    template_path = SCAFFOLD_DIR / "manifest" / "project.yaml.template"
    template = template_path.read_text(encoding="utf-8")

    manifest = template.replace("{{PROJECT_SLUG}}", slug)
    manifest = manifest.replace("{{PROJECT_PATH}}", str(project_dir))
    manifest = manifest.replace("{{STRATEGY}}", strategy)
    class_name = slug.replace("-", " ").title().replace(" ", "") + "Agent"
    manifest = manifest.replace("{{CLASS_NAME}}", class_name)
    manifest = manifest.replace("{{AGENT_NAME}}", f"{slug}-bench")

    manifest_path.write_text(manifest, encoding="utf-8")

    print(f"Benchmark inicializado em {bench_dir}/")
    print(f"  Estrategia detectada: {strategy}")
    if strategy == "monkeypatch":
        print(
            "  -> Encontradas chamadas LLM no projeto. Preencha a secao 'monkeypatch' no manifest."
        )
    elif strategy == "context_inject":
        print("  -> CLAUDE.md encontrado. Contexto sera injetado no prompt.")
    else:
        print("  -> Nenhuma chamada LLM detectada. Usando estrategia direta.")
    print("  -> Opcional: habilite memory.enabled para gerar o wiki loop portatil.")
    print(f"\nProximo passo: edite {manifest_path}")
    print("  Preencha os campos marcados com TODO")
    print(f"  Depois: python {__file__} create-bench --bench-dir {bench_dir}")


# ============================================================
# Subcomando: create-bench
# ============================================================
def cmd_create_bench(args):
    bench_dir = Path(args.bench_dir).resolve()
    manifest_path = bench_dir / "manifest.yaml"

    if not manifest_path.exists():
        print(f"ERRO: manifest.yaml nao encontrado em {bench_dir}")
        print("  Rode 'scaffold.py init <project-dir>' primeiro")
        sys.exit(1)

    cfg = _parse_yaml(manifest_path)
    project = cfg.get("project", {})
    agent = cfg.get("agent", {})
    benchmark = cfg.get("benchmark", {})
    memory = _memory_defaults(agent, cfg.get("memory"))

    strategy = agent.get("strategy", "direct")
    if strategy not in VALID_STRATEGIES:
        print(f"ERRO: estrategia '{strategy}' invalida. Use: {VALID_STRATEGIES}")
        sys.exit(1)

    template_name = f"agent_{strategy}.py.template"
    template_path = SCAFFOLD_DIR / "base" / template_name
    if not template_path.exists():
        print(f"ERRO: template {template_name} nao encontrado")
        sys.exit(1)

    slug = project.get("name", "myproject")
    class_name = agent.get(
        "class_name", slug.replace("-", " ").title().replace(" ", "") + "Agent"
    )
    agent_name = agent.get("agent_name", f"{slug}-bench")

    replacements = {
        "CLASS_NAME": class_name,
        "AGENT_NAME": agent_name,
        "AGENT_VERSION": agent.get("version", "0.1.0"),
        "CLAUDE_MODEL": agent.get("model", "claude-sonnet-4-6"),
        "CLAUDE_TIMEOUT": str(agent.get("timeout", 300)),
        "CLAUDE_MAX_RETRIES": str(agent.get("max_retries", 3)),
        "PROJECT_DESCRIPTION": project.get("description", "Agent benchmark"),
        "PROJECT_ROOT_PATH": project.get("path", ".."),
        "BACKEND": agent.get("backend", "cli"),
        "API_KEY_ENV": agent.get("api_key_env", "ANTHROPIC_API_KEY"),
        "BASE_URL": agent.get("base_url", ""),
    }

    system_prompt = agent.get("system_prompt", "")
    replacements["SYSTEM_PROMPT"] = system_prompt

    if strategy == "monkeypatch":
        monkeypatch = agent.get("monkeypatch", {})
        replacements["MODULES_PATH"] = monkeypatch.get("project_modules_path", "src")
        replacements["LLM_FUNCTION"] = monkeypatch.get("llm_function", "_ask_claude")

        roles = monkeypatch.get("roles", [])
        role_config_lines = []
        if isinstance(roles, list):
            for role in roles:
                if isinstance(role, dict):
                    role_name = role.get("name", "default")
                    role_model = role.get("model", "sonnet")
                    role_timeout = role.get("timeout", 120)
                    role_config_lines.append(
                        f'    "{role_name}": {{"model": "{role_model}", "timeout": {role_timeout}}},'
                    )
        replacements["ROLE_CONFIG_ENTRIES"] = "\n".join(role_config_lines)

    elif strategy == "context_inject":
        context_inject = agent.get("context_inject", {})
        context_files = context_inject.get("context_files", [])
        if isinstance(context_files, list):
            replacements["CONTEXT_FILES_LIST"] = "\n".join(
                f'    "{path}",' for path in context_files if isinstance(path, str)
            )
        else:
            replacements["CONTEXT_FILES_LIST"] = ""
        replacements["INLINE_CONTEXT"] = context_inject.get("inline_context", "")

    agent_result = _render_template(template_path, replacements)
    (bench_dir / "agent.py").write_text(agent_result, encoding="utf-8")

    dependencies = ['"harbor"']
    backend = agent.get("backend", "cli")
    if backend == "api":
        dependencies.append('"anthropic"')
    elif backend == "openai_compat":
        dependencies.append('"openai"')

    deps_literal = ", ".join(dependencies)

    pyproject = f"""[project]
name = "{slug}-bench"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [{deps_literal}]

[tool.uv.sources]
harbor = {{ git = "https://github.com/laude-institute/harbor" }}
"""
    (bench_dir / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    if not (bench_dir / "results.tsv").exists():
        (bench_dir / "results.tsv").write_text(
            "timestamp\tcommit\tpassed\ttotal\tavg_score\tdescription\n",
            encoding="utf-8",
        )

    memory_section = ""
    if memory["enabled"]:
        _create_memory_loop_assets(bench_dir, project, agent, memory)
        memory_section = f"""
## Wiki loop opcional

Arquivos gerados:
- `wiki.py`
- `{memory["wiki_dir"]}/`
"""
        if memory["sync_wiki_recall"]:
            memory_section += """- `scripts/sync_wiki_recall.py`
"""
        memory_section += f"""

Comandos uteis:

```bash
cd {bench_dir}
python wiki.py lint
"""
        if memory["sync_wiki_recall"]:
            memory_section += "python scripts/sync_wiki_recall.py --create\n"
        memory_section += "```\n"
        if memory["runtime_adapter"]["enabled"]:
            memory_section += """
Se voce conectar um adapter de runtime:

```bash
python scripts/sync_runtime_to_wiki.py --apply
```
"""

    mission = f"""# {slug} Benchmark - Missao

Projeto: {project.get("path", "?")}
Estrategia: {strategy}

## Objetivo

Maximizar o score no benchmark Harbor. Cada episodio:

1. Ler resultados do ultimo run
2. Diagnosticar tasks que falharam
3. Melhorar o harness (agent.py acima do boundary)
4. Rodar benchmark
5. Registrar em results.tsv
{memory_section}
## Como rodar

```bash
cd {bench_dir}
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:{class_name} -o jobs --job-name run1
```

## Regras

- NAO editar a secao [HARBOR ADAPTER - FIXO] do agent.py
- Foco em melhorias genericas, nao hacks por task
- Commitar apos cada melhoria confirmada
"""
    (bench_dir / "MISSION.md").write_text(mission, encoding="utf-8")

    gitignore = "jobs/\n__pycache__/\n*.pyc\n.venv/\ndata/\n"
    gitignore_path = bench_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(gitignore, encoding="utf-8")

    print(f"Benchmark criado em {bench_dir}/")
    print(f"  agent.py ({strategy})")
    print("  pyproject.toml")
    print("  MISSION.md")
    print("  results.tsv")
    if memory["enabled"]:
        print("  wiki.py + wiki/ (wiki loop opcional)")
        if memory["sync_wiki_recall"]:
            print("  scripts/sync_wiki_recall.py")
        if memory["runtime_adapter"]["enabled"]:
            print(f"  {memory['runtime_adapter']['export_script']} (stub de adapter)")
            print("  scripts/sync_runtime_to_wiki.py")
    print("\nProximo passo: adicione tasks com:")
    print(
        f"  python {__file__} add-task <nome> --verifier <tipo> --bench-dir {bench_dir}"
    )


# ============================================================
# Subcomando: add-task
# ============================================================
def cmd_add_task(args):
    bench_dir = Path(args.bench_dir).resolve()
    manifest_path = bench_dir / "manifest.yaml"

    if not manifest_path.exists():
        print(f"ERRO: manifest.yaml nao encontrado em {bench_dir}")
        sys.exit(1)

    cfg = _parse_yaml(manifest_path)
    project = cfg.get("project", {})
    benchmark = cfg.get("benchmark", {})

    task_name = args.name
    verifier = args.verifier
    if verifier not in VALID_VERIFIERS:
        print(f"ERRO: verifier '{verifier}' invalido. Use: {VALID_VERIFIERS}")
        sys.exit(1)

    task_dir = bench_dir / "tasks" / task_name
    if task_dir.exists():
        print(f"ERRO: task '{task_name}' ja existe em {task_dir}")
        sys.exit(1)

    task_dir.mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "environment").mkdir()

    slug = project.get("name", "myproject")
    difficulty = args.difficulty or "medium"

    task_toml = f"""[task]
name = "{slug}/{task_name}"
description = "TODO: descreva esta task"
timeout = 360
difficulty = "{difficulty}"
"""
    (task_dir / "task.toml").write_text(task_toml, encoding="utf-8")

    instruction_templates = {
        "json_schema": "TODO: Escreva a instrucao que pede ao agente gerar um JSON com estrutura especifica.\n\nDica: especifique os campos obrigatorios, tipos, e formato esperado.",
        "structured_text": "TODO: Escreva a instrucao que pede ao agente retornar texto com labels especificos.\n\nDica: use formato 'Label: valor' para facilitar extracao pelo verifier.",
        "code_execution": "TODO: Escreva a instrucao que pede ao agente gerar codigo Python.\n\nDica: especifique o nome da funcao e a assinatura esperada.",
        "numerical": "TODO: Escreva a instrucao com um problema numerico/matematico.\n\nDica: peca formato especifico como 'Resultado: <valor>'.",
        "markdown_sections": "TODO: Escreva a instrucao que pede um texto estruturado em markdown.\n\nDica: especifique as secoes obrigatorias (## Titulo) e requisitos de conteudo.",
        "keyword_pattern": "TODO: Escreva a instrucao que pede ao agente gerar output com padroes especificos.\n\nDica: liste os padroes/keywords que devem estar presentes no output.",
    }
    (task_dir / "instruction.md").write_text(
        instruction_templates[verifier], encoding="utf-8"
    )

    verifier_template = SCAFFOLD_DIR / "verifiers" / f"{verifier}.sh.template"
    if verifier_template.exists():
        shutil.copy2(verifier_template, task_dir / "tests" / "test.sh")
        os.chmod(task_dir / "tests" / "test.sh", 0o755)
    else:
        print(
            f"AVISO: template de verifier '{verifier}' nao encontrado, criando basico"
        )
        _write_basic_verifier(task_dir / "tests" / "test.sh")

    dockerfile_preset = benchmark.get("dockerfile_preset", "minimal")
    dockerfile_src = SCAFFOLD_DIR / "dockerfiles" / f"{dockerfile_preset}.Dockerfile"
    if dockerfile_src.exists():
        shutil.copy2(dockerfile_src, task_dir / "environment" / "Dockerfile")
    else:
        (task_dir / "environment" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y bc python3 && rm -rf /var/lib/apt/lists/*\n",
            encoding="utf-8",
        )

    print(f"Task criada: {task_dir}/")
    print(f"  Verifier: {verifier}")
    print(f"  Dockerfile: {dockerfile_preset}")
    print("\nPreencha:")
    print(f"  1. {task_dir / 'instruction.md'} - a instrucao da task")
    print(f"  2. {task_dir / 'tests' / 'test.sh'} - valores esperados nos TODOs")
    print(f"  3. {task_dir / 'task.toml'} - description")


def _write_basic_verifier(path: Path):
    content = """#!/bin/bash
OUTPUT_FILE="/logs/agent/output.txt"
REWARD_FILE="/logs/verifier/reward.txt"
mkdir -p /logs/verifier

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "0" > "$REWARD_FILE"
    exit 0
fi

SCORE=0
TOTAL=1  # TODO: ajuste o total de checks

# TODO: adicione seus checks aqui
# if grep -q "esperado" "$OUTPUT_FILE"; then
#     SCORE=$((SCORE + 1))
# fi

echo "scale=2; $SCORE / $TOTAL" | bc > "$REWARD_FILE"
"""
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _validate_python_file(path: Path, ok: list[str], errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"{path.name} NAO encontrado")
        return

    ok.append(f"{path.name} encontrado")
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        ok.append(f"{path.name}: sintaxe Python valida")
    except SyntaxError as exc:
        errors.append(f"{path.name}: erro de sintaxe linha {exc.lineno}: {exc.msg}")


# ============================================================
# Subcomando: doctor
# ============================================================
def cmd_doctor(args):
    bench_dir = Path(args.bench_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    manifest_path = bench_dir / "manifest.yaml"
    if manifest_path.exists():
        ok.append("manifest.yaml encontrado")
        cfg = _parse_yaml(manifest_path)
        project = cfg.get("project", {})
        agent = cfg.get("agent", {})
        memory = _memory_defaults(agent, cfg.get("memory"))

        if not project.get("name"):
            errors.append("manifest: project.name vazio")
        if not project.get("path"):
            errors.append("manifest: project.path vazio")
        if agent.get("strategy") not in VALID_STRATEGIES:
            errors.append(f"manifest: agent.strategy invalida: {agent.get('strategy')}")
        else:
            ok.append(f"estrategia: {agent.get('strategy')}")
        backend = agent.get("backend", "cli")
        if backend not in VALID_BACKENDS:
            errors.append(f"manifest: agent.backend invalido: {backend}")
        else:
            ok.append(f"backend: {backend}")
    else:
        errors.append("manifest.yaml NAO encontrado")
        cfg = {}
        agent = {}
        memory = _memory_defaults(agent, {})

    agent_path = bench_dir / "agent.py"
    _validate_python_file(agent_path, ok, errors)
    if agent_path.exists():
        content = agent_path.read_text(encoding="utf-8")
        if "HARBOR ADAPTER" in content:
            ok.append("agent.py: boundary HARBOR ADAPTER presente")
        else:
            warnings.append("agent.py: boundary HARBOR ADAPTER nao encontrado")
        if "%%" in content:
            warnings.append("agent.py: placeholders nao resolvidos detectados")

    if (bench_dir / "pyproject.toml").exists():
        ok.append("pyproject.toml encontrado")
    else:
        warnings.append("pyproject.toml nao encontrado")

    tasks_dir = bench_dir / "tasks"
    if tasks_dir.exists():
        task_dirs = [path for path in tasks_dir.iterdir() if path.is_dir()]
        if task_dirs:
            ok.append(f"{len(task_dirs)} task(s) encontrada(s)")
            for task_dir in task_dirs:
                task_name = task_dir.name
                required = [
                    task_dir / "task.toml",
                    task_dir / "instruction.md",
                    task_dir / "tests" / "test.sh",
                    task_dir / "environment" / "Dockerfile",
                ]
                for required_path in required:
                    if not required_path.exists():
                        errors.append(f"task {task_name}: faltando {required_path.name}")

                test_sh = task_dir / "tests" / "test.sh"
                if test_sh.exists():
                    content = test_sh.read_text(encoding="utf-8")
                    if "REWARD_FILE" not in content:
                        errors.append(f"task {task_name}: test.sh nao escreve REWARD_FILE")
                    if not os.access(test_sh, os.X_OK):
                        warnings.append(f"task {task_name}: test.sh nao eh executavel")

                instruction = task_dir / "instruction.md"
                if instruction.exists() and instruction.read_text(
                    encoding="utf-8"
                ).strip().startswith("TODO"):
                    warnings.append(f"task {task_name}: instruction.md ainda eh TODO")
        else:
            warnings.append("Nenhuma task criada ainda")
    else:
        warnings.append("Diretorio tasks/ nao existe")

    if memory["enabled"]:
        wiki_py = bench_dir / "wiki.py"
        _validate_python_file(wiki_py, ok, errors)

        wiki_dir = bench_dir / memory["wiki_dir"]
        if wiki_dir.exists():
            ok.append(f"{memory['wiki_dir']}/ encontrado")
        else:
            errors.append(f"{memory['wiki_dir']}/ NAO encontrado")

        for relative in ("SCHEMA.md", "index.md", "log.md"):
            path = wiki_dir / relative
            if path.exists():
                ok.append(f"{memory['wiki_dir']}/{relative} encontrado")
            else:
                errors.append(f"{memory['wiki_dir']}/{relative} NAO encontrado")

        pages_dir = wiki_dir / "pages"
        if pages_dir.exists():
            ok.append(f"{memory['wiki_dir']}/pages encontrado")
        else:
            errors.append(f"{memory['wiki_dir']}/pages NAO encontrado")

        if memory["sync_wiki_recall"]:
            sync_wiki = bench_dir / "scripts" / "sync_wiki_recall.py"
            _validate_python_file(sync_wiki, ok, errors)

        if memory["runtime_adapter"]["enabled"]:
            export_script = bench_dir / memory["runtime_adapter"]["export_script"]
            _validate_python_file(export_script, ok, errors)
            sync_runtime = bench_dir / "scripts" / "sync_runtime_to_wiki.py"
            _validate_python_file(sync_runtime, ok, errors)

            if export_script.exists() and "TODO" in export_script.read_text(
                encoding="utf-8"
            ):
                warnings.append(
                    "runtime adapter ainda eh stub TODO; falta ligar ao projeto real"
                )

    print(f"\n{'=' * 50}")
    print(f"  harbor-scaffold doctor: {bench_dir}")
    print(f"{'=' * 50}\n")

    for item in ok:
        print(f"  [OK] {item}")
    for item in warnings:
        print(f"  [!!] {item}")
    for item in errors:
        print(f"  [ERRO] {item}")

    print()
    if errors:
        print(f"  {len(errors)} erro(s), {len(warnings)} aviso(s)")
        sys.exit(1)

    print(f"  Tudo certo! {len(warnings)} aviso(s)")
    sys.exit(0)


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="harbor-scaffold - gerador portatil de benchmarks Harbor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Subcomandos")

    p_init = sub.add_parser("init", help="Inicializa benchmark para um projeto")
    p_init.add_argument("project_dir", help="Diretorio raiz do projeto alvo")

    p_create = sub.add_parser(
        "create-bench", help="Gera agent.py e estrutura do benchmark"
    )
    p_create.add_argument(
        "--bench-dir", default=".", help="Diretorio do benchmark (default: .)"
    )

    p_task = sub.add_parser("add-task", help="Adiciona task skeleton")
    p_task.add_argument("name", help="Nome da task (ex: json-validacao)")
    p_task.add_argument(
        "--verifier",
        required=True,
        choices=VALID_VERIFIERS,
        help="Tipo de verifier",
    )
    p_task.add_argument(
        "--difficulty", choices=("easy", "medium", "hard"), default="medium"
    )
    p_task.add_argument(
        "--bench-dir", default=".", help="Diretorio do benchmark (default: .)"
    )

    p_doctor = sub.add_parser("doctor", help="Valida manifest e estrutura do benchmark")
    p_doctor.add_argument(
        "--bench-dir", default=".", help="Diretorio do benchmark (default: .)"
    )

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "create-bench":
        cmd_create_bench(args)
    elif args.command == "add-task":
        cmd_add_task(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
