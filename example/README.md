# text-analyzer example

This directory is a complete `harbor-scaffold` example benchmark.

It demonstrates:

- `direct` strategy
- `cli` backend via `claude -p`
- two standard benchmark tasks
- the optional wiki loop layout
- `wiki-recall` sync scaffolding

## Files

- `manifest.yaml` - the source configuration
- `agent.py` - generated benchmark agent
- `tasks/` - two simple runnable tasks
- `wiki.py` - portable wiki loop entrypoint
- `wiki/` - seed wiki structure with one example page
- `scripts/sync_wiki_recall.py` - generates paired `no-wiki` and `with-wiki` tasks

## Run the benchmark

```bash
cd example
python ../scaffold.py doctor --bench-dir .
uv run harbor run -p tasks/ -n 1 --agent-import-path agent:TextAnalyzerAgent -o jobs --job-name run1
```

## Explore the wiki loop

```bash
cd example
python wiki.py lint
python wiki.py context "word counting heuristics"
python scripts/sync_wiki_recall.py
```

Notes:

- the wiki starts with one sample page in `wiki/pages/`
- `sync_wiki_recall.py` dry-run does not create tasks
- `sync_wiki_recall.py --create` requires a working backend because it asks the model to extract benchmark facts from wiki pages

## Why this example exists

The main scaffold can now generate either:

- benchmark-only projects
- benchmark + wiki loop projects

This example shows the second path without requiring a project-specific runtime adapter.
