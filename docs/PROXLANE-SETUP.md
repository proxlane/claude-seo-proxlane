# Proxlane setup, in detail

## Where things live

| What | Where |
|---|---|
| The skill | `~/.claude/skills/seo-proxlane/SKILL.md` |
| The script | `~/.claude/skills/seo-proxlane/proxlane_fetch.py` |
| Gateway URL and key | `~/.config/claude-seo/proxlane.json` |
| Today's paid fetch count | `~/.config/claude-seo/proxlane-usage.json` |

`PROXLANE_URL` and `PROXLANE_API_KEY` in the environment override the file, which is the
right choice on a shared machine: nothing is written to disk at all.

## Configuring without the installer

```bash
read -rs PROXLANE_CONFIGURE_KEY && export PROXLANE_CONFIGURE_KEY
python3 ~/.claude/skills/seo-proxlane/proxlane_fetch.py configure --url http://localhost:8787
unset PROXLANE_CONFIGURE_KEY
```

The key comes from the environment deliberately. On a command line it would be visible to
every user on the machine through the process list, and typed inline it would land in your
shell history. `read -rs` keeps it out of both.

## Why the URL and key travel together

`PROXLANE_URL` and `PROXLANE_API_KEY` override the file only as a pair. Setting the URL on its
own is refused rather than completed from the file, because that would let one environment
variable send the stored key to any address.

## The daily cap

100 paid fetches a day by default, counted in `~/.config/claude-seo/proxlane-usage.json`
before each request, so a refused fetch spends nothing. `check` and `--simulate` are never
counted. Set `PROXLANE_DAILY_FETCH_LIMIT` to change it; `0` allows no paid fetches at all. The
count is approximate when fetches run in parallel. It exists to stop a runaway loop, and the
gateway reports the real spend on every response.

## Proxies

Proxy settings from the environment (`http_proxy`, `https_proxy`) are ignored on purpose. With
them honoured, the default `http://localhost:8787` gateway sent the key in cleartext to the
configured proxy, because those settings do not exempt localhost. Reach a remote gateway
directly, over HTTPS.

## The check

`check` makes two requests and spends nothing:

1. `GET /health`, which needs no key and returns the gateway's version and how many of its
   providers are usable
2. `GET /v1` with the key and no target. The gateway authenticates before it validates, so a
   good key gets `400 url is required` and a bad one gets `401`. No provider is called

## Common problems

**`cannot reach http://localhost:8787`.** The gateway is not running, or is on another port.
`docker ps` shows whether the container is up.

**`rejected the key`.** The key in the config is not the gateway's `PROXLANE_API_KEY`. Re-run
the installer. A provider's key goes on the gateway, not here.

**`0 of 0 provider(s) usable`.** The gateway has no provider keys. Every fetch will return
`NO_PROVIDER_AVAILABLE` until one is set, for example `SCRAPERAPI_KEY`.

**`NOT_A_GATEWAY_RESPONSE`, or `refusing to follow a redirect`.** Something answered at that
URL, but not a Proxlane gateway. Often a reverse proxy redirecting HTTP to HTTPS: use the
`https://` URL directly.

**Every page comes back `blocked`.** The target defeats every provider you have configured at
its current tier. Try `--render`, then `--premium residential` if your plan includes it. Some
sites cannot be fetched by any provider, and reporting that is the honest result.

## Running the gateway somewhere other than this machine

Point `PROXLANE_URL` at it, over HTTPS. The key crosses the network on every request, so a
plain `http://` URL is only appropriate for `localhost` or a private network you trust.
