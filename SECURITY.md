# Security Policy

## Reporting a vulnerability

Do not disclose passwords, database files, webhook URLs, personal information or
production server details in a public issue.

Please use the repository's private GitHub security advisory feature to report a
security vulnerability. Include the affected version, reproduction steps and
the expected impact. Do not include real account credentials or trading data.

## Secrets

The repository must never contain:

- `.env` or `.streamlit/secrets.toml`
- MySQL passwords, administrator passwords or webhook tokens
- SQLite production databases, exports, backups or application logs
- TLS private keys or brokerage credentials

If a secret is committed, revoke or rotate it immediately. Removing it only from
the latest commit does not remove it from Git history.

## Supported use

This project is a research and decision-support tool. Do not expose a development
instance directly to the public internet. Production deployments should use
member authentication, HTTPS, least-privilege database accounts, backups and
the release checks included in this repository.
