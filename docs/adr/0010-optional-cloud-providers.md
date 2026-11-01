# ADR-0010: Optional cloud providers

Status: accepted

Cloud adapters (Claude API via the official `anthropic` SDK; any non-local OpenAI-compatible endpoint) are enabled
only when `AF_CLOUD_PROVIDERS_ENABLED=true` and credentials are configured. The build report warns when an agent
uses a cloud provider. The Claude adapter never sends sampling parameters, uses `output_config.format` for
structured output, replays provider content blocks unchanged, treats `refusal` as a non-retryable error, and enables
server-side refusal fallbacks by default (`AF_ANTHROPIC_FALLBACKS=off` disables them).
