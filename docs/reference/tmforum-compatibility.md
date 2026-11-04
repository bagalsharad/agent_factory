# TM Forum compatibility assessment (preliminary)

Scope: how the `agentfactory/v1` specification relates to TM Forum's agent-specification work. This is a
**preliminary** assessment written from public information only. The full TM Forum documents are available to
members; the field-level mapping below must be checked against them before anyone relies on it.

## What is publicly known (as of 2026-11-03)

| Document | Public information | Source |
|---|---|---|
| IG1412 AI Agent Specification Template | Introductory guide from the AI Closed Loop Automation project: "a standardized template for specifying the design and functionality of Enterprise Software AI Agents". Versions 1.0.0 (alpha), 1.1.0 (general availability) and 1.2.0 (team approved) are listed. | [v1.1.0](https://www.tmforum.org/resources/guidebook/ig1412-ai-agent-specification-template-v1-1-0), [v1.2.0](https://www.tmforum.org/resources/guidebook/ig1412-ai-agent-specification-template-v1-2-0) |
| TMF443 AI Agent Profile, TMF444 Agent Deployment | No public description found; scope and resources not confirmed. | – |
| Related | TM Forum catalysts on governed agentic AI over ODA components (e.g. C26.0.941) and an A2A telecom runtime (C26.0.910, IG1453). | [C26.0.941](https://www.tmforum.org/catalysts/projects/C26.0.941), [C26.0.910](https://www.tmforum.org/catalysts/projects/C26.0.910) |

## Concepts an agent specification template normally covers, and where they live here

This table maps *concept areas*, not TM Forum field names (which could not be verified).

| Concept area | `agentfactory/v1` | Gap |
|---|---|---|
| Identity, purpose, version | `metadata.name`, `display_name`, `description`, `version`; `spec.objective` | owner/business context is operational data (`agents.business_owner`, contacts), not in the spec |
| Capabilities and tools | `spec.tools` (with pinned versions), component refs, MCP tools | no capability taxonomy |
| Inputs and outputs | `spec.inputs`, `spec.outputs` (JSON Schema) | – |
| Behaviour / workflow | `spec.workflow` (single, graph, sequential, supervisor), `instructions` | – |
| Models | `spec.model` (provider, model, profile, limits) | – |
| Autonomy, guardrails, human oversight | `policies`, `human_approval`, `execution_limits` | no explicit autonomy-level field |
| Data handling | `policies.data_classification`, `capture_content`, memory settings | – |
| Quality and evaluation | `evaluation` (cases, acceptance gates, judge, security suite) | – |
| Lifecycle and deployment (TMF444-like) | version lifecycle, artifacts, environments, deployments (`/api/v1/agents/{id}/deployments`) | not exposed as a TM Forum Open API |
| Profile / catalogue entry (TMF443-like) | agent summary API, catalog UI | no standard profile resource |

## Recommendation

1. Obtain IG1412 v1.2.0 and the TMF443/TMF444 definitions and replace the concept table with a field-level
   mapping.
2. Add an export (`agentfactory/v1` → TM Forum template) rather than changing the internal model; keep internal
   fields that have no counterpart.
3. If TMF444 defines a deployment resource, expose a thin adapter over the existing release API.
