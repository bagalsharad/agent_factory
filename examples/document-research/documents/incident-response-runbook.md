# Data Incident Response Runbook (Sample)

1. Acknowledge the alert and open an incident channel.
2. Identify the failing stage from the execution log before taking any action.
3. For schema mismatch failures, coordinate with the upstream producer and apply a schema-evolution change.
4. For credential failures, rotate the credential through the secret manager; never paste secrets into tickets.
5. Restarts of production pipelines are performed only after the root cause is understood and approved.
6. Close the incident with a written summary of root cause, impact and follow-up actions.
