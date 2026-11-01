# Security policy

## Reporting a vulnerability

Please **do not open public issues** for security problems. Report privately via GitHub Security Advisories
("Report a vulnerability" on the repository's Security tab) or by email to the maintainers listed in the
repository profile. Include affected version, reproduction steps and impact. We aim to acknowledge within
3 business days and to agree on a disclosure timeline with you.

## Supported versions

Agent Factory is pre-1.0. Security fixes are released for the latest minor version only.

## Scope

In scope: the API, dashboard, harness, policy engine, approvals, tools shipped in this repository, packaging.
Out of scope: vulnerabilities in model outputs themselves (models can be manipulated — see the threat model),
third-party model providers, and deployments that disable documented safeguards (e.g. binding local mode to a
public interface by patching the startup checks).

## Security design

See [docs/security/security-model.md](docs/security/security-model.md) and
[docs/security/threat-model.md](docs/security/threat-model.md).
