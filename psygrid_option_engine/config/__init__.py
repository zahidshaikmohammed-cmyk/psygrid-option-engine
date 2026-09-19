"""Environment-driven settings and IST session-window rules.

No credentials are hard-coded here; secrets (if any are ever required) come
from environment variables only. See docs/CONFIG.md and docs/SAFETY.md.
"""

from psygrid_option_engine.config.session import SessionPhase, SessionWindow
from psygrid_option_engine.config.settings import EndpointCriticality, Settings, get_settings

__all__ = [
    "EndpointCriticality",
    "SessionPhase",
    "SessionWindow",
    "Settings",
    "get_settings",
]
