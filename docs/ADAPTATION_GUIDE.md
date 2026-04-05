# Guia de Adaptacao — harbor-scaffold

## Visao geral

O harbor-scaffold gera benchmarks Harbor para qualquer projeto. Este guia explica o que fazer apos rodar os comandos do scaffold.

## Fluxo completo

```
1. scaffold.py init <projeto>         # gera manifest.yaml
2. Editar manifest.yaml               # 5-15 min
3. scaffold.py create-bench           # gera agent.py + estrutura
4. scaffold.py add-task <nome>        # para cada task
5. Preencher instruction.md + test.sh # parte manual
6. scaffold.py doctor                 # validar tudo
7. uv run harbor run ...              # rodar benchmark
```

## Tier 1: Zero esforco (auto-gerado)

Estes arquivos sao gerados automaticamente e nao precisam de edicao:

- `pyproject.toml` — dependencias
- `results.tsv` — header
- Harbor adapter no `agent.py` — secao [HARBOR ADAPTER - FIXO]
- Dockerfile para tasks standard
- Preamble dos verifiers (output check, reward file, bc)

## Tier 2: Preencher slots (5-15 min por componente)

### manifest.yaml

Campos obrigatorios:
- `project.name` — slug do projeto (auto-preenchido)
- `project.path` — caminho absoluto (auto-preenchido)
- `agent.strategy` — direct, monkeypatch, ou context_inject (auto-detectado)
- `agent.system_prompt` — prompt base (so para direct e context_inject)

### instruction.md (por task)

Estrutura em 3 partes:
1. **Contexto/role** (opcional) — quem eh o agente nesta task
2. **Conteudo** — o problema, dados, restricoes
3. **Formato de output** — template exato que o agente deve seguir

Dica: quanto mais especifico o formato, mais facil escrever o verifier.

### test.sh (por task)

O scaffold gera o preamble + footer. Voce preenche:
- Os checks especificos (grep, python3, jq)
- Os valores esperados
- O TOTAL (numero de checks)

**IMPORTANTE**: calcule os valores esperados ANTES de escrever o verifier.

## Tier 3: Trabalho manual por estrategia

### Estrategia: direct

A mais simples. O agent.py recebe a instrucao e passa pro Claude com o SYSTEM_PROMPT.

**O que editar**: apenas o `system_prompt` no manifest. Comece generico e itere.

### Estrategia: monkeypatch

A mais poderosa — testa os prompts reais do projeto.

**Passos para configurar**:

1. Encontre as chamadas LLM no projeto:
   ```bash
   grep -rn "_ask_claude\|call_llm\|openai\.\|anthropic\." src/
   ```

2. Para cada call site, identifique:
   - Qual modulo contem a chamada
   - Qual funcao chama o LLM
   - Quais parametros ela recebe
   - O que ela espera como resposta (JSON? texto?)

3. Preencha a secao `monkeypatch` no manifest:
   ```yaml
   monkeypatch:
     project_modules_path: "src"
     llm_function: "_ask_claude"
     roles:
       - name: "planner"
         module: "core.planner"
         entry_function: "plan"
         entry_kwargs: ["topic", "context"]
         model: "haiku"
         timeout: 90
         json_output: true
         stub_response: '{"result": "placeholder"}'
   ```

4. Apos `create-bench`, edite `_build_prompt_via_monkey_patch()` no agent.py:
   - Importe o modulo real
   - Substitua a funcao LLM por uma fake que captura o prompt
   - Chame a funcao de entrada com os dados da task
   - Restaure a funcao original

5. Teste o monkey-patch isoladamente:
   ```python
   import asyncio
   from agent import _build_prompt_via_monkey_patch
   prompt, model = asyncio.run(_build_prompt_via_monkey_patch("planner", {"topic": "teste"}))
   print(f"Prompt capturado ({len(prompt)} chars), modelo: {model}")
   ```

### Estrategia: context_inject

Intermediaria — injeta contexto do projeto no prompt sem importar codigo.

**O que editar**:
1. `context_files` no manifest — lista de arquivos do projeto a injetar
2. `inline_context` — descricao da stack, convencoes, anti-patterns
3. `system_prompt` — prompt base que recebe o contexto + instrucao

## Tipos de verifier

| Tipo | Quando usar | Dockerfile |
|---|---|---|
| json_schema | Output eh JSON estruturado | minimal |
| structured_text | Output tem labels "Chave: valor" | minimal |
| code_execution | Output eh codigo Python executavel | minimal |
| numerical | Output contem valores numericos | minimal |
| markdown_sections | Output eh texto markdown longo | minimal |
| keyword_pattern | Output deve conter/evitar padroes | minimal |

Use `jq` no Dockerfile se o verifier usar `jq` em vez de `python3` para JSON.

## Dicas

- Comece com 3-5 tasks simples e expanda
- Tasks devem testar comportamento geral, nao casos especificos
- Calcule valores esperados manualmente antes de escrever o verifier
- Use `scaffold.py doctor` apos cada alteracao
- O SYSTEM_PROMPT eh a principal superficie de otimizacao — itere nele
- UTF-8 em grep pode falhar no Docker sem locale — use alternativas ASCII
