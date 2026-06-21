# Security Policy

Thanks for helping keep **100xprism** and its users safe.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Report privately via **[GitHub Security Advisories](https://github.com/rajitsaha/100xprism/security/advisories/new)** (the "Report a vulnerability" button on the [Security tab](https://github.com/rajitsaha/100xprism/security)). This keeps the report confidential until a fix is ready.

When reporting, please include:

- A clear description of the issue and its impact
- Steps to reproduce (commands, config, environment)
- Affected version(s) — `100xprism --version` or commit SHA
- Any proof-of-concept code or screenshots (optional but helpful)

## What to Expect

| Stage | Timeline |
|---|---|
| Acknowledgement of your report | Within **5 business days** |
| Initial assessment + severity triage | Within **10 business days** |
| Fix timeline | Communicated after triage — depends on severity and complexity |
| Public advisory + CVE (if applicable) | Published once a fix ships, with credit to the reporter unless you request anonymity |

This project is maintained by a solo author, so timelines are best-effort. Critical issues (RCE, supply-chain compromise, credential exposure) are prioritized over lower-severity ones.

## Supported Versions

Security fixes are applied to the **latest minor release** on the `main` branch. Older versions are not patched — please upgrade to receive fixes:

```bash
100xprism update
```

## Scope

**In scope:**

- Code in this repository: shell scripts (`get.sh`, `install.sh`, `update.sh`, `shell/*.sh`), the Node CLI (`bin/`, `lib/`), adapters, templates, and generated artifacts
- The published npm package [`100xprism`](https://www.npmjs.com/package/100xprism)
- The install one-liner served from `raw.githubusercontent.com/rajitsaha/100xprism/main/get.sh`

**Out of scope:**

- Vulnerabilities in **upstream tools** that 100xprism integrates with (Claude Code, Cursor, Codex, Windsurf, Copilot, Gemini, Antigravity, npm, GitHub Actions). Report those to the respective vendors.
- Vulnerabilities in **your project's** dependencies or generated CI workflows after `100xprism init` runs — these are your project's responsibility.
- Issues that require an attacker to already have write access to your machine, your `.env`, or your shell config.
- Social-engineering or phishing scenarios that don't involve a flaw in this codebase.

## Examples of In-Scope Issues

- Command injection in shell scripts or template substitution
- Path traversal when writing to user config (`~/.claude/`, `.cursor/rules/`, etc.)
- Credential or secret leakage from `/connect`, `.env` handling, or generated artifacts
- Supply-chain risks: tampered npm package, compromised `get.sh`, or unsafe `curl | bash` patterns
- Unsafe defaults that expose user systems (overly permissive file modes, unvalidated downloads)

## Coordinated Disclosure

We follow standard coordinated-disclosure practice: please give us a reasonable window to ship a fix before publishing details. We'll keep you informed of progress and credit you in the advisory unless you prefer otherwise.

Thank you for reporting responsibly.
