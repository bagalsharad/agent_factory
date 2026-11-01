# ADR-0003: LangGraph for orchestration

Status: accepted

LangGraph provides state graphs, checkpointing and `interrupt()`/`Command(resume=...)` for human-in-the-loop.
We compile each spec into a `StateGraph` whose nodes call our `Harness`. We do **not** use LangChain agents or
LangChain model wrappers: model access goes through our own gateway so that provider capability handling,
budgets and policy remain under platform control. No other agent framework is used.

Important property: LangGraph re-executes an interrupted node from its start on resume. Therefore every side
effect inside a node is keyed by an idempotency key (`tool_invocations`), and agent loops are split into separate
model and tools nodes so a resume never repeats model calls.
