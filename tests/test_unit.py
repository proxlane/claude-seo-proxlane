"""Everything that does not need a gateway."""

import http.server
import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.parse

import proxlane_fetch as pf
import pytest

KEY = "a-gateway-key-for-tests"


class TestLoadConfig:
    def test_environment_wins_over_the_file(self, tmp_path):
        path = tmp_path / "proxlane.json"
        path.write_text(json.dumps({"url": "http://file:1", "api_key": "from-file"}))
        cfg = pf.load_config({"PROXLANE_URL": "http://env:2", "PROXLANE_API_KEY": "from-env"}, path)
        assert cfg == pf.Config(url="http://env:2", api_key="from-env")

    def test_falls_back_to_the_file(self, tmp_path):
        path = tmp_path / "proxlane.json"
        path.write_text(json.dumps({"url": "http://file:1/", "api_key": "from-file"}))
        cfg = pf.load_config({}, path)
        assert cfg == pf.Config(url="http://file:1", api_key="from-file")

    def test_defaults_the_url_but_never_the_key(self, tmp_path):
        with pytest.raises(pf.ConfigError, match="no gateway key"):
            pf.load_config({}, tmp_path / "absent.json")
        cfg = pf.load_config({"PROXLANE_API_KEY": KEY}, tmp_path / "absent.json")
        assert cfg.url == pf.DEFAULT_URL

    def test_rejects_a_url_that_is_not_http(self, tmp_path):
        with pytest.raises(pf.ConfigError, match="http"):
            pf.load_config({"PROXLANE_URL": "file:///etc/passwd", "PROXLANE_API_KEY": KEY}, tmp_path / "x")

    def test_a_corrupt_file_is_an_error_not_a_silent_default(self, tmp_path):
        path = tmp_path / "proxlane.json"
        path.write_text("{not json")
        with pytest.raises(pf.ConfigError, match="not readable JSON"):
            pf.load_config({}, path)


class TestBuildRequest:
    cfg = pf.Config(url="http://gw:8787", api_key=KEY)

    def query(self, req):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))

    def test_the_key_never_enters_the_url(self):
        req = pf.build_request(self.cfg, "https://example.com/?a=1")
        assert KEY not in req.full_url
        assert req.get_header("Authorization") == f"Bearer {KEY}"

    def test_the_target_is_carried_intact(self):
        req = pf.build_request(self.cfg, "https://example.com/p?a=1&b=2")
        assert self.query(req) == {"url": "https://example.com/p?a=1&b=2"}

    def test_render_is_only_ever_sent_as_true(self):
        # The gateway renders on `true` or `1` only. Sending anything else, or nothing, is the
        # only safe spelling of "do not render".
        assert "render" not in self.query(pf.build_request(self.cfg, "https://e.com"))
        assert self.query(pf.build_request(self.cfg, "https://e.com", render=True))["render"] == "true"

    def test_options_map_to_gateway_parameters(self):
        req = pf.build_request(
            self.cfg,
            "https://e.com",
            country_code="DE",
            premium="residential",
            timeout_ms=30000,
            wait_for="#main",
        )
        q = self.query(req)
        assert q["country_code"] == "de"
        assert q["premium"] == "residential"
        assert q["timeout"] == "30000"
        assert q["wait_for"] == "#main"

    def test_simulate_is_a_header_not_a_parameter(self):
        req = pf.build_request(self.cfg, "https://e.com", simulate="SOFT_BLOCK")
        assert req.get_header("X-proxlane-simulate") == "SOFT_BLOCK"
        assert "simulate" not in self.query(req)

    @pytest.mark.parametrize("target", ["example.com", "ftp://example.com", "/relative"])
    def test_refuses_a_target_that_is_not_an_absolute_http_url(self, target):
        with pytest.raises(pf.ConfigError, match="absolute http"):
            pf.build_request(self.cfg, target)


class TestVerdict:
    def test_reads_every_gateway_header(self):
        reply = pf.Reply(
            200,
            {
                "X-Outcome": "OK",
                "X-Outcome-Class": "ok",
                "X-Provider-Used": "scrapfly",
                "X-Attempts": "2",
                "X-Chain": "scraperapi:PROVIDER_TIMEOUT>scrapfly:OK",
                "X-Cost-Estimate": "6.000000",
                "X-Cost-Unit": "provider-credits",
            },
            b"<html>",
        )
        v = pf.verdict_from(reply)
        assert v["outcome_class"] == "ok"
        assert v["attempts"] == 2
        assert v["cost"] == "6.000000"  # a string: the gateway may write `mixed`

    def test_header_names_are_case_insensitive(self):
        v = pf.verdict_from(pf.Reply(502, {"x-outcome": "SOFT_BLOCK", "x-outcome-class": "blocked"}, b""))
        assert v["outcome_class"] == "blocked"

    def test_a_refusal_at_the_door_is_read_from_the_body(self):
        body = json.dumps({"error": {"code": "UNAUTHORIZED", "class": "client", "message": "bad key"}})
        v = pf.verdict_from(pf.Reply(401, {}, body.encode()))
        assert (v["outcome"], v["outcome_class"], v["message"]) == ("UNAUTHORIZED", "client", "bad key")

    def test_something_that_is_not_a_gateway_is_named_rather_than_guessed(self):
        v = pf.verdict_from(pf.Reply(200, {"Content-Type": "text/html"}, b"<html>nginx</html>"))
        assert v["outcome"] == "NOT_A_GATEWAY_RESPONSE"
        assert v["outcome_class"] == "client"

    @pytest.mark.parametrize(
        "cls,code",
        [
            ("ok", 0),
            ("target", 3),
            ("blocked", 4),
            ("provider", 5),
            ("gateway", 5),
            ("client", 2),
        ],
    )
    def test_exit_codes_follow_the_class(self, cls, code):
        assert pf.EXIT_FOR_CLASS[cls] == code

    def test_every_class_the_gateway_documents_has_an_exit_code(self):
        assert set(pf.EXIT_FOR_CLASS) == {"ok", "target", "blocked", "provider", "gateway", "client"}


class TestRedirects:
    """The gateway never redirects. Following one would send the key somewhere else."""

    def test_refuses_to_follow_a_redirect(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/v1"):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = pf.Config(url=f"http://127.0.0.1:{server.server_port}", api_key=KEY)
            reply = pf._send(pf.build_request(cfg, "https://example.com"), 5)
            assert reply.status == 302
            assert [p for p, _ in seen if p == "/elsewhere"] == []
        finally:
            server.shutdown()


def test_describe_marks_a_simulation():
    line = pf.describe({"outcome": "SOFT_BLOCK", "outcome_class": "blocked", "simulated": "SOFT_BLOCK"})
    assert "SIMULATED" in line


class TestWriteConfig:
    def test_round_trips_through_load_config(self, tmp_path):
        path = tmp_path / "claude-seo" / "proxlane.json"
        pf.write_config("http://gw:8787/", KEY, path)
        assert pf.load_config({}, path) == pf.Config(url="http://gw:8787", api_key=KEY)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_is_readable_by_the_owner_only(self, tmp_path):
        path = tmp_path / "claude-seo" / "proxlane.json"
        pf.write_config("http://gw:8787", KEY, path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700

    def test_leaves_no_temp_file_behind(self, tmp_path):
        path = tmp_path / "proxlane.json"
        pf.write_config("http://gw:8787", KEY, path)
        pf.write_config("http://gw:8788", KEY, path)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["proxlane.json"]
        assert json.loads(path.read_text())["url"] == "http://gw:8788"

    def test_refuses_an_empty_key_and_writes_nothing(self, tmp_path):
        path = tmp_path / "proxlane.json"
        with pytest.raises(pf.ConfigError, match="no gateway key"):
            pf.write_config("http://gw:8787", "", path)
        assert not path.exists()

    def test_refuses_a_url_that_is_not_http(self, tmp_path):
        with pytest.raises(pf.ConfigError, match="http"):
            pf.write_config("javascript:alert(1)", KEY, tmp_path / "proxlane.json")

    def test_configure_takes_the_key_from_the_environment_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pf, "CONFIG_PATH", tmp_path / "proxlane.json")
        monkeypatch.delenv("PROXLANE_CONFIGURE_KEY", raising=False)
        assert pf.main(["configure", "--url", "http://gw:8787"]) == pf.EXIT_CLIENT
        monkeypatch.setenv("PROXLANE_CONFIGURE_KEY", KEY)
        assert pf.main(["configure", "--url", "http://gw:8787"]) == pf.EXIT_OK
        assert json.loads((tmp_path / "proxlane.json").read_text())["api_key"] == KEY


class TestOpener:
    """The opener is the enforcement; URL validation is only the first line."""

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "data:,hello"])
    def test_cannot_open_anything_but_http(self, url):
        with pytest.raises(urllib.error.URLError, match="unknown url type"):
            pf._OPENER.open(url, timeout=2)


class TestKeyStaysWithItsGateway:
    """The review finding that blocked publishing: one variable must not redirect the key."""

    def test_an_environment_url_is_never_paired_with_the_stored_key(self, tmp_path):
        path = tmp_path / "proxlane.json"
        pf.write_config("http://localhost:8787", "STORED-KEY-must-stay-home", path)
        with pytest.raises(pf.ConfigError, match="set both or neither"):
            pf.load_config({"PROXLANE_URL": "https://attacker.example"}, path)

    def test_the_environment_pair_is_used_whole(self, tmp_path):
        path = tmp_path / "proxlane.json"
        pf.write_config("http://localhost:8787", "STORED-KEY", path)
        cfg = pf.load_config({"PROXLANE_URL": "http://other:1", "PROXLANE_API_KEY": "ENV-KEY"}, path)
        assert cfg == pf.Config(url="http://other:1", api_key="ENV-KEY")

    def test_the_file_pair_is_used_whole(self, tmp_path):
        path = tmp_path / "proxlane.json"
        pf.write_config("http://localhost:9999", "STORED-KEY", path)
        assert pf.load_config({}, path) == pf.Config(url="http://localhost:9999", api_key="STORED-KEY")

    @pytest.mark.parametrize("bad", ["ke\ry", "key\ninjected: header", "key\x00"])
    def test_a_key_with_a_control_character_is_refused_without_echoing_it(self, bad, tmp_path):
        with pytest.raises(pf.ConfigError) as err:
            pf.load_config({"PROXLANE_API_KEY": bad}, tmp_path / "absent.json")
        assert "control character" in str(err.value)
        assert "injected" not in str(err.value)

    @pytest.mark.parametrize("pasted", ["  padded-key  ", "padded-key\r", "padded-key\r\n"])
    def test_surrounding_whitespace_from_a_paste_is_stripped(self, pasted, tmp_path):
        # The reviewer's leak case was a trailing CR: the header was invalid and urllib quoted it,
        # key and all, to stderr. Stripping it makes the key usable instead of refused.
        cfg = pf.load_config({"PROXLANE_API_KEY": pasted}, tmp_path / "absent.json")
        assert cfg.api_key == "padded-key"


class TestNoProxy:
    """A proxy from the environment must never see the Authorization header."""

    def test_an_environment_proxy_is_ignored(self, monkeypatch):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        proxy = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        gateway = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        for srv in (proxy, gateway):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
            opener = pf._http_only_opener()  # built after the variable is set, as at startup
            cfg = pf.Config(url=f"http://127.0.0.1:{gateway.server_port}", api_key=KEY)
            req = pf.build_request(cfg, "https://example.com")
            with opener.open(req, timeout=5) as resp:
                assert resp.status == 200
            assert seen == [f"Bearer {KEY}"]  # exactly once, and that was the gateway
        finally:
            proxy.shutdown()
            gateway.shutdown()

    def test_file_proxy_does_not_reopen_the_file_scheme(self, monkeypatch):
        monkeypatch.setenv("file_proxy", "http://127.0.0.1:9")
        opener = pf._http_only_opener()
        with pytest.raises(urllib.error.URLError, match="unknown url type"):
            opener.open("file:///etc/passwd", timeout=2)


class TestOutputPath:
    def cwd(self, tmp_path):
        work = tmp_path / "project"
        work.mkdir()
        return work

    def test_a_page_inside_the_working_directory_is_allowed(self, tmp_path):
        work = self.cwd(tmp_path)
        assert pf.resolve_output("pages/home.html", cwd=work, home=tmp_path) == work / "pages" / "home.html"

    @pytest.mark.parametrize("target", ["../escape.html", "/etc/hosts.html"])
    def test_outside_it_is_refused(self, target, tmp_path):
        with pytest.raises(pf.ConfigError, match="inside the working directory"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    def test_a_symlink_out_of_the_directory_is_refused(self, tmp_path):
        work, outside = self.cwd(tmp_path), tmp_path / "outside"
        outside.mkdir()
        (work / "link").symlink_to(outside)
        with pytest.raises(pf.ConfigError, match="inside the working directory"):
            pf.resolve_output("link/page.html", cwd=work, home=tmp_path)

    @pytest.mark.parametrize(
        "target",
        [
            ".claude/settings.local.json",  # hooks run commands
            ".mcp.json",  # declares servers to start
            ".vscode/settings.json",
            "pages/.hidden.html",
        ],
    )
    def test_dotfiles_and_dot_directories_are_refused(self, target, tmp_path):
        # Each of these was ACCEPTED by the first version of this check, in a security review.
        with pytest.raises(pf.ConfigError, match="dotfile"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    @pytest.mark.parametrize("target", ["CLAUDE.md", "agents.md", "docs/GEMINI.md"])
    def test_files_agents_read_as_instructions_are_refused(self, target, tmp_path):
        with pytest.raises(pf.ConfigError, match="executes or obeys|must end in"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    @pytest.mark.parametrize(
        "target",
        [
            "pom.xml",
            "build.xml",
            "CMakeLists.txt",
            "requirements.txt",
            "requirements-dev.txt",
            "sub/constraints.txt",
        ],
    )
    def test_files_a_build_tool_would_execute_are_refused(self, target, tmp_path):
        with pytest.raises(pf.ConfigError, match="executes or obeys"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    @pytest.mark.parametrize("target", ["package.json", "deno.json", "composer.json", "data.json"])
    def test_json_is_not_a_page_format(self, target, tmp_path):
        # A review showed `package.json` created, unforced, in a project that had none.
        with pytest.raises(pf.ConfigError, match="must end in"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    @pytest.mark.parametrize("target", ["run.sh", "page", "evil.py", "notes.md"])
    def test_only_page_formats_are_allowed(self, target, tmp_path):
        with pytest.raises(pf.ConfigError, match="must end in"):
            pf.resolve_output(target, cwd=self.cwd(tmp_path), home=tmp_path)

    def test_the_home_directory_as_working_directory_is_refused(self, tmp_path):
        with pytest.raises(pf.ConfigError, match="home directory"):
            pf.resolve_output("page.html", cwd=tmp_path, home=tmp_path)

    def test_a_directory_above_home_is_refused_too(self, tmp_path):
        # With cwd=/Users, `me/Library/.../settings.json` has no dot component at all.
        home = tmp_path / "Users" / "me"
        home.mkdir(parents=True)
        with pytest.raises(pf.ConfigError, match="home directory or above it"):
            pf.resolve_output("me/Library/page.html", cwd=tmp_path / "Users", home=home)

    def test_sitemaps_and_robots_still_save(self, tmp_path):
        work = self.cwd(tmp_path)
        for name in ("sitemap.xml", "robots.txt", "page.htm"):
            assert pf.resolve_output(name, cwd=work, home=tmp_path) == work / name


class TestDailyCap:
    def test_counts_and_then_refuses(self, tmp_path):
        path = tmp_path / "usage.json"
        assert pf.reserve_fetch(2, path, today="2026-09-24") == 1
        assert pf.reserve_fetch(2, path, today="2026-09-24") == 2
        with pytest.raises(pf.ConfigError, match="daily fetch limit reached"):
            pf.reserve_fetch(2, path, today="2026-09-24")

    def test_resets_on_a_new_day(self, tmp_path):
        path = tmp_path / "usage.json"
        pf.reserve_fetch(1, path, today="2026-09-24")
        assert pf.reserve_fetch(1, path, today="2026-09-25") == 1

    def test_a_corrupt_ledger_starts_again_rather_than_crashing(self, tmp_path):
        path = tmp_path / "usage.json"
        path.write_text("{not json")
        assert pf.reserve_fetch(5, path, today="2026-09-24") == 1

    def test_zero_means_no_paid_fetches(self, tmp_path):
        with pytest.raises(pf.ConfigError):
            pf.reserve_fetch(0, tmp_path / "usage.json", today="2026-09-24")

    @pytest.mark.parametrize("raw", ["lots", "-1"])
    def test_a_bad_limit_is_an_error(self, raw):
        with pytest.raises(pf.ConfigError):
            pf._usage_limit({"PROXLANE_DAILY_FETCH_LIMIT": raw})


def test_plain_http_to_another_machine_warns(capsys):
    pf._warn_if_plain_http("http://gateway.internal:8787")
    assert "unencrypted" in capsys.readouterr().err
    pf._warn_if_plain_http("http://localhost:8787")
    pf._warn_if_plain_http("https://gateway.example")
    assert capsys.readouterr().err == ""
