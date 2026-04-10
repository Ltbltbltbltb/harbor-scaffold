# Text Analysis Heuristics

_Last updated: 2026-04-10 | Events: 1_

## Summary
This page captures the benchmark-specific heuristics used in the `text-analyzer`
example. The example is intentionally small, but the page shows the shape of a
compiled wiki entry that can later be expanded by real benchmark runs or manual
ingestion. The current benchmark tasks focus on deterministic text-analysis
behaviors such as counting words, identifying sentence boundaries, and producing
simple sentiment classifications in a fixed output format.

For this example, word counting is interpreted as counting natural-language word
tokens in the provided English passage. Sentence counting is based on obvious
terminal punctuation in the sample text. Sentiment classification is expected to
label the provided review as positive because the language is overwhelmingly
favorable and the only criticism is explicitly minor.

## Key Facts
- [HIGH] The `word-count` task expects labeled output with `Words`, `Sentences`, and `Unique words`.
- [HIGH] The seeded `word-count` verifier expects a total word count of `44` and a sentence count of `4`.
- [HIGH] The seeded `sentiment-label` task expects the sentiment label `positive`.
- [MEDIUM] The example wiki is intended to demonstrate how benchmark-local knowledge could later be compiled into reusable pages.

## Open Questions
- How should punctuation-heavy edge cases be normalized for future text-analysis tasks?
- Should future sentiment examples require a stricter rationale format or citation style?

## Source Trail
| Date | Event ID | Contribution |
|------|----------|--------------|
| 2026-04-10 | example-seed-1 | Seeded a starter page to demonstrate the optional wiki loop. |
