# ADR-0002: Declarative specifications; no generated code

Status: accepted

Agents are `agentfactory/v1` documents (Pydantic models exported as JSON Schema). Workflows reference registered
tools and harness step kinds; templating supports only `{{ input.x }}`, `{{ steps.id.output }}` and
`{{ components.name }}` lookups — no expressions. A spec becomes executable only after semantic validation and a
build. Consequence: agents are safe to review, diff, version and share; new capabilities require registering a
tool (reviewed code), not prompting a model to write one.
