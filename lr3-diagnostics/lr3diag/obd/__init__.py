"""OBD-II communication through an ELM327-compatible adapter (USB, Bluetooth or WiFi)."""

from .elm327 import ELM327, open_connection  # noqa: F401
from .pids import PIDS, DEFAULT_LOG_PIDS  # noqa: F401
