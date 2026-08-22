"""The one place the monorepo's version is written.

Every member's ``pyproject.toml`` carries the same number and semantic-release bumps them together, but
code should not read a version out of package metadata: that requires the distribution to be installed
under the name the reader guessed, and there are nine of them. This module is the single source.
"""

from __future__ import annotations

__version__ = "0.5.0"
"""The version of every vitruvio distribution in this repository."""
