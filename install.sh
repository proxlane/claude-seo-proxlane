#!/usr/bin/env bash
# Proxlane integration for Claude SEO: installer.
#
# Installs the seo-proxlane skill and records where your Proxlane gateway is and its key.
# Everything lives in main() so a truncated download cannot run half an installer.
#
# Non-interactive use: set PROXLANE_URL and PROXLANE_API_KEY in the environment and they are
# used instead of prompting. Useful for dotfiles and CI; the key still never touches argv.
set -euo pipefail

main() {
    SKILL_DIR="${HOME}/.claude/skills/seo-proxlane"
    SEO_SKILL_DIR="${HOME}/.claude/skills/seo"
    # Beside Claude SEO's own extension credentials (matomo.json lives here too), and NOT in
    # ~/.claude/settings.json, which other tooling reads, prints and syncs.
    CONFIG_FILE="${HOME}/.config/claude-seo/proxlane.json"

    echo "Proxlane integration for Claude SEO"
    echo ""

    if [ -d "${SEO_SKILL_DIR}" ]; then
        echo "v Claude SEO detected"
    else
        # A soft dependency. The skill works on its own; it is written to cooperate with
        # Claude SEO's audit skills, and a plugin install of Claude SEO lives elsewhere.
        echo "! Claude SEO not found at ${SEO_SKILL_DIR}."
        echo "  The skill still works standalone. Install Claude SEO for the audit integration:"
        echo "  https://github.com/AgriciDaniel/claude-seo"
    fi

    # 3.9, not Claude SEO's own 3.10: this runs under whatever `python3` is on PATH, and on
    # macOS that is the system 3.9. The script is standard library only and CI runs it on 3.9,
    # so the floor is what it actually needs rather than what its host project needs.
    if ! command -v python3 >/dev/null 2>&1; then
        echo "x Python 3.9+ is required and python3 was not found."
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
        echo "x Python 3.9+ is required (found $(python3 -V 2>&1))."
        exit 1
    fi
    echo "v $(python3 -V 2>&1) detected"

    SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
    if [ ! -f "${SOURCE_DIR}/skills/seo-proxlane/SKILL.md" ]; then
        echo "x Cannot find the skill files next to this script."
        echo "  Run it from a clone: git clone https://github.com/proxlane/claude-seo-proxlane"
        exit 1
    fi

    GATEWAY_URL="${PROXLANE_URL:-}"
    if [ -z "${GATEWAY_URL}" ]; then
        if [ -t 0 ]; then
            read -rp "Gateway URL [http://localhost:8787]: " GATEWAY_URL
        fi
        GATEWAY_URL="${GATEWAY_URL:-http://localhost:8787}"
    fi

    GATEWAY_KEY="${PROXLANE_API_KEY:-}"
    if [ -z "${GATEWAY_KEY}" ]; then
        if [ ! -t 0 ]; then
            echo "x No terminal to prompt on, and PROXLANE_API_KEY is not set."
            exit 1
        fi
        echo ""
        echo "The gateway's key: the PROXLANE_API_KEY your gateway was started with."
        echo "Not a ScraperAPI or Firecrawl key. Those stay on the gateway."
        read -rsp "Gateway key: " GATEWAY_KEY
        echo ""
    fi
    if [ -z "${GATEWAY_KEY}" ]; then
        echo "x The key cannot be empty."
        exit 1
    fi

    echo ""
    echo "-> Installing the skill to ${SKILL_DIR}"
    mkdir -p "${SKILL_DIR}"
    cp "${SOURCE_DIR}/skills/seo-proxlane/SKILL.md" "${SKILL_DIR}/SKILL.md"
    cp "${SOURCE_DIR}/skills/seo-proxlane/proxlane_fetch.py" "${SKILL_DIR}/proxlane_fetch.py"
    cp "${SOURCE_DIR}/LICENSE" "${SKILL_DIR}/LICENSE"

    echo "-> Writing ${CONFIG_FILE}"
    # The key travels in the environment, never argv, where any user on the machine could read
    # it from the process list. The script writes the file; see write_config() for the rest.
    PROXLANE_CONFIGURE_KEY="${GATEWAY_KEY}" python3 "${SKILL_DIR}/proxlane_fetch.py" configure --url "${GATEWAY_URL}"

    echo ""
    # Not fatal. Installing before the gateway is started is a reasonable order of work, and
    # the check says exactly what is wrong either way.
    if python3 "${SKILL_DIR}/proxlane_fetch.py" check; then
        echo ""
        echo "v Installed."
    else
        echo ""
        echo "! Installed, but the check above failed. Fix what it says, then run:"
        echo "  python3 ${SKILL_DIR}/proxlane_fetch.py check"
    fi
    echo ""
    echo "In Claude Code:  /seo-proxlane fetch https://example.com"
    echo "Uninstall:       ./uninstall.sh"
}

main "$@"
