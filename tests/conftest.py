"""Pytest bootstrap: make the component's pure modules importable without HA.

``custom_components/solis_local/__init__.py`` imports ``homeassistant``, which
is not installed here. To unit-test the pure modules (parser, models,
api.local) we pre-register the package chain as plain namespace packages, so
``from custom_components.solis_local.parser import ...`` resolves to the real
files without ever executing the HA-importing ``__init__.py``.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_COMPONENT_DIR = Path(__file__).resolve().parent.parent / "custom_components"


def _register_namespace(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module


_register_namespace("custom_components", _COMPONENT_DIR)
_register_namespace("custom_components.solis_local", _COMPONENT_DIR / "solis_local")
_register_namespace(
    "custom_components.solis_local.api", _COMPONENT_DIR / "solis_local" / "api"
)