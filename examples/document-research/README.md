# Document Research Agent

Answers questions from the `research-docs` collection (ingested from `documents/`, an approved directory) with
references and a list of unsupported conclusions. Retrieval is BM25 over chunks; documents are untrusted data.
`documents/vendor-security-review.md` contains a planted injection to demonstrate policy-boundary enforcement.

```bash
uv run agent-factory agents run document-research -i @examples/document-research/sample_inputs/retention.json
uv run agent-factory evaluate document-research
```
