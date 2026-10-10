# Security

Scout is an internal tool. It reads customer conversations, survey answers, and reviews. Treat the repository and its issues as public.

## Reporting a vulnerability

Contact Yankho privately. Do not open a public GitHub issue, pull request, or Slack message that includes the vulnerability details, a secret, or customer data. Yankho can update this file with a preferred email address.

## Supported branch

`main` is the supported branch. Fixes land there.

## Secrets

- Credentials live in environment variables, never in tracked files.
- Production loads them from `/etc/scout.env` on the VM (mode 600).
- Local development uses a `.env` file, which is gitignored. Start from `.env.example` and fill values only on your machine.
- Do not commit API keys, OAuth secrets, private keys, Slack tokens, account identifiers, or key-file paths.

## If a secret leaks

1. Rotate the credential first.
2. Then purge it from git history and force every clone to start fresh.
3. Do not open a pull request that still contains the secret in order to "remove" it. History rewrite has to drop the blob.

## Customer data

Do not paste customer messages, emails, order details, or other personal data into issues, pull requests, commits, or logs.

## Dependencies

Runtime pins are in `requirements.txt`. Change them in a pull request against `main` and run the test suite before merging.
