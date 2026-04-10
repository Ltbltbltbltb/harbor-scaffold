# Guia de Adaptacao — harbor-scaffold

## Visao geral

O harbor-scaffold gera benchmarks Harbor para qualquer projeto. Este guia explica o que fazer apos rodar os comandos do scaffold.

Na versao atual, ele pode operar em dois niveis:

- benchmark Harbor portatil
- memory loop opcional em markdown wiki

Juntos, esses dois niveis formam um sistema fechado:

- o projeto e avaliado por tasks explicitas
- o conhecimento util pode ser compilado na wiki
- a wiki pode voltar como contexto
- a wiki tambem pode virar novas tasks `no-wiki` vs `with-wiki`
- o resultado dessas tasks informa a proxima iteracao do benchmark

Em outras palavras, o scaffold nao organiza so um benchmark. Ele organiza um
loop de melhoria continua em cima do benchmark.

## Fluxo completo

```
1. scaffold.py init <projeto>         # gera manifest.yaml
2. Editar manifest.yaml               # 5-15 min
3. scaffold.py create-bench           # gera agent.py + estrutura
4. scaffold.py add-task <nome>        # para cada task
5. Preencher instruction.md + test.sh # parte manual
6. scaffold.py doctor                 # validar tudo
7. uv run harbor run ...              # rodar benchmark
8. (Opcional) habilitar memory loop   # wiki + wiki-recall
```

## Fluxo sistemico

Quando o trilho de memoria esta ligado, o fluxo deixa de ser linear e passa a
ser ciclico:

```text
runtime/projeto
  -> wiki local
  -> sync_wiki_recall
  -> tasks no-wiki vs with-wiki
  -> harbor run
  -> resultados
  -> ajuste de prompt, task, verifier e memoria
  -> runtime/projeto novamente
```

Essa e a ideia central do repo: nao apenas medir um agente, mas criar uma
estrutura para ele aprender com o proprio historico de execucao e provar se
esse aprendizado ajudou.

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
- `agent.backend` — `cli`, `api`, ou `openai_compat` para direct/context_inject
- `agent.system_prompt` — prompt base (so para direct e context_inject)
- `memory.enabled` — habilite se quiser o trilho opcional de wiki loop

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

**O que editar**:
1. `system_prompt`
2. `backend` se quiser usar API em vez de `claude -p`

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
4. `backend` se quiser usar API em vez de `claude -p`

## Trilho opcional: wiki loop

Se `memory.enabled: true`, o scaffold gera uma camada de memoria portatil dentro do benchmark:

- `wiki.py` — API e CLI para `ingest`, `query`, `context` e `lint`
- `wiki/SCHEMA.md`, `wiki/index.md`, `wiki/log.md`, `wiki/pages/`
- `scripts/sync_wiki_recall.py` — transforma paginas da wiki em pares de tasks `no-wiki` e `with-wiki`

O ponto importante aqui e que a wiki nao e tratada como documentacao passiva.
Ela entra no mesmo circuito de engenharia do benchmark:

- conhecimento novo pode ir para a wiki
- a wiki pode voltar como contexto para o agente
- a wiki pode gerar novas tasks de avaliacao
- essas tasks medem se a memoria compilada esta ajudando de verdade

Por isso faz sentido pensar nesse trilho como um "loop sobre loops".

Esse trilho foi desenhado para preservar a portabilidade do scaffold:

- Parte portatil:
  - estrutura markdown da wiki
  - API generica de ingest/query/lint
  - sync de `wiki-recall`
  - uso do mesmo backend configurado no benchmark
- Parte especifica do projeto:
  - extracao de eventos reais do runtime

Se voce tambem habilitar `memory.runtime_adapter.enabled: true`, o scaffold gera:

- `scripts/export_runtime_events.py` — stub que define o contrato do adapter
- `scripts/sync_runtime_to_wiki.py` — bridge generica do adapter para a wiki

O contrato do adapter e simples: retornar uma lista de eventos com:

- `event_id`
- `topic`
- `summary`
- `facts` (opcional)
- `sources` (opcional)

Fluxo sugerido:

```bash
cd harbor-bench
python wiki.py lint
python wiki.py ingest --topic "example" --summary "..."   # opcional
python scripts/sync_wiki_recall.py --create
python scripts/sync_runtime_to_wiki.py --apply   # so se houver adapter
```

Importante: o scaffold gera o protocolo e o stub. A captura do runtime real continua sendo adaptacao local do projeto.

Veja tambem `example/` no repo para um benchmark pequeno que inclui a estrutura do wiki loop sem depender de adapter de runtime.

## O que e portatil e o que nao e

Para manter a proposta do scaffold, a divisao de responsabilidades e explicita:

- Portatil:
  - estrutura do benchmark Harbor
  - templates de `agent.py`
  - wiki markdown compilada
  - `sync_wiki_recall.py`
  - contrato do runtime adapter
- Especifico do projeto:
  - como capturar eventos reais
  - quais fatos entram na wiki
  - quais fontes ou artefatos fazem sentido no dominio

Essa separacao e parte do valor do repo. Ela permite reaproveitar o sistema em
outros projetos sem fingir que o runtime de todos os projetos e igual.

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
- O wiki loop eh opcional: habilite apenas quando fizer sentido medir memoria compilada ou reaproveitar conhecimento entre runs
- Se ativar o wiki loop, trate as paginas como insumo de benchmark, nao como arquivo morto
