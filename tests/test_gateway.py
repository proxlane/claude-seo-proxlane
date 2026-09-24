"""Against a real gateway in sandbox mode. No provider is called and nothing is spent.

Set PROXLANE_TEST_URL and PROXLANE_TEST_SANDBOX_KEY to run these. CI starts the published
gateway image as a service container, the same way scrapy-proxlane's tests do.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

URL = os.environ.get("PROXLANE_TEST_URL")
SANDBOX_KEY = os.environ.get("PROXLANE_TEST_SANDBOX_KEY")
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "seo-proxlane" / "proxlane_fetch.py"

pytestmark = pytest.mark.skipif(
    not (URL and SANDBOX_KEY), reason="needs PROXLANE_TEST_URL and PROXLANE_TEST_SANDBOX_KEY"
)


def run(*args, key=None, tmp_home=None):
    env = {**os.environ, "PROXLANE_URL": URL, "PROXLANE_API_KEY": key or SANDBOX_KEY}
    if tmp_home is not None:
        env["HOME"] = str(tmp_home)  # no stray ~/.config/claude-seo/proxlane.json
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_check_accepts_the_key_and_reports_the_version(tmp_path):
    r = run("check", tmp_home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "key accepted" in r.stdout


def test_check_rejects_a_wrong_key_without_calling_a_provider(tmp_path):
    r = run("check", key="definitely-not-the-key", tmp_home=tmp_path)
    assert r.returncode == 2
    assert "rejected the key" in r.stderr


@pytest.mark.parametrize(
    "outcome,cls,code",
    [
        ("OK", "ok", 0),
        ("SOFT_BLOCK", "blocked", 4),
        ("TARGET_NOT_FOUND", "target", 3),
        ("PROVIDER_TIMEOUT", "provider", 5),
    ],
)
def test_each_class_exits_with_its_code(outcome, cls, code, tmp_path):
    r = run("fetch", "https://example.com", "--simulate", outcome, "--json", tmp_home=tmp_path)
    assert r.returncode == code, r.stderr
    verdict = json.loads(r.stdout)
    assert verdict["outcome"] == outcome
    assert verdict["outcome_class"] == cls
    assert verdict["simulated"] == outcome


def test_a_blocked_page_never_reaches_stdout_as_content(tmp_path):
    # The whole point: an agent reading stdout must not receive a challenge page as if it were
    # the site. The verdict still goes to stderr.
    r = run("fetch", "https://example.com", "--simulate", "SOFT_BLOCK", tmp_home=tmp_path)
    assert r.stdout == ""
    assert "blocked" in r.stderr
    assert "cloudflare" in r.stderr


def test_an_ok_page_goes_to_stdout_and_the_verdict_to_stderr(tmp_path):
    r = run("fetch", "https://example.com", "--simulate", "OK", tmp_home=tmp_path)
    assert r.returncode == 0
    assert "<html" in r.stdout.lower()
    assert "SIMULATED" in r.stderr


def run_in(cwd, *args, **env_extra):
    """Run with `cwd` as the working directory and a SEPARATE throwaway home beside it, since
    `--output` refuses to write when the working directory is the home directory."""
    home = cwd.parent / f"{cwd.name}-home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "PROXLANE_URL": URL, "PROXLANE_API_KEY": SANDBOX_KEY, "HOME": str(home), **env_extra}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=cwd
    )


def test_output_writes_the_page_inside_the_working_directory(tmp_path):
    r = run_in(tmp_path, "fetch", "https://example.com", "--simulate", "OK", "--output", "ok.html")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "ok.html").read_text().lower().startswith("<!doctype html")


def test_output_refuses_a_path_outside_the_working_directory(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    r = run_in(work, "fetch", "https://example.com", "--simulate", "OK", "--output", "../escaped.html")
    assert r.returncode == 2
    assert not (tmp_path / "escaped.html").exists()


def test_output_will_not_replace_a_file_without_force(tmp_path):
    (tmp_path / "page.html").write_text("mine")
    r = run_in(tmp_path, "fetch", "https://example.com", "--simulate", "OK", "--output", "page.html")
    assert r.returncode == 2 and "--force" in r.stderr
    assert (tmp_path / "page.html").read_text() == "mine"


def test_a_failed_fetch_removes_a_stale_output_rather_than_leaving_it(tmp_path):
    (tmp_path / "page.html").write_text("yesterday's content")
    r = run_in(
        tmp_path,
        "fetch",
        "https://example.com",
        "--simulate",
        "SOFT_BLOCK",
        "--output",
        "page.html",
        "--force",
    )
    assert r.returncode == 4
    assert not (tmp_path / "page.html").exists()


def test_the_daily_cap_stops_paid_fetches_but_not_simulations(tmp_path):
    # The sandbox key refuses a real fetch, but the cap is checked before the request is made,
    # so a limit of 0 must refuse without anything being sent, and a simulation is never counted.
    r = run_in(tmp_path, "fetch", "https://example.com", PROXLANE_DAILY_FETCH_LIMIT="0")
    assert r.returncode == 2 and "daily fetch limit reached" in r.stderr
    r = run_in(tmp_path, "fetch", "https://example.com", "--simulate", "OK", PROXLANE_DAILY_FETCH_LIMIT="0")
    assert r.returncode == 0


def test_an_environment_url_alone_cannot_borrow_the_stored_key(tmp_path):
    r = run_in(tmp_path, "configure", "--url", URL, PROXLANE_CONFIGURE_KEY=SANDBOX_KEY)
    assert r.returncode == 0, r.stderr
    env = {k: v for k, v in os.environ.items() if k != "PROXLANE_API_KEY"}
    env.update(HOME=str(tmp_path), PROXLANE_URL="http://127.0.0.1:9")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "check"], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert r.returncode == 2
    assert "set both or neither" in r.stderr


def test_the_gateway_refuses_a_private_target_for_us(tmp_path):
    # SSRF on the TARGET is the gateway's job, at its edge. This pins that the script relies on
    # it rather than duplicating it, and that the refusal comes back as a client error.
    r = run("fetch", "http://169.254.169.254/latest/meta-data/", "--json", tmp_home=tmp_path)
    assert r.returncode == 2, r.stderr
    assert json.loads(r.stdout)["outcome"] == "TARGET_FORBIDDEN"


def test_force_clears_the_old_file_even_when_the_gateway_is_unreachable(tmp_path):
    # Removing the old file only on a non-ok verdict left it in place when the request never
    # got an answer at all: stale content beside a failed fetch, the thing --force must prevent.
    work = tmp_path / "work"
    work.mkdir()
    (work / "page.html").write_text("yesterday's content")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "PROXLANE_URL": "http://127.0.0.1:9",
        "PROXLANE_API_KEY": SANDBOX_KEY,
        "HOME": str(home),
    }
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "fetch", "https://example.com", "--output", "page.html", "--force"],
        capture_output=True,
        text=True,
        env=env,
        cwd=work,
    )
    assert r.returncode == 5, r.stderr
    assert not (work / "page.html").exists()
