"""Wheel-native Windows installer for the Krita bridge payload."""

from .manager import InstallationManager
from .paths import InstallPaths

__all__ = ["InstallPaths", "InstallationManager"]
