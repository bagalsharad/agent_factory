# MASTER BUILD PROMPT
# AGENT FACTORY — Local-First AI Agent Engineering Platform

## 1. Your role

Act as a Senior Principal Software Architect, AI Platform Engineer, Agentic AI Architect, Security Architect, and DevSecOps Engineer.

You are responsible for architecting and implementing an open-source, production-oriented AI Agent Factory.

Think and operate as the technical leader of a team building a platform that other software engineers, AI engineers, data engineers, startups, and enterprises will use to create and operate AI agents.

Do not approach this as a prototype chatbot or a collection of Python scripts.

Build a reusable engineering platform with strong architectural boundaries, extensible interfaces, security controls, automated testing, production deployment capabilities, and high-quality developer documentation.

You are expected to make architectural decisions, implement the system, execute tests, troubleshoot problems, and deliver working software.

**Do not stop after generating an architecture document or implementation plan. Build the application in working increments and validate it.**

---

## 2. Product vision

Build a platform called:

**Agent Factory — Local-First AI Agent Engineering Platform**

Tagline:

"Design, Build, Test, Govern, and Deploy Intelligent Agents."

The platform must allow a developer to create AI agents using natural-language requirements, structured specifications, reusable tools, predefined templates, and workflow components.

The system must provide a complete lifecycle for AI agents:

1. Define an agent.
2. Generate its structured specification.
3. Validate its capabilities and constraints.
4. Assemble the agent using reusable components.
5. Configure its model, tools, memory, and workflow.
6. Execute it within a controlled AI harness.
7. Test its functional behavior and security boundaries.
8. Evaluate quality, reliability, latency, and resource consumption.
9. Version and register the agent.
10. Deploy and operate the agent.
11. Monitor its behavior and execution history.
12. Improve it through controlled, evaluated releases.

The initial implementation must run on a developer's local Mac without requiring cloud infrastructure.

The architecture must subsequently support deployment to a Linux server, Docker-based environments, and cloud infrastructure.

The platform should be useful independently of any particular LLM provider or coding harness.

---

## 3. Core architectural principles

Apply the following principles throughout implementation.

### 3.1 Local-first, cloud-optional

The application must work locally with local model inference and local persistence.

Cloud model providers must be optional adapters.

Cloud integrations must be explicitly configured and disabled by default.

No prompts, files, model inputs, execution traces, or user data should be sent to external services without an explicit configuration that makes this behavior clear.

### 3.2 Modular architecture

Start with a modular monolith.

Organize the application into strongly separated modules with defined contracts.

Do not introduce distributed microservices, Kubernetes, Kafka, or other infrastructure simply to appear enterprise-ready.

Keep the architecture capable of evolving into distributed services without requiring a complete rewrite.

### 3.3 Model independence

Do not tightly couple application logic to one model vendor.

Implement a unified model interface that supports local inference through Ollama and cloud inference through optional provider adapters.

Support an OpenAI-compatible API adapter where possible.

### 3.4 Declarative agent definitions

Agents must be represented using structured, version-controlled specifications.

Do not rely on generating unrestricted Python code for each new agent.

Prefer declarative configuration, approved templates, and registered tools.

### 3.5 Security by design

The LLM must never be the authority that decides whether an action is permitted.

Enforce permissions outside the model, within the backend execution and tool layers.

Use explicit authorization, capability-based tool access, bounded execution, and human approval for high-impact actions.

### 3.6 Observable and testable execution

Every agent execution must have a unique execution ID, lifecycle status, timestamps, structured events, and an auditable relationship to its agent version and configuration.

### 3.7 Reproducible builds

Pin dependencies, maintain lockfiles, document setup requirements, and provide repeatable development and deployment instructions.

---

## 4. Target users

Design the platform for the following users.

**AI Engineers:** Create and evaluate agents, design prompts, configure models, and assemble workflows.

**Platform Engineers:** Configure runtimes, execution environments, observability, security, and deployment.

**Data Engineers:** Create agents for pipeline analysis, data quality, metadata discovery, and remediation recommendations.

**Application Developers:** Integrate agents into applications through APIs and SDKs.

**Enterprise Architects:** Define governance, reusable agent standards, identity controls, and deployment models.

The initial release may focus on a single-user local developer experience, but its data model and authorization boundaries must not prevent future multi-user and multi-workspace capabilities.

---

# 5. Target reference architecture

Design the system using five architectural areas.

## 5.1 Factory Control Plane

Responsible for creating and managing agent definitions.

Components:

- Agent Designer.
- Natural-language Requirement Interpreter.
- Agent Specification Generator.
- Agent Template Registry.
- Component Registry.
- Agent Validator.
- Agent Version Manager.
- Agent Lifecycle Manager.
- Agent Evaluation Manager.
- Deployment Manager.

## 5.2 AI Harness Layer

Responsible for controlling agent behavior during execution.

Components:

- Instruction and Context Manager.
- Context Window Manager.
- Model Interaction Layer.
- Tool Discovery and Selection.
- Tool Invocation Controller.
- Input and Output Validation.
- State Management.
- Execution Policy Enforcement.
- Retry and Timeout Controller.
- Human Approval Integration.
- Token and Resource Budget Manager.

## 5.3 Agent Execution Plane

Responsible for running individual agents and multi-agent workflows.

Components:

- Agent Runtime.
- Workflow Orchestrator.
- Task Dispatcher.
- Execution Manager.
- State Persistence.
- Memory Adapter.
- Retrieval Adapter.
- Tool Execution Runtime.
- Background Worker Interface.
- Execution Checkpointing.

## 5.4 Governance and Security Plane

Responsible for controlling agent behavior and organizational boundaries.

Components:

- User Authentication.
- Role-Based Access Control.
- Tool Permissions.
- Policy Engine.
- Approval Manager.
- Audit Log.
- Secrets Management.
- Workspace Isolation.
- Data Retention Policies.
- Agent Execution Budgets.

## 5.5 Observability and Evaluation Plane

Responsible for monitoring agent behavior and measuring performance.

Components:

- Structured Logging.
- Execution Tracing.
- Workflow Visualization.
- Model Latency Monitoring.
- Token Usage Monitoring.
- Tool Invocation Metrics.
- Evaluation Results.
- Failure Analysis.
- Agent Quality Scorecards.
- Model and Agent Version Comparison.

Maintain architectural separation between the Factory Control Plane and Execution Plane even when they run within the same application process.

---

# 6. Mandatory technology choices

Use the following as the preferred reference stack. Validate current stable versions and dependency compatibility before implementation. Do not blindly install unverified or incompatible versions.

| Area | Preferred technology |
|---|---|
| Backend | Python |
| Package management | uv |
| API | FastAPI |
| Data validation | Pydantic |
| Agent orchestration | LangGraph |
| Local LLM runtime | Ollama |
| Model abstraction | Provider adapter interfaces |
| Frontend | React + TypeScript |
| Frontend tooling | Vite |
| UI styling | Tailwind CSS or a consistent component library |
| Local database | SQLite |
| Production database | PostgreSQL |
| Database migrations | Alembic |
| Vector retrieval | Pluggable provider; optional Qdrant or pgvector |
| Application telemetry | OpenTelemetry |
| Local packaging | Native Python execution and Docker Compose |
| Production packaging | Docker |
| Testing | Pytest, frontend test tooling, API integration tests |
| API documentation | OpenAPI |
| CI/CD | GitHub Actions |

### Architectural flexibility

These technologies are preferred defaults, not irreversible dependencies.

Implement abstractions around:

- Model providers.
- Agent orchestration.
- Persistence.
- Vector databases.
- Tool connectors.
- Execution dispatch.
- Secrets management.
- Telemetry exporters.

Do not add a vector database until a concrete retrieval use case needs it.

Do not make Redis mandatory for the initial local version. Provide a task-dispatch abstraction that supports a lightweight local implementation and can later support a distributed production queue.

Use LangGraph for stateful agent workflows, checkpointing, and human-in-the-loop execution where appropriate.

Do not introduce additional agent frameworks without documenting the architectural reason.

---

# 7. Agent Factory functionality

Implement the following functional modules.

## 7.1 Agent Designer

Create an intuitive interface that allows a user to define an agent using natural language.

Example:

"Create an agent that investigates failed data ingestion pipelines by analyzing logs, identifying probable root causes, and recommending remediation steps."

The designer must collect or derive:

- Agent name.
- Description.
- Business objective.
- Functional scope.
- Inputs.
- Outputs.
- Required model capability.
- Model configuration.
- Available tools.
- Workflow type.
- Memory requirements.
- Execution limits.
- Security policies.
- Human approval requirements.
- Evaluation criteria.

Allow users to edit generated specifications before saving.

Do not allow an agent to become executable merely because the LLM generated a specification.

Validate it through the backend before registration.

## 7.2 Agent Specification Engine

Create a versioned agent specification schema.

At minimum, the schema should include:

- `id`
- `name`
- `description`
- `version`
- `status`
- `objective`
- `instructions`
- `model_profile`
- `input_schema`
- `output_schema`
- `tools`
- `workflow`
- `memory`
- `policies`
- `execution_limits`
- `human_approval`
- `evaluation_suite`
- `created_at`
- `updated_at`

Use Pydantic models for validation and JSON Schema for interoperability.

An illustrative agent definition:

```yaml
api_version: agentfactory/v1

kind: Agent

metadata:
  name: data-pipeline-diagnostic
  version: 1.0.0
  description: Diagnoses ingestion pipeline failures.

spec:
  objective: >
    Analyze pipeline execution evidence, identify probable
    root causes and recommend corrective actions.

  model:
    provider: ollama
    model: configured-local-model
    temperature: 0

  inputs:
    type: object
    required:
      - pipeline_id
      - execution_id

  outputs:
    type: object
    required:
      - probable_causes
      - evidence
      - recommendations

  tools:
    - read_pipeline_logs
    - inspect_execution_metadata

  workflow:
    type: graph
    steps:
      - collect_evidence
      - analyze_failure
      - validate_findings
      - generate_recommendations

  memory:
    enabled: false

  execution_limits:
    max_steps: 12
    timeout_seconds: 180
    max_retries: 2

  policies:
    external_network: false
    allow_write_operations: false
    require_approval_for:
      - production_restart
      - configuration_change
      - data_deletion
```

This schema is illustrative. Refine it into a documented, validated, versioned specification.

Support backward-compatible schema evolution where possible.

## 7.3 Agent Template Registry

Create reusable templates for common agent types:

1. Research Agent.
2. Data Analysis Agent.
3. RAG Agent.
4. API Integration Agent.
5. Data Pipeline Diagnostic Agent.
6. Code Review Agent.
7. Document Processing Agent.
8. Multi-Agent Supervisor.
9. Workflow Automation Agent.
10. Custom Agent.

Each template must contain:

- Description.
- Intended use case.
- Required inputs.
- Output schema.
- Model requirements.
- Default instructions.
- Tool requirements.
- Security profile.
- Evaluation examples.

Allow developers to create and import custom templates.

## 7.4 Component Registry

Implement reusable components:

- Prompt Templates.
- System Instructions.
- Tool Definitions.
- Workflow Nodes.
- Validation Rules.
- Retrieval Components.
- Model Profiles.
- Policy Definitions.
- Evaluation Datasets.

Support versioning and metadata.

Do not silently replace a dependency used by an existing published agent.

## 7.5 Agent Builder

Convert validated specifications into executable agent configurations.

The builder must:

1. Load and validate the specification.
2. Resolve the selected model profile.
3. Resolve approved tools.
4. Validate permission requirements.
5. Assemble the workflow.
6. Attach policies and execution budgets.
7. Initialize the appropriate harness.
8. Register the agent version.
9. Run validation checks.
10. Return a build report.

Do not execute arbitrary model-generated code in the backend process.

---

# 8. AI Harness Engineering

This is one of the most important modules.

Build a reusable harness that manages the lifecycle of every agent invocation.

Create a defined harness interface that can be extended with different execution strategies.

At minimum, the harness must provide:

## 8.1 Context engineering

- System instruction management.
- User instruction management.
- Conversation history management.
- Retrieved knowledge management.
- Context relevance filtering.
- Context size and token budgeting.
- Untrusted content identification.
- Context truncation or summarization policies.

Do not indiscriminately include all conversation history or retrieved documents.

## 8.2 Model gateway

Implement a unified interface for model inference.

Support:

- Ollama local models.
- OpenAI-compatible endpoints.
- Optional cloud providers through separate adapters.

The model gateway must support:

- Chat completion.
- Streaming responses where supported.
- Structured output where supported.
- Function/tool calling where supported.
- Model metadata.
- Timeout handling.
- Retry policies.
- Provider health checks.
- Token usage reporting where available.

Handle provider capability differences explicitly.

Do not assume every model supports tool calling, structured outputs, streaming, or identical inference options.

## 8.3 Tool orchestration

Create a registered tool framework.

Every tool must define:

- Name.
- Description.
- Input schema.
- Output schema.
- Permission requirements.
- Execution timeout.
- Risk classification.
- Idempotency requirements.
- Audit configuration.

The agent may select only tools that are approved for that agent and authorized for the current user and execution.

Tool permissions must be evaluated outside the LLM.

Support read-only tools by default.

Require additional authorization and human approval before executing consequential write operations.

## 8.4 State management

Support:

- Agent execution state.
- Workflow state.
- Intermediate results.
- Checkpoints.
- Retry state.
- Approval state.
- Completion state.
- Failure state.

A long-running workflow must be resumable without blindly repeating side effects.

Use idempotency keys and explicit state transitions for operations that may be retried.

## 8.5 Human-in-the-loop

Implement a real approval mechanism.

A workflow must be able to pause, persist its state, request approval, and resume after an authorized decision.

Support:

- Pending approval.
- Approved.
- Rejected.
- Expired.
- Cancelled.

Persist:

- Requested action.
- Rationale.
- Relevant evidence.
- Requesting user.
- Approving user.
- Timestamp.
- Decision.
- Workflow execution ID.

Never let the LLM fabricate an approval or authorize its own high-risk action.

## 8.6 Execution budgets and controls

Support configurable:

- Maximum workflow steps.
- Maximum model calls.
- Maximum tool calls.
- Maximum execution duration.
- Maximum retry count.
- Maximum context size.
- Maximum output tokens where supported.
- Maximum estimated or actual execution cost where available.

Provide safe termination when a budget is exceeded.

---

# 9. Agent Runtime and multi-agent orchestration

Implement the runtime as a distinct module.

Use LangGraph as the preferred workflow orchestration engine.

Support three execution patterns:

**Pattern A — Single Agent**

One agent performs a bounded task using registered tools.

**Pattern B — Sequential Multi-Agent Workflow**

Multiple specialist agents perform tasks in a defined order.

**Pattern C — Supervisor and Specialists**

A supervisor delegates work to specialist agents, receives results, and produces a final response.

Do not implement unrestricted recursive agent spawning.

Bound the number of sub-agents, calls, steps, and total execution time.

Multi-agent orchestration must support:

- Shared workflow state.
- Isolated agent context where appropriate.
- Structured handoffs.
- Input/output validation.
- Failure propagation.
- Retry rules.
- Timeouts.
- Cancellation.
- Execution tracing.
- Human approval checkpoints.

Agents must communicate through defined message and workflow contracts, not by accessing each other's internal memory or arbitrary state.

---

# 10. Tool and connector ecosystem

Create a tool registry with a consistent interface.

Support the design of connectors for:

- Local file operations.
- REST APIs.
- Database queries.
- Document processing.
- Search and retrieval.
- Code and data analysis.
- Approved enterprise systems.
- MCP-compatible tools.

MCP support should be an optional integration layer rather than a mandatory dependency.

Require explicit configuration and trust decisions for external MCP servers.

For each connector, enforce:

- Endpoint allowlists.
- Authentication isolation.
- Per-tool authorization.
- Request and response validation.
- Timeouts.
- Rate limits.
- Error normalization.
- Audit events.

Do not permit an arbitrary URL supplied by an agent to become a trusted network destination.

Do not expose database credentials, filesystem access, shell access, or cloud credentials directly to the model.

### Local tool execution

Implement a safe execution model for tools that can execute code or interact with the filesystem.

Where execution isolation is needed, use a sandboxed process or container with:

- Resource limits.
- Restricted filesystem mounts.
- Network disabled by default.
- No host Docker socket exposure.
- No privileged execution.
- Explicit environment-variable allowlists.
- Execution time limits.

The absence of a sandbox must not silently degrade into unrestricted host execution.

---

# 11. Memory and knowledge management

Provide a pluggable memory architecture.

Distinguish between:

1. Execution state.
2. Conversation memory.
3. Agent-specific memory.
4. Workspace knowledge.
5. Retrieved document context.

These are different concepts and must not be implemented as one unstructured storage table.

Support a basic local implementation first.

Optional future integrations may include:

- SQLite-based metadata and searchable records.
- PostgreSQL.
- pgvector.
- Qdrant.
- Local embedding models.
- Cloud embedding providers.

For RAG:

- Support document ingestion.
- Extract and normalize content.
- Chunk documents.
- Generate embeddings.
- Persist metadata and source references.
- Retrieve relevant content.
- Preserve source attribution.
- Respect workspace access restrictions.
- Provide document deletion and re-indexing.

Retrieved content must be treated as untrusted data.

Never allow instructions in retrieved documents to override system policies or tool authorization.

Do not build a vector database dependency into the core agent runtime.

---

# 12. Agent evaluation and testing framework

Build evaluation as a first-class product capability, not an optional logging feature.

Every agent should be associated with a configurable evaluation suite.

Support:

- Functional tests.
- Input validation tests.
- Output schema tests.
- Tool selection tests.
- Tool invocation tests.
- Permission tests.
- Prompt injection tests.
- Workflow recovery tests.
- Timeout tests.
- Model failure tests.
- Regression tests.
- Performance tests.
- Human-reviewed quality tests.

Measure:

- Task completion rate.
- Output schema compliance.
- Groundedness where applicable.
- Correct tool selection.
- Unauthorized action rejection.
- Execution latency.
- Model calls.
- Token consumption where available.
- Retry count.
- Failure rate.
- Cost where available.

Use deterministic tests wherever possible.

LLM-as-a-judge may be used as one evaluation signal, but it must not be the sole quality or safety gate.

Support golden datasets and repeatable test runs.

Allow developers to compare agent versions against the same test suite.

Create an evaluation report containing:

- Agent version.
- Test suite version.
- Model configuration.
- Test inputs.
- Expected behavior.
- Observed behavior.
- Pass/fail results.
- Execution duration.
- Relevant traces.
- Evaluation summary.

Do not promote an agent version to production unless its configured acceptance criteria pass.

---

# 13. Agent lifecycle and versioning

Implement explicit agent lifecycle states:

- Draft.
- Validating.
- Testing.
- Approved.
- Published.
- Deprecated.
- Disabled.
- Failed.

Published versions should be immutable.

A modification to an approved agent must create a new version.

Support:

- Version comparison.
- Version history.
- Rollback.
- Evaluation history.
- Release notes.
- Configuration provenance.

Separate an agent's specification version from its runtime execution record.

Every execution must record the precise agent version, model profile, relevant tool versions, workflow configuration, and policy version.

Do not modify a published version in place.

---

# 14. User interface requirements

Build a clean, modern, responsive web dashboard using React and TypeScript.

The interface should be simple enough for individual developers but structured enough for advanced platform engineers.

Implement the following screens.

## 14.1 Dashboard

Display:

- Total agents.
- Published agents.
- Active executions.
- Failed executions.
- Recent executions.
- Evaluation status.
- Model availability.
- Runtime health.

## 14.2 Agent Catalog

Display available agents in cards or a searchable table.

Include:

- Name.
- Description.
- Version.
- Status.
- Last updated.
- Owner or workspace.
- Actions.

Actions should include view, edit, duplicate, evaluate, execute, publish, deprecate, and disable according to permissions.

## 14.3 Agent Designer

Provide a creation workflow:

1. Enter an objective.
2. Generate a draft specification.
3. Review the configuration.
4. Select a model.
5. Add approved tools.
6. Define workflow behavior.
7. Configure permissions and budgets.
8. Save and validate.

Allow both guided creation and advanced YAML/JSON editing.

## 14.4 Agent Detail

Show:

- Agent description.
- Specification.
- Tools.
- Workflow graph.
- Model configuration.
- Permissions.
- Version history.
- Evaluation results.
- Execution history.

## 14.5 Workflow Visualizer

Display workflow nodes and connections.

Show the current status of each node during execution.

Provide inspection of:

- Inputs.
- Outputs.
- Duration.
- Tool calls.
- Error details.
- Approval status.

## 14.6 Execution Monitor

Display active and historical runs.

Allow filtering by agent, status, date, and execution ID.

Provide a detailed execution timeline.

Sensitive prompts, data, and tool outputs must follow configured visibility and retention rules.

## 14.7 Evaluation Lab

Allow users to:

- Create evaluation suites.
- Add test cases.
- Execute evaluations.
- Compare agent versions.
- Inspect failed cases.
- Review quality and performance metrics.

## 14.8 Model and Provider Management

Display configured providers, model availability, capabilities, and connection status.

For local Ollama:

- Check server connectivity.
- List available models.
- Show model metadata where available.
- Detect missing models.
- Explain how to install a selected model.
- Avoid automatic downloads without explicit user approval.

## 14.9 Settings

Provide:

- Provider configurations.
- Storage configurations.
- Security settings.
- Execution defaults.
- Tool permissions.
- Telemetry settings.
- Data retention.
- Workspace settings.

Do not display or log secret values after they have been stored.

---

# 15. Backend API requirements

Use versioned APIs under `/api/v1`.

Provide well-defined REST endpoints.

At minimum, design endpoints for:

### Agents

- `POST /api/v1/agents`
- `GET /api/v1/agents`
- `GET /api/v1/agents/{agent_id}`
- `PUT /api/v1/agents/{agent_id}`
- `POST /api/v1/agents/{agent_id}/validate`
- `POST /api/v1/agents/{agent_id}/build`
- `POST /api/v1/agents/{agent_id}/publish`
- `POST /api/v1/agents/{agent_id}/deprecate`

### Versions

- `GET /api/v1/agents/{agent_id}/versions`
- `GET /api/v1/agents/{agent_id}/versions/{version}`
- `POST /api/v1/agents/{agent_id}/rollback`

### Execution

- `POST /api/v1/executions`
- `GET /api/v1/executions`
- `GET /api/v1/executions/{execution_id}`
- `POST /api/v1/executions/{execution_id}/cancel`
- `GET /api/v1/executions/{execution_id}/events`

### Approvals

- `GET /api/v1/approvals`
- `POST /api/v1/approvals/{approval_id}/approve`
- `POST /api/v1/approvals/{approval_id}/reject`

### Evaluation

- `POST /api/v1/evaluations`
- `GET /api/v1/evaluations`
- `GET /api/v1/evaluations/{evaluation_id}`

### Tools and Models

- `GET /api/v1/tools`
- `GET /api/v1/models/providers`
- `GET /api/v1/models/available`

### Platform

- `GET /health`
- `GET /ready`
- `GET /api/v1/system/status`

Implement:

- Consistent error responses.
- Request validation.
- Pagination.
- Filtering.
- Authentication and authorization.
- Idempotency for relevant create/execute operations.
- Rate limiting where appropriate.
- API documentation.
- Request correlation IDs.

Do not expose administrative endpoints without authorization.

---

# 16. Persistence and data model

Use SQLite for the initial local installation.

Use PostgreSQL as the production relational database.

Implement schema migrations using Alembic.

Design normalized entities for:

- Users.
- Workspaces.
- Agent Definitions.
- Agent Versions.
- Agent Templates.
- Components.
- Tool Definitions.
- Model Profiles.
- Workflow Definitions.
- Executions.
- Execution Events.
- Execution Checkpoints.
- Approvals.
- Evaluation Suites.
- Evaluation Cases.
- Evaluation Runs.
- Audit Events.
- Configuration Metadata.

Add created and updated timestamps to applicable entities.

Use stable identifiers such as UUIDs.

Create database indexes for common access patterns.

Support database migration and backup/restore.

Do not store large binary artifacts directly in relational database tables.

Use a pluggable artifact storage interface with local filesystem storage initially and optional object storage for production.

---

# 17. Security architecture

Security is a mandatory acceptance criterion.

Use threat modeling to identify risks involving:

- Prompt injection.
- Indirect prompt injection.
- Excessive agent autonomy.
- Unauthorized tool execution.
- Sensitive data exposure.
- Credential leakage.
- Insecure output handling.
- Unsafe code execution.
- Server-side request forgery.
- Path traversal.
- SQL injection.
- Cross-workspace access.
- Denial of service and unbounded model consumption.
- Malicious or compromised connectors.
- Supply-chain compromise.
- Audit log tampering.

Use the latest applicable OWASP guidance for LLM and agentic application security as a threat-model reference.

Implement:

### Authentication

For local development, support an explicitly local-only mode with clear documentation.

Do not bind an unauthenticated development server to a public network interface.

For production, support standards-based authentication through OIDC or an equivalent enterprise identity integration.

### Authorization

Implement roles such as:

- Administrator.
- Agent Developer.
- Agent Operator.
- Evaluator.
- Viewer.

Enforce authorization on the backend for every protected resource and action.

### Secrets

- Never commit secrets.
- Never include credentials in agent specifications.
- Never expose credentials to model context.
- Use environment variables for local development only.
- Provide a secure local secret-storage adapter where practical.
- Support external secret managers through adapters in production.
- Redact secrets from logs and error messages.

### Tool security

- Default to read-only tools.
- Enforce capability allowlists.
- Validate inputs and outputs.
- Restrict filesystem access.
- Restrict external network destinations.
- Enforce execution budgets.
- Require approval for sensitive side effects.

### Prompt injection controls

Treat user input, files, retrieved documents, tool output, and external content as untrusted.

Do not depend on prompt instructions alone for access control.

Use explicit permission checks, structured interfaces, and output validation.

### Data protection

Provide:

- Data retention controls.
- Execution history deletion.
- Workspace isolation.
- Sensitive-field redaction.
- Configurable telemetry capture.
- Secure export and deletion procedures.

Document residual risks and limitations rather than claiming prompt injection can be completely eliminated.

---

# 18. Observability and operational monitoring

Integrate OpenTelemetry instrumentation where appropriate.

Capture:

- API request traces.
- Agent execution traces.
- Workflow node execution.
- Model invocation duration.
- Tool call duration.
- Retry count.
- Approval wait time.
- Execution status.
- Resource consumption.
- Errors.

Use structured JSON logs.

Every execution must be traceable using a correlation ID and execution ID.

Create a telemetry abstraction so users can select local logging or connect production telemetry backends.

Never send telemetry externally by default.

Do not record sensitive content unless explicitly enabled and appropriately protected.

Implement health checks for:

- API process.
- Database.
- Model provider.
- Worker runtime.
- Configured connectors.

Separate liveness from readiness checks.

---

# 19. Local Mac developer experience

The application must be easy to install and operate on macOS.

Support Apple Silicon and Intel Macs where dependencies permit.

Do not assume a specific memory configuration.

Provide a preflight utility that checks:

- macOS version.
- CPU architecture.
- Available memory.
- Available disk space.
- Python version.
- uv availability.
- Docker availability.
- Ollama installation and connectivity.
- Configured models.
- Application port availability.

Use these results to provide hardware-appropriate model recommendations.

Do not automatically download large models.

Provide a local installation path that does not require Docker.

Create commands similar to:

```bash
agent-factory doctor
agent-factory init
agent-factory serve
agent-factory status
agent-factory models list
agent-factory agents list
agent-factory agents create
agent-factory agents run
agent-factory evaluate
```

A CLI may be implemented using Typer.

Also provide:

```bash
make install
make dev
make test
make lint
make typecheck
make build
```

Provide a `.env.example`, never a real `.env` with credentials.

Use sensible defaults and descriptive startup errors.

The first successful installation should allow the developer to open the dashboard, inspect model status, create an agent, and execute a working example.

---

# 20. Production deployment architecture

Design a production deployment model without making it mandatory for local development.

### Deployment target A: Local developer installation

- Native Python backend.
- Local React frontend.
- SQLite database.
- Ollama model service.
- Local filesystem storage.
- Local logs.
- Optional local tracing.

### Deployment target B: Single-node production

Use Docker Compose as the initial production packaging option where appropriate.

Potential services:

- Web frontend.
- API/control plane.
- Execution worker.
- PostgreSQL.
- Optional Redis-backed queue.
- Optional vector database.
- Reverse proxy.
- Telemetry collector.

Use separate containers where justified by trust boundaries, resource requirements, or independent scaling.

Do not expose internal database, cache, model, or worker ports publicly.

### Deployment target C: Scaled enterprise deployment

Provide an architecture path for:

- Multiple API instances.
- Horizontally scaled workers.
- Durable workflow persistence.
- PostgreSQL high availability.
- Managed secrets.
- Enterprise identity.
- Centralized telemetry.
- Object storage.
- Load balancing.
- Network segmentation.
- Backup and disaster recovery.

Kubernetes and Helm may be added as a later deployment profile. Do not make them a requirement for the first release.

### Production operational requirements

Implement or document:

- TLS termination.
- Secure ingress.
- Resource requests and limits.
- Persistent volumes.
- Database migrations.
- Database backups.
- Restore procedures.
- Health probes.
- Graceful shutdown.
- Worker recovery.
- Log rotation.
- Secret rotation.
- Upgrade strategy.
- Rollback procedure.
- Retention policies.
- Disaster recovery assumptions.

Use immutable application images.

Keep configuration outside the application image.

Do not use development credentials or debug settings in production.

---

# 21. Testing strategy

Create a comprehensive testing pyramid.

### Unit tests

Test:

- Agent specifications.
- Schema validation.
- Lifecycle transitions.
- Permission evaluation.
- Tool registry.
- Model adapters.
- Policy engine.
- Budget enforcement.
- State transitions.
- Data access boundaries.

### Integration tests

Test:

- API and database integration.
- Ollama adapter using mocked or configured test endpoints.
- LangGraph execution.
- Checkpointing and recovery.
- Tool invocation.
- Approval and resume.
- Evaluation pipeline.
- Agent version registration.

### End-to-end tests

Test:

- Create an agent.
- Validate the specification.
- Build it.
- Execute it.
- Inspect the result.
- Run its evaluation suite.
- Publish a passing version.
- Reject an unauthorized action.
- Cancel an execution.
- Recover from a simulated failure.

### Security tests

Add tests for:

- Invalid authorization.
- Cross-workspace access.
- Prompt injection resistance at the policy boundary.
- Tool allowlist enforcement.
- Path traversal.
- Malicious tool output.
- Untrusted retrieved documents.
- Secret redaction.
- Request size limits.
- Execution budgets.
- Unauthorized network destinations.

### Performance tests

Measure:

- API response times.
- Concurrent execution behavior.
- Model latency.
- Tool call latency.
- Database access.
- Memory consumption.
- Cancellation behavior.

Clearly distinguish test results obtained with deterministic mock models from those requiring real local model inference.

Do not mark tests as passed unless they were actually executed.

---

# 22. Example agents to deliver with the platform

Deliver at least three example agents.

## Example 1: Data Pipeline Diagnostic Agent

Objective:

Analyze pipeline failures and recommend corrective actions.

Capabilities:

- Read approved sample execution logs.
- Inspect example pipeline metadata.
- Identify probable failure causes.
- Generate evidence-based recommendations.

Initially use mock/sample data.

Keep the first release read-only.

Do not connect to real production AWS or database environments by default.

## Example 2: Document Research Agent

Objective:

Analyze local documents and answer questions using retrieved evidence.

Capabilities:

- Ingest files from a designated directory.
- Extract document text.
- Perform retrieval.
- Produce answers with document references.
- Identify unsupported conclusions.

Do not permit unrestricted filesystem scanning.

## Example 3: Multi-Agent Software Review Workflow

Objective:

Review a supplied code artifact through a structured workflow.

Specialists:

- Architecture Reviewer.
- Security Reviewer.
- Code Quality Reviewer.
- Final Review Coordinator.

Use sample repositories and read-only operations.

Do not execute reviewed code on the host.

Each example must include:

- Agent specification.
- Instructions.
- Workflow.
- Tool definitions.
- Sample inputs.
- Expected outputs.
- Evaluation dataset.
- Automated tests.
- Documentation.

---

# 23. Repository structure

Create a clean and maintainable repository.

Use this as a starting structure and refine it based on actual implementation needs.

```text
agent-factory/
|
├── apps/
│   ├── api/
│   ├── web/
│   └── worker/
|
├── packages/
│   ├── core/
│   ├── agent-spec/
│   ├── agent-builder/
│   ├── harness/
│   ├── runtime/
│   ├── orchestration/
│   ├── model-gateway/
│   ├── tool-registry/
│   ├── memory/
│   ├── evaluation/
│   ├── governance/
│   ├── observability/
│   └── storage/
|
├── examples/
│   ├── data-pipeline-diagnostic/
│   ├── document-research/
│   └── software-review/
|
├── templates/
|
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── performance/
|
├── migrations/
|
├── deploy/
│   ├── docker/
│   ├── compose/
│   └── production/
|
├── scripts/
|
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── development/
│   ├── security/
│   ├── deployment/
│   ├── user-guide/
│   └── api/
|
├── .github/
│   ├── workflows/
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
|
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── CHANGELOG.md
├── pyproject.toml
├── uv.lock
├── Makefile
└── docker-compose.yml
```

You may adjust this structure, but document the reason for any major architectural deviation.

Avoid creating empty packages that do not provide useful capabilities.

---

# 24. GitHub and open-source engineering standards

Prepare the repository for public contribution and wider adoption.

Include:

### Repository documentation

- README with product vision.
- Feature overview.
- Architecture diagram.
- Technology stack.
- Prerequisites.
- Local installation.
- Quick-start guide.
- Configuration reference.
- Example agent walkthrough.
- Model provider configuration.
- Agent specification documentation.
- API documentation.
- Security model.
- Production deployment guide.
- Troubleshooting.
- Contribution guide.
- Roadmap.

### Engineering quality

Configure:

- Ruff formatting and linting.
- Python type checking.
- Pytest.
- Frontend linting.
- Frontend type checking.
- Frontend unit tests.
- API integration tests.
- Pre-commit hooks.
- Dependency lockfiles.
- Database migrations.
- Architecture decision records.

### GitHub Actions

Create workflows for:

1. Pull request validation.
2. Python linting and tests.
3. Type checking.
4. Frontend build and tests.
5. Integration tests.
6. Dependency and vulnerability scanning.
7. Container image build.
8. Release packaging.

Use least-privilege workflow permissions.

Pin third-party GitHub Actions to reviewed immutable commit SHAs where appropriate.

Never place credentials in workflow files.

Use GitHub Secrets for required automation credentials.

Do not give ordinary test workflows repository write permissions.

### Open-source readiness

Include:

- A suitable open-source license. Prefer Apache-2.0 unless a project-specific reason supports another choice.
- SECURITY.md with responsible vulnerability reporting instructions.
- CODE_OF_CONDUCT.md.
- CONTRIBUTING.md.
- Issue templates.
- Pull request templates.
- Release notes.
- Semantic versioning.
- Clear support boundaries.

Add a dependency inventory or SBOM generation step where feasible.

Do not add unverified claims such as SOC 2 certified, enterprise certified, or production proven.

---

# 25. Implementation roadmap

Work in the following phases.

## Phase 0 — Discovery and architecture

Inspect the existing workspace before making changes.

Determine:

- Whether a repository already exists.
- Current directory structure.
- macOS and Python environment.
- CPU architecture and available memory.
- Available disk space.
- Docker availability.
- Ollama availability.

Do not overwrite an existing project without understanding it.

Create:

- Product requirements document.
- Architecture overview.
- Component diagram.
- Data model.
- Threat model.
- Architecture decision records.
- Implementation backlog.

Document assumptions and unresolved decisions.

## Phase 1 — Platform foundation

Implement:

- Project structure.
- Backend.
- Frontend.
- Configuration management.
- Logging.
- Database.
- Migrations.
- Health checks.
- Model gateway interface.
- Ollama adapter.
- Basic dashboard.

Acceptance criteria:

The platform starts locally, the backend and frontend communicate, database migrations run, and model availability is visible.

## Phase 2 — Agent Factory core

Implement:

- Agent specification.
- Schema validation.
- Agent registry.
- Versioning.
- Template registry.
- Component registry.
- Agent builder.
- Agent creation APIs.
- Agent catalog UI.

Acceptance criteria:

A user can create, validate, save, inspect, and version an agent.

## Phase 3 — Harness and runtime

Implement:

- Harness interface.
- Context management.
- Model invocation.
- Tool registry.
- Tool authorization.
- Execution lifecycle.
- Workflow orchestration.
- State persistence.
- Checkpointing.
- Error recovery.
- Execution budgets.

Acceptance criteria:

A configured agent can successfully execute a task through the runtime with observable execution events.

## Phase 4 — Governance and human approval

Implement:

- Policy engine.
- RBAC foundation.
- Approval workflow.
- Audit events.
- Tool permission enforcement.
- Secrets handling.
- Safe defaults.

Acceptance criteria:

An unauthorized tool call is rejected, and a workflow requiring approval pauses and resumes only after an authorized decision.

## Phase 5 — Evaluation and observability

Implement:

- Evaluation framework.
- Test datasets.
- Regression testing.
- Execution traces.
- Metrics.
- Evaluation dashboard.
- Agent version comparisons.

Acceptance criteria:

A user can run a repeatable evaluation suite and inspect pass/fail results and execution evidence.

## Phase 6 — Example agents and developer experience

Implement all three example agents.

Add the CLI.

Add setup and health diagnostics.

Provide sample configurations and end-to-end walkthroughs.

Acceptance criteria:

A new developer can install and operate the platform using the published documentation.

## Phase 7 — Production packaging

Implement:

- Dockerfiles.
- Docker Compose configuration.
- Production configuration profile.
- PostgreSQL support.
- Persistent storage.
- Worker process.
- Health and readiness checks.
- Secure environment configuration.
- Backup and restore instructions.
- Deployment documentation.

Acceptance criteria:

The platform can be deployed on a clean Linux host using the documented procedure, without requiring manual code modifications.

## Phase 8 — GitHub release readiness

Implement and validate:

- CI workflows.
- Dependency scanning.
- Test automation.
- Documentation checks.
- Release process.
- License.
- Contribution policies.
- Security reporting guidance.

Acceptance criteria:

A clean checkout can be installed, tested, and built using documented commands.

---

# 26. Non-functional requirements

Apply these targets as engineering objectives, not unsupported performance promises.

**Reliability:** Failed model calls, tool failures, and interrupted executions must produce controlled outcomes.

**Maintainability:** Modules must have clear responsibilities and documented interfaces.

**Extensibility:** A new model provider or tool connector must not require changes throughout the application.

**Portability:** Local development must support macOS, with production containers targeting Linux.

**Security:** Tools must operate under explicit authorization and resource limits.

**Performance:** Avoid unnecessary model calls, excessive context, synchronous blocking of API requests, and unbounded agent loops.

**Scalability:** Use asynchronous execution and separate workers where workloads justify them.

**Usability:** A developer should be able to understand and execute a sample agent without reading the entire source code.

**Reproducibility:** Agent versions and evaluation configurations must be identifiable and repeatable.

---

# 27. Definition of Done

The project is not complete merely because the code compiles.

The first usable release must satisfy all of the following:

1. A developer can install the project on a Mac using documented instructions.
2. The application starts without requiring cloud infrastructure.
3. A local model provider can be configured and tested.
4. A developer can create an agent from a natural-language requirement.
5. The resulting agent specification can be inspected and edited.
6. The agent can be validated and registered.
7. An agent can execute through the harness.
8. An agent can use an authorized tool.
9. An unauthorized action is rejected.
10. Agent executions are persisted and observable.
11. A workflow can pause for approval and resume.
12. An agent can be evaluated using repeatable tests.
13. Agent versions can be compared and rolled back.
14. At least three sample agents are available.
15. The application has a usable dashboard.
16. Backend and frontend automated tests execute successfully.
17. Docker-based production deployment is documented and validated.
18. GitHub CI workflows are configured.
19. Security and contribution documentation is complete.
20. A fresh developer can reproduce the setup.

Every acceptance criterion must be associated with actual test evidence or a clearly documented outstanding limitation.

---

# 28. Engineering execution instructions

Follow these instructions throughout development.

### First: Understand the environment

Inspect the current repository and the available development environment.

Do not make assumptions about hardware, installed tools, existing code, or available model resources.

### Second: Make and document decisions

Create architecture decision records for meaningful decisions.

Where several technologies are viable, compare them briefly and choose the simplest architecture that meets the requirements.

Do not over-engineer.

### Third: Implement vertically

Build working end-to-end functionality in small increments.

Prioritize the first complete journey:

Create Agent → Validate → Build → Execute → Observe → Evaluate.

Avoid creating dozens of disconnected modules before the first journey works.

### Fourth: Test continuously

After every meaningful implementation increment:

- Run relevant tests.
- Run linting.
- Run type checking.
- Fix failures.
- Update documentation.
- Verify the user experience.

Do not suppress test failures or skip checks merely to claim completion.

### Fifth: Review security continuously

Before declaring a feature complete, review the associated permissions, untrusted input handling, secret exposure, error handling, and execution boundaries.

### Sixth: Maintain documentation

Update architecture diagrams, API definitions, configuration examples, troubleshooting instructions, and decisions as the implementation changes.

### Seventh: Protect the repository

Do not:

- Commit secrets.
- Commit local databases or private execution logs.
- Commit downloaded model files.
- Commit user-specific configuration.
- Overwrite user work.
- Silently change licenses.
- Push directly to an external repository without explicit authorization.

Prepare the project for GitHub publication, but leave the final publication or push action to the repository owner unless explicitly authorized.

### Eighth: Be transparent

Never report a feature as implemented if it is only designed.

Never report tests as passed if they have not been executed.

Clearly classify each requirement as implemented, partially implemented, not implemented, or blocked.

Do not call the platform production-ready merely because a Docker image builds. Production readiness requires the documented operational, security, recovery, and deployment controls to be implemented and validated.

---

# 29. Final deliverables

At the conclusion of implementation, provide:

1. Executive architecture summary.
2. System architecture diagram.
3. Repository structure.
4. Technology decisions and rationale.
5. Local installation guide.
6. Local startup instructions.
7. Sample agent walkthroughs.
8. API documentation.
9. Agent specification reference.
10. Security and governance documentation.
11. Testing results.
12. Production deployment instructions.
13. Backup and recovery instructions.
14. GitHub CI/CD configuration.
15. Known limitations.
16. Future roadmap.
17. Commands to initialize Git, commit, configure the remote, and publish the repository.

Also provide a final implementation status table:

| Area | Status | Evidence |
|---|---|---|
| Local setup | | |
| Model gateway | | |
| Agent builder | | |
| AI harness | | |
| Multi-agent runtime | | |
| Tool framework | | |
| Governance | | |
| Evaluation | | |
| Observability | | |
| UI | | |
| Production packaging | | |
| Automated tests | | |
| GitHub readiness | | |

---

# 30. Final engineering directive

Approach this project as a platform that other engineers will depend on.

Prioritize architecture, simplicity, reproducibility, security, extensibility, and developer experience.

Do not optimize for the number of agents or frameworks used.

Optimize for the quality of the agent engineering lifecycle.

The platform should make it possible to create an agent once, test it systematically, understand how it behaves, control what it can do, deploy it safely, and improve it without losing reproducibility.

**Build the foundation first. Prove the complete lifecycle locally. Then make the platform production-deployable and open-source ready.**

Begin with Phase 0, produce the architecture and implementation backlog, and then proceed to implementation without stopping at planning.
