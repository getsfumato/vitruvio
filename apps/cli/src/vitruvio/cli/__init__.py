"""The vitruvio CLI: parse, delegate to the service layer, render.

Three responsibilities and no fourth. Anything that looks like knowledge about brains rather than about
terminals belongs in ``vitruvio.runtime``, where the MCP server and the HTTP API can reach it too. The rule
is enforced in CI: this package may import ``vitruvio.runtime`` and ``vitruvio.kernel``, and may never
import ``boltzmann`` -- if it needs an SDK type, the seam is in the wrong place.

The renderer never joins matches into a sentence. The brain returns evidence and the caller writes the
prose; a CLI that summarised for you would be a CLI that had quietly become the model.
"""

from __future__ import annotations
