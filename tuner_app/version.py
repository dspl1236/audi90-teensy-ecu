"""
version.py — Single source of truth for app version.
Bump this and everything else picks it up automatically.

Versioning convention:
  MAJOR.MINOR.PATCH
  MAJOR — manual decision only (breaking change / milestone release)
  MINOR — new features added (new tab, new tool, new capability)
  PATCH — bug fixes and corrections only

Examples:
  1.2.3 -> 1.2.4   fix: map size 256->288
  1.2.3 -> 1.3.0   feat: add datalog export
  1.2.3 -> 2.0.0   [manual] first physical ECU test
"""

APP_VERSION  = "1.6.3"
APP_NAME     = f"Audi90Tuner-v{APP_VERSION}"
WINDOW_TITLE = f"7A 20v Tuner  v{APP_VERSION}"
