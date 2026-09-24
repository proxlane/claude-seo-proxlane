import importlib.util
import sys
from pathlib import Path

# The script is installed as a loose file in ~/.claude/skills, not as a package, so the tests
# import it the same way: by path.
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "seo-proxlane" / "proxlane_fetch.py"
spec = importlib.util.spec_from_file_location("proxlane_fetch", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules["proxlane_fetch"] = module
spec.loader.exec_module(module)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """No test may reach the real home directory.

    An early version of the configure test wrote a dummy key into the developer's real
    ~/.config/claude-seo/proxlane.json, because the default path was bound at definition time
    and patching the module attribute did not reach it. That is fixed in the script; this makes
    the whole class impossible here, whatever a future test forgets.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(module, "CONFIG_PATH", home / ".config" / "claude-seo" / "proxlane.json")
    monkeypatch.setattr(module, "USAGE_PATH", home / ".config" / "claude-seo" / "proxlane-usage.json")
    for var in (
        "PROXLANE_URL",
        "PROXLANE_API_KEY",
        "PROXLANE_CONFIGURE_KEY",
        "PROXLANE_DAILY_FETCH_LIMIT",
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)
