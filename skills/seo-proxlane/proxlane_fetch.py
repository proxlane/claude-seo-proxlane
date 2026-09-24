#!/usr/bin/env python3
"""Fetch a page through a self-hosted Proxlane gateway, and say what happened.

Proxlane sits in front of the scraping APIs you already pay for (ScraperAPI, ScrapingBee,
Scrapfly, Bright Data, Firecrawl). It fails over when one is blocked or times out, and checks
every body for a block page before calling it a success. This script is the claude-seo side of
that: it asks the gateway for one page and reports the gateway's verdict.

    proxlane_fetch.py check
    proxlane_fetch.py configure --url http://localhost:8787   (key from PROXLANE_CONFIGURE_KEY)
    proxlane_fetch.py fetch https://example.com
    proxlane_fetch.py fetch https://example.com --render --json
    proxlane_fetch.py fetch https://example.com --output page.html

The page goes to stdout (or --output). The verdict goes to stderr, always, so a caller that
only reads the body still sees "blocked" rather than mistaking a challenge page for content.
With --json, both go to stdout as one object.

Standard library only, so installing it adds nothing to claude-seo's dependencies.

Exit codes, by the gateway's outcome class. The class is a closed set of six and does not grow
when the gateway learns a new outcome, which is why this branches on it and not on the outcome.

    0  ok        you have the page
    3  target    the site itself answered no (404, 410, a 5xx of its own). Do not retry
    4  blocked   every provider was blocked. Retrying immediately will not help
    5  provider  transient provider failure. Safe to retry later
    5  gateway   the gateway could not serve it (busy, no provider, deadline). Retry later
    2  client    the request or the configuration is wrong. Fix it, do not retry
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".config" / "claude-seo" / "proxlane.json"
USAGE_PATH = Path.home() / ".config" / "claude-seo" / "proxlane-usage.json"
DEFAULT_URL = "http://localhost:8787"

EXIT_OK = 0
EXIT_CLIENT = 2
EXIT_TARGET = 3
EXIT_BLOCKED = 4
EXIT_RETRY = 5

EXIT_FOR_CLASS = {
    "ok": EXIT_OK,
    "target": EXIT_TARGET,
    "blocked": EXIT_BLOCKED,
    "provider": EXIT_RETRY,
    "gateway": EXIT_RETRY,
    "client": EXIT_CLIENT,
}

# Header name on the wire -> key in the verdict. Same set, same names, as scrapy-proxlane.
VERDICT_HEADERS = {
    "X-Outcome": "outcome",
    "X-Outcome-Class": "outcome_class",
    "X-Provider-Used": "provider",
    "X-Attempts": "attempts",
    "X-Chain": "chain",
    "X-Cost-Estimate": "cost",
    "X-Cost-Unit": "cost_unit",
    "X-Cost-Source": "cost_source",
    "X-Detect-Rule": "detect_rule",
    "X-Ignored-Params": "ignored_params",
    "X-Request-Id": "request_id",
    "X-Proxlane-Simulated": "simulated",
}

# A HARD CAP on paid fetches per day, enforced here rather than requested in SKILL.md. The
# skill's instructions are advice to an agent, and a hostile page it has just read can argue
# against advice. It cannot argue against a counter. Raise it with PROXLANE_DAILY_FETCH_LIMIT.
DEFAULT_DAILY_FETCH_LIMIT = 100

# Far above any page an SEO audit wants, and far below what would exhaust memory. The gateway
# has its own cap; this stops a misconfigured one from filling the machine.
MAX_BODY_BYTES = 64 * 1024 * 1024


class ConfigError(Exception):
    """Something the user has to fix before any request can succeed."""


@dataclass(frozen=True)
class Config:
    url: str
    api_key: str


def load_config(env: dict[str, str] | None = None, path: Path | None = None) -> Config:
    """The gateway URL and key, TOGETHER, from the environment or from the installer's file.

    NEVER ONE FROM EACH. An earlier version took each value from whichever source had it, so
    setting `PROXLANE_URL` alone paired an attacker's address with the key stored in the file:
    one environment variable, which a hostile page can ask an agent to set, sent the key
    anywhere. The pair now comes from one place. `PROXLANE_URL` without `PROXLANE_API_KEY` is
    refused rather than completed from the file.

    The environment wins when it has both, so a shared machine or a CI job never needs the file.

    `path` defaults at CALL time, never in the signature. A default bound at definition time
    cannot be redirected by a test, and the first version of this file's tests wrote a dummy
    key into the real ~/.config/claude-seo that way.
    """
    env = os.environ if env is None else env
    path = CONFIG_PATH if path is None else path

    env_url, env_key = env.get("PROXLANE_URL", ""), env.get("PROXLANE_API_KEY", "")
    if env_url and not env_key:
        raise ConfigError(
            "PROXLANE_URL is set but PROXLANE_API_KEY is not. The stored key is only ever used "
            "with the stored URL, so set both or neither."
        )
    if env_key:
        url, key = env_url or DEFAULT_URL, env_key
    elif path.exists():
        try:
            stored = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path} is not readable JSON: {exc}") from exc
        url, key = stored.get("url") or DEFAULT_URL, stored.get("api_key") or ""
    else:
        url, key = DEFAULT_URL, ""

    url = _validate_gateway_url(url)
    key = _validate_key(key)
    return Config(url=url, api_key=key)


def _validate_key(key: str) -> str:
    """Strip surrounding whitespace and refuse anything that could not be sent as a header.

    A key with a CR or LF in it made urllib raise with the whole header, key included, in the
    message, and that message went to stderr, which is the stream the agent reads.
    """
    key = (key or "").strip()
    if not key:
        raise ConfigError(
            "no gateway key. Run install.sh, or set PROXLANE_API_KEY. "
            "This is the key your gateway was started with, not a provider's key."
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in key):
        raise ConfigError("the gateway key contains a control character; re-enter it")
    return key


def _validate_gateway_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"gateway URL must be http(s)://host[:port], got {url!r}")
    return url.rstrip("/")


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _warn_if_plain_http(url: str) -> None:
    """The key crosses the network on every request. Over plain HTTP that is only sensible
    when the gateway is on this machine."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "http" and (parsed.hostname or "") not in _LOOPBACK:
        print(
            f"warning: {url} is plain HTTP to another machine, so the gateway key travels "
            "unencrypted on every request. Use https:// unless this is a network you trust.",
            file=sys.stderr,
        )


def write_config(url: str, api_key: str, path: Path | None = None) -> None:
    """Write the gateway URL and key where `load_config` reads them, readable by the owner only.

    One implementation for both installers, so the bash and PowerShell paths cannot drift apart
    on the part that handles a secret. Claude SEO's own credential files follow the same rules:

    - the key never arrives on argv, where any local user can read it from the process list;
      the `configure` command takes it from the environment instead
    - written to a same-directory temp file and renamed, so no reader ever sees a half-written
      file, and there is no moment when the file exists with looser permissions
    - 0600 on POSIX, and an ACL restricted to the current user on Windows, where mode bits are
      meaningless. The Windows half is best effort and warns rather than failing, as theirs does
    """
    path = CONFIG_PATH if path is None else path  # at call time; see load_config
    url = _validate_gateway_url(url)
    api_key = _validate_key(api_key)
    _warn_if_plain_http(url)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"url": url, "api_key": api_key}, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _restrict_to_current_user_windows(path)


def _current_user_sid() -> str | None:
    """The current account's SID, from `whoami /user`. None if that is unavailable.

    A SID rather than %USERNAME%, because a bare name is resolved by icacls against the local
    machine first and can name a different account on a domain-joined machine.
    """
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fields = [f.strip().strip('"') for f in result.stdout.strip().split(",")]
    sid = fields[-1] if fields else ""
    return sid if sid.startswith("S-1-") else None


def _restrict_to_current_user_windows(path: Path) -> None:
    """Best effort, and said so: NTFS ignores mode bits, and icacls is the nearest equivalent.

    Between mkstemp and this call the file carries the ACL it inherited from its directory,
    which for a profile directory is normally the user, SYSTEM and Administrators.
    """
    if os.name != "nt":
        return
    sid = _current_user_sid()
    who = f"*{sid}" if sid else os.environ.get("USERNAME", "").strip()
    if not who:
        print(f"warning: could not identify you to restrict {path}", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{who}:F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            print(f"warning: icacls could not restrict {path}: {detail}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: could not restrict {path} with icacls: {exc}", file=sys.stderr)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects.

    The gateway never redirects: it fetches the target and returns the result. A redirect here
    means the configured URL is not a gateway, and following one would send the Authorization
    header to wherever it points.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"refusing to follow a redirect to {newurl}: the gateway never redirects, "
            "so this URL is probably not a Proxlane gateway",
            headers,
            fp,
        )


def _http_only_opener() -> urllib.request.OpenerDirector:
    """An opener that can speak HTTP and HTTPS and nothing else.

    `build_opener()` also installs handlers for `file:`, `ftp:` and `data:`. Every URL here is
    validated as http(s) before it is used, but a validator is one check a future edit can skip;
    an opener with no handler for `file:` cannot open one whatever reaches it.

    `UnknownHandler` is not optional. Without it, `OpenerDirector.open()` returns None for a
    scheme it has no handler for instead of raising, and the refusal would surface as an
    AttributeError somewhere downstream. The test that pins this caught exactly that.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        # NO PROXIES, which is not the default. A default `ProxyHandler()` reads http_proxy and
        # friends from the environment and does not exempt localhost, so the default gateway
        # URL sent the Authorization header, key and all, in cleartext to whatever proxy the
        # machine was configured with. It also honours `file_proxy`, which reopened the `file:`
        # route this opener exists to close. The gateway is the only thing this talks to.
        urllib.request.ProxyHandler({}),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        _NoRedirect(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


_OPENER = _http_only_opener()


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes


def _send(req: urllib.request.Request, timeout_s: float) -> Reply:
    try:
        resp = _OPENER.open(req, timeout=timeout_s)
    except urllib.error.HTTPError as err:
        # The gateway answers failures with a real status and a JSON body. Those are results,
        # not exceptions, so they come back the same way a 200 does.
        resp = err
    with resp:
        body = resp.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            raise ConfigError(f"response is larger than {MAX_BODY_BYTES} bytes; refusing it")
        return Reply(
            status=resp.status if hasattr(resp, "status") else resp.code,
            headers={k: v for k, v in resp.headers.items()},
            body=body,
        )


def build_request(
    cfg: Config,
    target: str,
    *,
    render: bool = False,
    country_code: str | None = None,
    premium: str | None = None,
    timeout_ms: int | None = None,
    wait_for: str | None = None,
    simulate: str | None = None,
) -> urllib.request.Request:
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"target must be an absolute http(s) URL, got {target!r}")

    params: dict[str, str] = {"url": target}
    # Only `true` renders. The gateway treats every other value as false, deliberately, so
    # `render=false` can never render a page by being present.
    if render:
        params["render"] = "true"
    if country_code:
        params["country_code"] = country_code.lower()
    if premium:
        params["premium"] = premium
    if timeout_ms is not None:
        params["timeout"] = str(timeout_ms)
    if wait_for:
        params["wait_for"] = wait_for

    # The key rides in a header, never in the query string, where it would land in the
    # gateway's access log and anything else that records URLs.
    headers = {"Authorization": f"Bearer {cfg.api_key}", "User-Agent": "claude-seo-proxlane"}
    if simulate:
        headers["X-Proxlane-Simulate"] = simulate
    # S310: cfg.url was validated as http(s), and _OPENER has no handler for anything else.
    return urllib.request.Request(  # noqa: S310
        f"{cfg.url}/v1?{urllib.parse.urlencode(params)}", headers=headers, method="GET"
    )


def verdict_from(reply: Reply) -> dict[str, Any]:
    lower = {k.lower(): v for k, v in reply.headers.items()}
    out: dict[str, Any] = {"status": reply.status}
    for name, key in VERDICT_HEADERS.items():
        value = lower.get(name.lower())
        if value is None:
            continue
        # `cost` stays a string: the gateway writes `mixed` when one chain spent in two units.
        out[key] = int(value) if key == "attempts" and value.isdigit() else value

    if "outcome_class" not in out:
        # No verdict headers means the answer did not come from a gateway's /v1 at all, or it was
        # refused at the door. Read the JSON error body when there is one; say so when there is
        # not, rather than inventing a class.
        err = _error_body(reply.body)
        if err is not None:
            out["outcome"] = err.get("code", "UNKNOWN")
            out["outcome_class"] = err.get("class", "client")
            out["message"] = err.get("message", "")
        else:
            out["outcome"] = "NOT_A_GATEWAY_RESPONSE"
            out["outcome_class"] = "client"
            out["message"] = (
                f"HTTP {reply.status} with no Proxlane headers. Is PROXLANE_URL pointing at the gateway?"
            )
    elif out["outcome_class"] != "ok":
        err = _error_body(reply.body)
        if err is not None and err.get("message"):
            out["message"] = err["message"]
    return out


def _error_body(body: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    err = doc.get("error") if isinstance(doc, dict) else None
    return err if isinstance(err, dict) else None


def describe(verdict: dict[str, Any]) -> str:
    """One line a person, or an agent, can act on."""
    cls = verdict.get("outcome_class", "?")
    line = f"proxlane: {verdict.get('outcome', '?')} ({cls})"
    if verdict.get("provider"):
        line += f" via {verdict['provider']}"
    if verdict.get("attempts"):
        line += f", {verdict['attempts']} attempt(s)"
    if verdict.get("cost") is not None:
        line += f", cost {verdict['cost']} {verdict.get('cost_unit', '')}".rstrip()
    if verdict.get("detect_rule"):
        line += f", block rule {verdict['detect_rule']}"
    if verdict.get("simulated"):
        line += " [SIMULATED, no provider was called]"
    if verdict.get("message"):
        line += f". {verdict['message']}"
    return line


def cmd_check(cfg: Config, timeout_s: float) -> int:
    """Reachable, the right software, and the key accepted. Spends nothing, and does not count
    against the daily fetch limit.

    The key is checked by asking /v1 for nothing: the gateway authenticates before it validates,
    so a good key gets a 400 for the missing url and a bad one gets a 401, and no provider is
    ever called. That is measured behaviour, not an assumption; the integration tests pin it.
    """
    _warn_if_plain_http(cfg.url)
    try:
        health = _send(urllib.request.Request(f"{cfg.url}/health"), timeout_s)  # noqa: S310
    except (urllib.error.URLError, OSError, http.client.HTTPException, ConfigError) as exc:
        print(f"proxlane: cannot reach {cfg.url}: {exc}", file=sys.stderr)
        print(
            "  Is the gateway running? docker run -p 8787:8787 ... ghcr.io/proxlane/gateway", file=sys.stderr
        )
        return EXIT_RETRY
    try:
        info = json.loads(health.body)
        version = info["version"]
    except (ValueError, KeyError, TypeError):
        print(f"proxlane: {cfg.url}/health did not answer like a Proxlane gateway", file=sys.stderr)
        return EXIT_CLIENT

    probe = urllib.request.Request(  # noqa: S310  (validated http(s); see _http_only_opener)
        f"{cfg.url}/v1", headers={"Authorization": f"Bearer {cfg.api_key}"}, method="GET"
    )
    try:
        auth = _send(probe, timeout_s)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ConfigError) as exc:
        print(f"proxlane: /v1 unreachable: {exc}", file=sys.stderr)
        return EXIT_RETRY
    if auth.status == 401:
        print(f"proxlane: gateway {version} is up, but it rejected the key.", file=sys.stderr)
        return EXIT_CLIENT

    providers, usable = info.get("providers"), info.get("usable")
    print(
        f"proxlane: gateway {version} at {cfg.url}, key accepted, {usable} of {providers} provider(s) usable",
        flush=True,
    )
    if providers == 0:
        print(
            "  No provider keys are configured on the gateway. Every fetch will return "
            "NO_PROVIDER_AVAILABLE until one is set, e.g. SCRAPERAPI_KEY.",
            file=sys.stderr,
        )
    return EXIT_OK


# What --output may produce. Page formats only: the body is chosen by the site being fetched,
# and anything a tool loads as configuration or as instructions is out of reach by extension.
#
# NOT `.json`. It was here, and a review showed `--output package.json` creating a manifest in a
# project that had none, whose scripts the next `npm install` would run; `deno.json` and
# `composer.json` the same. Too many tools treat a JSON file in a project as configuration to
# name them all, and nothing an SEO audit saves needs it: `--json` already prints the verdict.
OUTPUT_SUFFIXES = {".html", ".htm", ".xml", ".txt"}

# Refused by name whatever the suffix rule allows, because a tool executes or obeys them.
# A denylist backing the allowlist above, not replacing it: it cannot be complete, which is why
# the suffix rule does the real work.
FORBIDDEN_OUTPUT_NAMES = {
    "claude.md",  # read as instructions by coding agents
    "agents.md",
    "gemini.md",
    ".mcp.json",
    "pom.xml",  # Maven runs its plugins
    "build.xml",  # Ant runs its targets
    "cmakelists.txt",  # CMake runs it
}
# requirements.txt, requirements-dev.txt, constraints.txt: pip installs what they name.
FORBIDDEN_OUTPUT_PATTERN = re.compile(r"^(requirements|constraints)([-_.].*)?\.txt$", re.IGNORECASE)


def resolve_output(target: str, *, cwd: Path | None = None, home: Path | None = None) -> Path:
    """Where `--output` may write: a page file, inside the working directory, and nowhere else.

    The body is chosen by the site being fetched. Staying inside the working directory stops
    `~/.zshrc`, but a working directory is usually a project, and a project is full of files
    that tools load as configuration or instructions: `.claude/settings.local.json`,
    `.mcp.json`, `CLAUDE.md`. A security review showed all of those accepted by the first
    version of this check. So, in addition:

    - no path component may start with `.`, which rules out every dotfile and dot-directory
    - agent instruction files and build or dependency manifests are refused by name
    - the suffix must be a page format, and `.json` is deliberately not one
    - the working directory must not be the home directory or any directory above it
    """
    base = (cwd or Path.cwd()).resolve()
    home_dir = (home or Path.home()).resolve()
    # Home itself, OR ANY DIRECTORY ABOVE IT. Checking only equality let a working directory of
    # `/Users` write `me/Library/Application Support/Code/User/settings.json`, which has no dot
    # component anywhere in it.
    if base == home_dir or base in home_dir.parents:
        raise ConfigError(
            "--output is refused when the working directory is your home directory or above it. "
            "Change to a project or scratch directory first."
        )
    resolved = (base / target).resolve()
    if resolved != base and base not in resolved.parents:
        raise ConfigError(f"--output must be inside the working directory ({base}), got {target!r}")
    relative = resolved.relative_to(base)
    if any(part.startswith(".") for part in relative.parts):
        raise ConfigError(f"--output may not write a dotfile or into a dot-directory, got {target!r}")
    if resolved.name.lower() in FORBIDDEN_OUTPUT_NAMES or FORBIDDEN_OUTPUT_PATTERN.match(resolved.name):
        raise ConfigError(f"--output may not write {resolved.name}, which a tool executes or obeys")
    if resolved.suffix.lower() not in OUTPUT_SUFFIXES:
        allowed = ", ".join(sorted(OUTPUT_SUFFIXES))
        raise ConfigError(f"--output must end in one of {allowed}, got {target!r}")
    if resolved.is_dir():
        raise ConfigError(f"--output {target!r} is a directory")
    return resolved


def _usage_limit(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("PROXLANE_DAILY_FETCH_LIMIT", "")
    if not raw:
        return DEFAULT_DAILY_FETCH_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ConfigError(f"PROXLANE_DAILY_FETCH_LIMIT must be a whole number, got {raw!r}") from exc
    if limit < 0:
        raise ConfigError("PROXLANE_DAILY_FETCH_LIMIT cannot be negative")
    return limit


def reserve_fetch(limit: int, path: Path | None = None, today: str | None = None) -> int:
    """Count one paid fetch against today's limit, or refuse. Returns the count after this one.

    Counted BEFORE the request, because a fetch that is refused after spending is no cap at
    all. Approximate under concurrency: two fetches racing can both read the same count. The
    cap exists to stop a runaway loop, not to do accounting, and the gateway reports the real
    spend on every response.
    """
    path = USAGE_PATH if path is None else path
    today = today or datetime.date.today().isoformat()
    used = 0
    try:
        doc = json.loads(path.read_text())
        if doc.get("date") == today:
            used = int(doc.get("fetches", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        used = 0
    if used >= limit:
        raise ConfigError(
            f"daily fetch limit reached ({used} of {limit}). Nothing was requested and nothing "
            "was spent. Ask the user before raising PROXLANE_DAILY_FETCH_LIMIT."
        )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_atomic(path, json.dumps({"date": today, "fetches": used + 1}).encode())
    return used + 1


def cmd_fetch(cfg: Config, args: argparse.Namespace, timeout_s: float) -> int:
    output = resolve_output(args.output) if args.output else None
    if output is not None and output.exists() and not args.force:
        raise ConfigError(f"{args.output} already exists. Pass --force to replace it.")

    req = build_request(
        cfg,
        args.url,
        render=args.render,
        country_code=args.country,
        premium=args.premium,
        timeout_ms=args.timeout,
        wait_for=args.wait_for,
        simulate=args.simulate,
    )
    # A simulation calls no provider and spends nothing, so it does not count against the cap.
    if not args.simulate:
        reserve_fetch(_usage_limit())

    try:
        reply = _send(req, timeout_s)
    except urllib.error.URLError as exc:
        print(f"proxlane: cannot reach {cfg.url}: {exc.reason}", file=sys.stderr)
        return EXIT_RETRY
    except (OSError, http.client.HTTPException) as exc:
        # HTTPException covers a connection the gateway dropped mid-body (IncompleteRead).
        # Named by type only: the message of a low-level error can carry request details.
        print(f"proxlane: the request failed ({type(exc).__name__}). Retry later.", file=sys.stderr)
        return EXIT_RETRY

    verdict = verdict_from(reply)
    verdict["url"] = args.url
    code = EXIT_FOR_CLASS.get(verdict["outcome_class"], EXIT_CLIENT)
    page = reply.body if verdict["outcome_class"] == "ok" else b""

    if output is not None:
        if page:
            _write_atomic(output, page)
            verdict["output"] = str(output)
        elif output.exists():
            # Only reachable with --force. A file left from an earlier run, beside a verdict that
            # says this fetch failed, is exactly how stale content gets analysed as current.
            output.unlink()
            verdict["output_removed"] = str(output)
    verdict["bytes"] = len(page)

    if args.json:
        if page and output is None:
            verdict["body"] = page.decode("utf-8", errors="replace")
        print(json.dumps(verdict, indent=2))
    elif page and output is None:
        sys.stdout.write(page.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    print(describe(verdict), file=sys.stderr)
    return code


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proxlane_fetch.py", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the gateway (default 120, above its own deadline)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify the gateway is reachable and the key is accepted")

    c = sub.add_parser(
        "configure",
        help="record the gateway URL and key (the key comes from "
        "PROXLANE_CONFIGURE_KEY, never from the command line)",
    )
    c.add_argument("--url", required=True, help="the gateway, e.g. http://localhost:8787")

    f = sub.add_parser("fetch", help="fetch one page through the gateway")
    f.add_argument("url")
    f.add_argument("--render", action="store_true", help="run JavaScript. Costs up to 5-25x a plain fetch")
    f.add_argument("--country", help="ISO 3166-1 alpha-2 country the request should come from")
    f.add_argument("--premium", choices=["none", "residential", "stealth"], help="proxy tier")
    f.add_argument("--timeout", type=int, help="deadline in ms, at least 8000, capped by the gateway")
    f.add_argument("--wait-for", help="CSS selector to wait for. Implies --render")
    f.add_argument("--output", help="write the page to this file, inside the working directory")
    f.add_argument("--force", action="store_true", help="let --output replace an existing file")
    f.add_argument("--json", action="store_true", help="one JSON object: the verdict, with the body")
    f.add_argument("--simulate", help="sandbox only: return this outcome without calling a provider")

    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            write_config(args.url, os.environ.get("PROXLANE_CONFIGURE_KEY", ""))
            print(f"proxlane: wrote {CONFIG_PATH}")
            return EXIT_OK
        cfg = load_config()
        if args.command == "check":
            return cmd_check(cfg, args.connect_timeout)
        return cmd_fetch(cfg, args, args.connect_timeout)
    except ConfigError as exc:
        print(f"proxlane: {exc}", file=sys.stderr)
        return EXIT_CLIENT
    except ValueError as exc:
        # Never the message: urllib's "Invalid header value" quotes the header, key included.
        # _validate_key() should make this unreachable; this is the net under it.
        print(f"proxlane: invalid request ({type(exc).__name__}). Check the gateway key.", file=sys.stderr)
        return EXIT_CLIENT


if __name__ == "__main__":
    sys.exit(main())
