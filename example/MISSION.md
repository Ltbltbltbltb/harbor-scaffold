# text-analyzer Benchmark - Missao

Projeto: .
Estrategia: direct

## Objetivo

Maximizar o score no benchmark Harbor. Cada episodio:

1. Ler resultados do ultimo run
2. Diagnosticar tasks que falharam
3. Melhorar o harness (agent.py acima do boundary)
4. Rodar benchmark
5. Registrar em results.tsv

## Wiki loop opcional

Arquivos gerados:
- `wiki.py`
- `wiki/`
- `scripts/sync_wiki_recall.py`


Comandos uteis:

```bash
cd example
python wiki.py lint
python scripts/sync_wiki_recall.py --create
```

## Como rodar

```bash
cd example
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:TextAnalyzerAgent -o jobs --job-name run1
```

## Regras

- NAO editar a secao [HARBOR ADAPTER - FIXO] do agent.py
- Foco em melhorias genericas, nao hacks por task
- Commitar apos cada melhoria confirmada
