# Security Policy

## Supported Versions

Only the latest release and the current default branch receive security fixes.

## Reporting a Vulnerability

Please **do not** open a public issue for security problems. Report privately via
[GitHub Security Advisories](https://github.com/Bonham-Technologies/coglossa/security/advisories/new).

Include reproduction steps and the version you tested. You should hear back
within a few business days; please allow a reasonable window for a fix before
public disclosure.

## Scope notes

In scope: the engine, the API, the web app, the scorer, the container images, the compose files and the offline (air-gapped) bundle, and the hosted preview's code under deploy/hosted. Out of scope: the customer's own model servers and infrastructure, third-party model providers, and the development-only credentials in deploy/compose.dev.yaml.
