# Security

## Reporting a vulnerability

**Do not open a public issue.**

- [**Private vulnerability reporting**](https://github.com/proxlane/claude-seo-proxlane/security/advisories/new). Preferred.
- **security@proxlane.dev** if you would rather use email.

This repository follows [Proxlane's disclosure process](https://github.com/proxlane/proxlane/blob/main/SECURITY.md): a human reply within 72 hours, a fix or a written reason within 90 days, and a public advisory when the fix ships.

## Scope

This integration holds one secret, the key to your Proxlane gateway, and hands third-party page content to an AI agent. Reports touching either are read first:

- the gateway key reaching anything but the gateway it was stored with, a command line, a URL, a log, or a file readable by another user
- a fetched page causing a write outside the working directory, a command to run, or a fetch the user did not ask for
- the daily fetch brake counting wrongly. It is a guard against runaway loops, not a security boundary: raising `PROXLANE_DAILY_FETCH_LIMIT` is a supported setting, not a bypass

Vulnerabilities in the gateway itself belong to [proxlane/proxlane](https://github.com/proxlane/proxlane/security/advisories/new). Vulnerabilities in Claude SEO belong to [its maintainers](https://github.com/AgriciDaniel/claude-seo).
