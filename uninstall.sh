#!/usr/bin/env bash
# Proxlane integration for Claude SEO: uninstaller. Removes only what install.sh created.
set -euo pipefail

SKILL_DIR="${HOME}/.claude/skills/seo-proxlane"
CONFIG_FILE="${HOME}/.config/claude-seo/proxlane.json"
USAGE_FILE="${HOME}/.config/claude-seo/proxlane-usage.json"

if [ -d "${SKILL_DIR}" ]; then
    rm -rf "${SKILL_DIR}"
    echo "v Removed ${SKILL_DIR}"
else
    echo "  ${SKILL_DIR} not present"
fi

if [ -f "${CONFIG_FILE}" ]; then
    rm -f "${CONFIG_FILE}"
    echo "v Removed ${CONFIG_FILE}"
else
    echo "  ${CONFIG_FILE} not present"
fi

if [ -f "${USAGE_FILE}" ]; then
    rm -f "${USAGE_FILE}"
    echo "v Removed ${USAGE_FILE}"
fi

echo ""
echo "v Uninstalled. Claude SEO itself is unchanged, and so is your gateway."
