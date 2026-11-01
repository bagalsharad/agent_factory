# ADR-0008: Deterministic mock model provider

Status: accepted

CI and first-run demos must not depend on downloading multi-GB models. `MockProvider` deterministically plans
tool calls from known inputs and fills output schemas from evidence; variants simulate an adversarial model,
outages, flakiness, slowness, invalid output and missing tool support. Test reports state that results were
obtained with the mock. Real-inference tests are opt-in (`-m ollama`).
