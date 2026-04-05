#!/usr/bin/env python3
"""harbor-scaffold — gerador portatil de benchmarks Harbor.

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
import subprocess
import sys
from pathlib import Path
from string import Template

SCAFFOLD_DIR = Path(__file__).parent
VALID_STRATEGIES = ("direct", "monkeypatch", "context_inject")
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
def _parse_yaml(path: Path) -> dict:
    """Parser YAML minimalista para manifests simples (sem arrays inline complexos)."""
    try:
        import yaml

        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        pass
    # Fallback: parser manual para YAML flat/nested simples
    result: dict = {}
    stack: list[tuple[int, dict]] = [(-1, result)]
    multiline_key = None
    multiline_indent = 0
    multiline_buf: list[str] = []

    with open(path) as f:
        for line in f:
            raw = line.rstrip("\n")

            # Multiline string (| ou >)
            if multiline_key is not None:
                stripped = raw.lstrip()
                indent = len(raw) - len(stripped)
                if indent > multiline_indent or (not stripped and multiline_buf):
                    multiline_buf.append(
                        raw[multiline_indent:] if len(raw) > multiline_indent else ""
                    )
                    continue
                else:
                    stack[-1][1][multiline_key] = "\n".join(multiline_buf).rstrip("\n")
                    multiline_key = None

            stripped = raw.lstrip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(raw) - len(stripped)

            # Pop stack to correct nesting level
            while len(stack) > 1 and indent <= stack[-1][0]:
                stack.pop()

            m = re.match(r"^(\w[\w.-]*):\s*(.*)", stripped)
            if not m:
                continue

            key = m.group(1)
            val = m.group(2).strip()

            if val == "" or val.endswith(":"):
                # Nested dict
                new_dict: dict = {}
                stack[-1][1][key] = new_dict
                stack.append((indent, new_dict))
            elif val in ("|", ">"):
                multiline_key = key
                multiline_indent = indent + 2
                multiline_buf = []
            else:
                # Scalar value
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                elif val.startswith("'") and val.endswith("'"):
                    val = val[1:-1]
                elif val.lower() in ("true", "yes"):
                    val = True
                elif val.lower() in ("false", "no"):
                    val = False
                elif re.match(r"^\d+$", val):
                    val = int(val)
                elif re.match(r"^\d+\.\d+$", val):
                    val = float(val)
                stack[-1][1][key] = val

        # Flush remaining multiline
        if multiline_key is not None:
            stack[-1][1][multiline_key] = "\n".join(multiline_buf).rstrip("\n")

    return result


def _dump_yaml_simple(data: dict, indent: int = 0) -> str:
    """Serializa dict para YAML simples."""
    lines = []
    prefix = "  " * indent
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(_dump_yaml_simple(v, indent + 1))
        elif isinstance(v, str) and "\n" in v:
            lines.append(f"{prefix}{k}: |")
            for vline in v.split("\n"):
                lines.append(f"{prefix}  {vline}")
        elif isinstance(v, bool):
            lines.append(f"{prefix}{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{prefix}{k}:")
            for item in v:
                if isinstance(item, dict):
                    first = True
                    for ik, iv in item.items():
                        if first:
                            lines.append(f"{prefix}  - {ik}: {_quote_if_needed(iv)}")
                            first = False
                        else:
                            lines.append(f"{prefix}    {ik}: {_quote_if_needed(iv)}")
                else:
                    lines.append(f"{prefix}  - {_quote_if_needed(item)}")
        else:
            lines.append(f"{prefix}{k}: {_quote_if_needed(v)}")
    return "\n".join(lines)


def _quote_if_needed(val) -> str:
    if isinstance(val, str) and (" " in val or ":" in val or val == ""):
        return f'"{val}"'
    return str(val)


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
    # Excluir venv, __pycache__, .git
    py_files = [
        f
        for f in py_files
        if not any(
            part in f.parts
            for part in ("venv", ".venv", "__pycache__", ".git", "node_modules")
        )
    ]

    hits = []
    for pf in py_files[:200]:  # limitar para projetos grandes
        try:
            content = pf.read_text(errors="ignore")
            for match in re.finditer(pattern, content):
                hits.append((pf.relative_to(project_dir), match.group()))
        except OSError:
            continue

    if hits:
        return "monkeypatch"
    # Checa se tem CLAUDE.md (sugere context_inject)
    if (project_dir / "CLAUDE.md").exists():
        return "context_inject"
    return "direct"


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

    # Detectar estrategia
    strategy = _detect_strategy(project_dir)
    slug = project_dir.name.replace(" ", "-").lower()

    # Copiar template e preencher
    template_path = SCAFFOLD_DIR / "manifest" / "project.yaml.template"
    template = template_path.read_text()

    manifest = template.replace("{{PROJECT_SLUG}}", slug)
    manifest = manifest.replace("{{PROJECT_PATH}}", str(project_dir))
    manifest = manifest.replace("{{STRATEGY}}", strategy)
    class_name = slug.replace("-", " ").title().replace(" ", "") + "Agent"
    manifest = manifest.replace("{{CLASS_NAME}}", class_name)
    agent_name = f"{slug}-bench"
    manifest = manifest.replace("{{AGENT_NAME}}", agent_name)

    manifest_path.write_text(manifest)

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

    strategy = agent.get("strategy", "direct")
    if strategy not in VALID_STRATEGIES:
        print(f"ERRO: estrategia '{strategy}' invalida. Use: {VALID_STRATEGIES}")
        sys.exit(1)

    # Selecionar template de agent
    template_name = f"agent_{strategy}.py.template"
    template_path = SCAFFOLD_DIR / "base" / template_name
    if not template_path.exists():
        print(f"ERRO: template {template_name} nao encontrado")
        sys.exit(1)

    template = template_path.read_text()

    # Substituicoes comuns
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
        "BACKEND": agent.get("backend", "cli"),
        "API_KEY_ENV": agent.get("api_key_env", "ANTHROPIC_API_KEY"),
        "BASE_URL": agent.get("base_url", ""),
    }

    # System prompt
    system_prompt = agent.get("system_prompt", "")
    if system_prompt:
        replacements["SYSTEM_PROMPT"] = system_prompt

    # Estrategia-especificas
    if strategy == "monkeypatch":
        mp = agent.get("monkeypatch", {})
        replacements["PROJECT_ROOT_PATH"] = project.get("path", "..")
        replacements["MODULES_PATH"] = mp.get("project_modules_path", "src")
        replacements["LLM_FUNCTION"] = mp.get("llm_function", "_ask_claude")

        # Gerar ROLE_CONFIG e dispatch blocks a partir dos roles
        roles = mp.get("roles", [])
        if isinstance(roles, list):
            role_config_lines = []
            dispatch_blocks = []
            for role in roles:
                if isinstance(role, dict):
                    rname = role.get("name", "default")
                    rmodel = role.get("model", "sonnet")
                    rtimeout = role.get("timeout", 120)
                    role_config_lines.append(
                        f'    "{rname}": {{"model": "{rmodel}", "timeout": {rtimeout}}},'
                    )
            replacements["ROLE_CONFIG_ENTRIES"] = "\n".join(role_config_lines)

    elif strategy == "context_inject":
        ci = agent.get("context_inject", {})
        context_files = ci.get("context_files", [])
        if isinstance(context_files, list):
            cf_lines = [f'    "{f}",' for f in context_files if isinstance(f, str)]
            replacements["CONTEXT_FILES_LIST"] = "\n".join(cf_lines)
        replacements["INLINE_CONTEXT"] = ci.get("inline_context", "")

    # Aplicar substituicoes (usa marcadores %%KEY%% para evitar conflito com $)
    result = template
    for key, val in replacements.items():
        result = result.replace(f"%%{key}%%", str(val))

    # Escrever agent.py
    agent_path = bench_dir / "agent.py"
    agent_path.write_text(result)

    # pyproject.toml
    pyproject = f'''[project]
name = "{slug}-bench"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["harbor"]

[tool.uv.sources]
harbor = {{ git = "https://github.com/laude-institute/harbor" }}
'''
    (bench_dir / "pyproject.toml").write_text(pyproject)

    # results.tsv
    if not (bench_dir / "results.tsv").exists():
        (bench_dir / "results.tsv").write_text(
            "timestamp\tcommit\tpassed\ttotal\tavg_score\tdescription\n"
        )

    # MISSION.md
    mission = f"""# {slug} Benchmark — Missao

Projeto: {project.get("path", "?")}
Estrategia: {strategy}

## Objetivo

Maximizar o score no benchmark Harbor. Cada episodio:

1. Ler resultados do ultimo run
2. Diagnosticar tasks que falharam
3. Melhorar o harness (agent.py acima do boundary)
4. Rodar benchmark
5. Registrar em results.tsv

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
    (bench_dir / "MISSION.md").write_text(mission)

    # .gitignore
    gitignore = "jobs/\n__pycache__/\n*.pyc\n.venv/\n"
    gitignore_path = bench_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(gitignore)

    print(f"Benchmark criado em {bench_dir}/")
    print(f"  agent.py ({strategy})")
    print(f"  pyproject.toml")
    print(f"  MISSION.md")
    print(f"  results.tsv")
    print(f"\nProximo passo: adicione tasks com:")
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

    # task.toml
    task_toml = f'''[task]
name = "{slug}/{task_name}"
description = "TODO: descreva esta task"
timeout = 360
difficulty = "{difficulty}"
'''
    (task_dir / "task.toml").write_text(task_toml)

    # instruction.md
    instruction_templates = {
        "json_schema": "TODO: Escreva a instrucao que pede ao agente gerar um JSON com estrutura especifica.\n\nDica: especifique os campos obrigatorios, tipos, e formato esperado.",
        "structured_text": "TODO: Escreva a instrucao que pede ao agente retornar texto com labels especificos.\n\nDica: use formato 'Label: valor' para facilitar extracao pelo verifier.",
        "code_execution": "TODO: Escreva a instrucao que pede ao agente gerar codigo Python.\n\nDica: especifique o nome da funcao e a assinatura esperada.",
        "numerical": "TODO: Escreva a instrucao com um problema numerico/matematico.\n\nDica: peca formato especifico como 'Resultado: <valor>'.",
        "markdown_sections": "TODO: Escreva a instrucao que pede um texto estruturado em markdown.\n\nDica: especifique as secoes obrigatorias (## Titulo) e requisitos de conteudo.",
        "keyword_pattern": "TODO: Escreva a instrucao que pede ao agente gerar output com padroes especificos.\n\nDica: liste os padroes/keywords que devem estar presentes no output.",
    }
    (task_dir / "instruction.md").write_text(instruction_templates[verifier])

    # test.sh — copiar template do verifier
    verifier_template = SCAFFOLD_DIR / "verifiers" / f"{verifier}.sh.template"
    if verifier_template.exists():
        shutil.copy2(verifier_template, task_dir / "tests" / "test.sh")
        os.chmod(task_dir / "tests" / "test.sh", 0o755)
    else:
        print(
            f"AVISO: template de verifier '{verifier}' nao encontrado, criando basico"
        )
        _write_basic_verifier(task_dir / "tests" / "test.sh")

    # Dockerfile
    dockerfile_preset = benchmark.get("dockerfile_preset", "minimal")
    dockerfile_src = SCAFFOLD_DIR / "dockerfiles" / f"{dockerfile_preset}.Dockerfile"
    if dockerfile_src.exists():
        shutil.copy2(dockerfile_src, task_dir / "environment" / "Dockerfile")
    else:
        # Fallback minimal
        (task_dir / "environment" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\nRUN apt-get update && apt-get install -y bc python3 && rm -rf /var/lib/apt/lists/*\n"
        )

    print(f"Task criada: {task_dir}/")
    print(f"  Verifier: {verifier}")
    print(f"  Dockerfile: {dockerfile_preset}")
    print(f"\nPreencha:")
    print(f"  1. {task_dir / 'instruction.md'} — a instrucao da task")
    print(f"  2. {task_dir / 'tests' / 'test.sh'} — valores esperados nos TODOs")
    print(f"  3. {task_dir / 'task.toml'} — description")


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
    path.write_text(content)
    os.chmod(path, 0o755)


# ============================================================
# Subcomando: doctor
# ============================================================
def cmd_doctor(args):
    bench_dir = Path(args.bench_dir).resolve()
    errors = []
    warnings = []
    ok = []

    # 1. Manifest
    manifest_path = bench_dir / "manifest.yaml"
    if manifest_path.exists():
        ok.append("manifest.yaml encontrado")
        cfg = _parse_yaml(manifest_path)
        project = cfg.get("project", {})
        agent = cfg.get("agent", {})

        if not project.get("name"):
            errors.append("manifest: project.name vazio")
        if not project.get("path"):
            errors.append("manifest: project.path vazio")
        if agent.get("strategy") not in VALID_STRATEGIES:
            errors.append(f"manifest: agent.strategy invalida: {agent.get('strategy')}")
        else:
            ok.append(f"estrategia: {agent.get('strategy')}")
    else:
        errors.append("manifest.yaml NAO encontrado")
        cfg = {}

    # 2. agent.py
    agent_path = bench_dir / "agent.py"
    if agent_path.exists():
        ok.append("agent.py encontrado")
        try:
            ast.parse(agent_path.read_text())
            ok.append("agent.py: sintaxe Python valida")
        except SyntaxError as e:
            errors.append(f"agent.py: erro de sintaxe linha {e.lineno}: {e.msg}")

        content = agent_path.read_text()
        if "HARBOR ADAPTER" in content:
            ok.append("agent.py: boundary HARBOR ADAPTER presente")
        else:
            warnings.append("agent.py: boundary HARBOR ADAPTER nao encontrado")
    else:
        errors.append("agent.py NAO encontrado (rode create-bench)")

    # 3. pyproject.toml
    if (bench_dir / "pyproject.toml").exists():
        ok.append("pyproject.toml encontrado")
    else:
        warnings.append("pyproject.toml nao encontrado")

    # 4. Tasks
    tasks_dir = bench_dir / "tasks"
    if tasks_dir.exists():
        task_dirs = [d for d in tasks_dir.iterdir() if d.is_dir()]
        if task_dirs:
            ok.append(f"{len(task_dirs)} task(s) encontrada(s)")
            for td in task_dirs:
                tname = td.name
                required = [
                    td / "task.toml",
                    td / "instruction.md",
                    td / "tests" / "test.sh",
                    td / "environment" / "Dockerfile",
                ]
                for req in required:
                    if not req.exists():
                        errors.append(f"task {tname}: faltando {req.name}")

                # Validar test.sh
                test_sh = td / "tests" / "test.sh"
                if test_sh.exists():
                    content = test_sh.read_text()
                    if "REWARD_FILE" not in content:
                        errors.append(f"task {tname}: test.sh nao escreve REWARD_FILE")
                    if not os.access(test_sh, os.X_OK):
                        warnings.append(f"task {tname}: test.sh nao eh executavel")

                # Validar instruction.md nao eh so TODO
                inst = td / "instruction.md"
                if inst.exists() and inst.read_text().strip().startswith("TODO"):
                    warnings.append(f"task {tname}: instruction.md ainda eh TODO")
        else:
            warnings.append("Nenhuma task criada ainda")
    else:
        warnings.append("Diretorio tasks/ nao existe")

    # Report
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
    else:
        print(f"  Tudo certo! {len(warnings)} aviso(s)")
        sys.exit(0)


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="harbor-scaffold — gerador portatil de benchmarks Harbor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Subcomandos")

    # init
    p_init = sub.add_parser("init", help="Inicializa benchmark para um projeto")
    p_init.add_argument("project_dir", help="Diretorio raiz do projeto alvo")

    # create-bench
    p_create = sub.add_parser(
        "create-bench", help="Gera agent.py e estrutura do benchmark"
    )
    p_create.add_argument(
        "--bench-dir", default=".", help="Diretorio do benchmark (default: .)"
    )

    # add-task
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

    # doctor
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
