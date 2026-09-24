#!/usr/bin/env bash
# The installer and uninstaller, run for real against a throwaway HOME.
#
# Needs PROXLANE_TEST_URL and PROXLANE_TEST_SANDBOX_KEY: the same sandbox gateway the Python
# tests use. Nothing here touches the real ~/.claude or ~/.config.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
: "${PROXLANE_TEST_URL:?set PROXLANE_TEST_URL}"
: "${PROXLANE_TEST_SANDBOX_KEY:?set PROXLANE_TEST_SANDBOX_KEY}"

fail() { echo "FAIL: $*"; exit 1; }
pass() { echo "ok   $*"; }

HOME_DIR="$(mktemp -d)"
SHIM="$(mktemp -d)"
trap 'rm -rf "${HOME_DIR}" "${SHIM}"' EXIT
mkdir -p "${HOME_DIR}/.claude/skills/seo"   # pretend Claude SEO is installed

run_install() {
    HOME="${HOME_DIR}" PROXLANE_URL="$1" PROXLANE_API_KEY="$2" "${REPO}/install.sh" </dev/null
}

# 1. A clean install writes the skill, the script and a 0600 config, and the check passes.
out="$(run_install "${PROXLANE_TEST_URL}" "${PROXLANE_TEST_SANDBOX_KEY}" 2>&1)" || fail "install exited non-zero: ${out}"
for f in SKILL.md proxlane_fetch.py LICENSE; do
    [ -f "${HOME_DIR}/.claude/skills/seo-proxlane/${f}" ] || fail "missing ${f}"
done
pass "installs the skill files"

CONFIG="${HOME_DIR}/.config/claude-seo/proxlane.json"
[ -f "${CONFIG}" ] || fail "no config written"
mode="$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "${CONFIG}")"
[ "${mode}" = "0o600" ] || fail "config mode is ${mode}, want 0o600"
pass "writes the config at 0600"

python3 - "${CONFIG}" "${PROXLANE_TEST_URL}" "${PROXLANE_TEST_SANDBOX_KEY}" <<'PY' || fail "config contents"
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc == {"url": sys.argv[2], "api_key": sys.argv[3]}, doc
PY
pass "records the url and key"

echo "${out}" | grep -q "key accepted" || fail "the post-install check did not pass: ${out}"
pass "runs the check after installing"

# 2. The installed script works from its installed location, reading only the config file.
HOME="${HOME_DIR}" env -u PROXLANE_URL -u PROXLANE_API_KEY \
    python3 "${HOME_DIR}/.claude/skills/seo-proxlane/proxlane_fetch.py" fetch https://example.com --simulate OK >/dev/null 2>&1 \
    || fail "the installed script could not fetch using the config file alone"
pass "the installed script reads its config"

# 3. The key never appears on a command line. Tested for real rather than by reading the
#    installer: a `python3` shim first on PATH records every argv it is given and then runs the
#    real interpreter, so anything the installer puts on a python command line is in the log.
REAL_PY="$(command -v python3)"
cat > "${SHIM}/python3" <<SH
#!/bin/sh
printf '%s\n' "\$*" >> "${SHIM}/argv.log"
exec "${REAL_PY}" "\$@"
SH
chmod +x "${SHIM}/python3"
SECRET="argv-canary-$(date +%s)-${RANDOM}"
HOME="${HOME_DIR}" PATH="${SHIM}:${PATH}" PROXLANE_URL="${PROXLANE_TEST_URL}" PROXLANE_API_KEY="${SECRET}" \
    "${REPO}/install.sh" </dev/null >/dev/null 2>&1 || true
[ -s "${SHIM}/argv.log" ] || fail "the shim saw no python calls, so this test proved nothing"
grep -q "configure" "${SHIM}/argv.log" || fail "the shim did not see the configure call"
if grep -q "${SECRET}" "${SHIM}/argv.log"; then
    fail "the key reached a python command line: $(grep "${SECRET}" "${SHIM}/argv.log")"
fi
pass "the key never reaches a python command line"

# 4. Reinstalling over an existing install is clean.
run_install "${PROXLANE_TEST_URL}" "${PROXLANE_TEST_SANDBOX_KEY}" >/dev/null 2>&1 || fail "reinstall failed"
pass "reinstalls over an existing install"

# 5. An unreachable gateway installs anyway and says so.
out="$(run_install "http://127.0.0.1:1" "${PROXLANE_TEST_SANDBOX_KEY}" 2>&1)" || fail "an unreachable gateway made install fail"
echo "${out}" | grep -q "check above failed" || fail "an unreachable gateway was not reported: ${out}"
pass "installs before the gateway exists, and says the check failed"

# 6. With no key and no terminal it refuses rather than writing an empty key.
rm -f "${CONFIG}"
if HOME="${HOME_DIR}" env -u PROXLANE_API_KEY PROXLANE_URL="${PROXLANE_TEST_URL}" "${REPO}/install.sh" </dev/null >/dev/null 2>&1; then
    fail "installed with no key"
fi
[ ! -f "${CONFIG}" ] || fail "wrote a config with no key"
pass "refuses to install without a key"

# 7. Uninstall removes exactly what install created, and runs twice without error.
run_install "${PROXLANE_TEST_URL}" "${PROXLANE_TEST_SANDBOX_KEY}" >/dev/null 2>&1
printf '{"date": "2026-01-01", "fetches": 1}' > "${HOME_DIR}/.config/claude-seo/proxlane-usage.json"
HOME="${HOME_DIR}" "${REPO}/uninstall.sh" >/dev/null || fail "uninstall failed"
[ ! -e "${HOME_DIR}/.claude/skills/seo-proxlane" ] || fail "skill dir left behind"
[ ! -e "${CONFIG}" ] || fail "config left behind"
[ ! -e "${HOME_DIR}/.config/claude-seo/proxlane-usage.json" ] || fail "usage ledger left behind"
[ -d "${HOME_DIR}/.claude/skills/seo" ] || fail "uninstall touched Claude SEO itself"
HOME="${HOME_DIR}" "${REPO}/uninstall.sh" >/dev/null || fail "a second uninstall failed"
pass "uninstalls cleanly, and idempotently"

echo "all installer tests passed"
