# Proxlane for Claude SEO

A community integration for [Claude SEO](https://github.com/AgriciDaniel/claude-seo) that fetches pages through your own [Proxlane](https://github.com/proxlane/proxlane) gateway.

Proxlane sits in front of the scraping APIs you already pay for: ScraperAPI, ScrapingBee, Scrapfly, Bright Data and Firecrawl. When one is blocked or times out it fails over to the next, and it checks every page for a block before calling it a success. This integration gives Claude SEO a fetch that says *why* a page failed, so an audit can tell a broken page from a blocked one.

Maintained here, not by Claude SEO. Report problems in [this repository's issues](https://github.com/proxlane/claude-seo-proxlane/issues).

## Prerequisites

- [Claude SEO](https://github.com/AgriciDaniel/claude-seo). Optional: the skill works on its own, and cooperates with Claude SEO's audit skills when both are installed
- Python 3.9+
- A running Proxlane gateway and its key. One container, your own provider keys:

```bash
export PROXLANE_API_KEY=$(openssl rand -hex 32)
export SCRAPERAPI_KEY=...
docker run -p 8787:8787 -e PROXLANE_API_KEY -e SCRAPERAPI_KEY ghcr.io/proxlane/gateway:0.19.2
```

Keep that shell open: the installer below picks `PROXLANE_API_KEY` up from it instead of prompting.

`-e NAME` with no value passes the variable through, so the key is not on `docker`'s command line.

The gateway's own [quickstart](https://proxlane.dev/docs/quickstart) covers the rest. To try this with no provider account, see [the sandbox](#trying-it-without-a-provider-account).

## Installation

### macOS / Linux

```bash
git clone https://github.com/proxlane/claude-seo-proxlane
cd claude-seo-proxlane
./install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/proxlane/claude-seo-proxlane
cd claude-seo-proxlane
.\install.ps1
```

The installer takes the gateway URL and key from `PROXLANE_URL` and `PROXLANE_API_KEY` when they are set, and asks for them when they are not. It writes them to `~/.config/claude-seo/proxlane.json`, readable only by you, then runs a check that spends nothing.

The key it asks for is the gateway's, the `PROXLANE_API_KEY` you started it with. Your ScraperAPI or Firecrawl keys stay on the gateway and never reach this machine's Claude configuration.

## Commands

| Command | Purpose | Cost |
|---|---|---|
| `/seo proxlane check` | Gateway reachable, key accepted | Free |
| `/seo proxlane fetch <url>` | One page | One provider request, more on failover |
| `/seo proxlane fetch <url> --render` | One page with JavaScript executed | Usually 5-25x a plain request |
| `/seo proxlane fetch <url> --country de` | As if requested from Germany | Provider-dependent |

Every fetch reports what it cost, including attempts that failed before one succeeded. The skill tells the agent to ask before the first paid fetch, before any batch of more than five, and before every `--render`, `--premium` or `--country`.

## What you get back

The page on stdout, and one line on stderr saying what happened:

```
proxlane: OK (ok) via scrapfly, 2 attempt(s), cost 6.000000 provider-credits
proxlane: SOFT_BLOCK (blocked) via scrapingbee, 3 attempt(s), cost 3.000000 provider-credits, block rule cloudflare-challenge
proxlane: TARGET_NOT_FOUND (target) via scraperapi, 1 attempt(s), cost 1.000000 provider-credits
```

A blocked page never reaches stdout, so nothing downstream analyses a challenge page as though it were the site. The exit code follows the outcome class: `0` ok, `3` the site said no, `4` blocked, `5` transient, `2` fix the request.

## Integration with Claude SEO

- **`/seo audit`**: pages a direct fetch could not get are re-fetched through the gateway, and pages blocked outright are reported as a finding, since a site that blocks scrapers may be blocking crawlers too
- **`/seo technical`**: real status codes from the site are kept apart from blocks, so a blocked page is never reported as broken
- **`/seo content`**, **`/seo schema`**: only pages that came back `ok` are analysed

## Trying it without a provider account

Start the gateway with a sandbox key as well:

```bash
export PROXLANE_API_KEY=$(openssl rand -hex 32)
export PROXLANE_SANDBOX_KEY=$(openssl rand -hex 32)
docker run -p 8787:8787 -e PROXLANE_API_KEY -e PROXLANE_SANDBOX_KEY ghcr.io/proxlane/gateway:0.19.2
```

Install with the **sandbox** key. It has to be named explicitly, because an exported `PROXLANE_API_KEY` would otherwise be picked up instead:

```bash
PROXLANE_API_KEY="$PROXLANE_SANDBOX_KEY" ./install.sh
```

Then any fetch can take `--simulate <OUTCOME>`, for example `--simulate SOFT_BLOCK`, and returns exactly what that outcome looks like without calling a provider. Every simulated verdict says `SIMULATED`.

## Security

- **The key only ever goes to the gateway it was stored with.** The URL and key are read as a pair, from the environment or from the file, never one from each, so setting `PROXLANE_URL` alone cannot send the stored key elsewhere
- **Stored readable by you only.** `~/.config/claude-seo/proxlane.json` is mode `0600` on macOS and Linux, written atomically. On Windows it is restricted to your account with `icacls`, as a best effort, since NTFS ignores mode bits
- **Never on a command line**, including during install, and never in a URL: it goes in an `Authorization` header
- **Only to the gateway.** Redirects are refused, since a gateway never redirects, and proxies from the environment are ignored, so an `http_proxy` setting never sees the key
- **Pages cannot write where they like.** `--output` writes only `.html`, `.htm`, `.xml` or `.txt` files inside the working directory, never a dotfile, an agent instruction file or a build manifest, and will not replace a file without `--force`
- **A daily brake** of 100 paid fetches, counted by the script, so a runaway loop stops even if the agent does not. It is a brake rather than a wall: anyone who can set `PROXLANE_DAILY_FETCH_LIMIT` can raise it, and the skill tells the agent that only the user may
- **Private, loopback and cloud-metadata targets** are refused by the gateway itself, at its edge

Report a vulnerability through [SECURITY.md](SECURITY.md), not a public issue.

## Troubleshooting

Run the check. It says what is wrong.

```bash
python3 ~/.claude/skills/seo-proxlane/proxlane_fetch.py check
```

More in [docs/PROXLANE-SETUP.md](docs/PROXLANE-SETUP.md).

## Uninstall

```bash
./uninstall.sh      # macOS / Linux
.\uninstall.ps1     # Windows
```

Removes the skill and the config file. Claude SEO and the gateway are untouched.

## Licence

Apache-2.0. Disclosure: written by Proxlane's maintainer.
