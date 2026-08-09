"""Non-invasive executable discovery for installed local runtimes."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping

from zana_core.domain.enums import RuntimeKind

# Maps a runtime family to executable names checked on PATH. `mlx_lm` is
# detected only when a real command exists; no Python import or memory-heavy
# environment inspection is performed.
RUNTIME_EXECUTABLES: Mapping[RuntimeKind, tuple[str, ...]] = {
    RuntimeKind.OLLAMA: ("ollama",),
    RuntimeKind.LM_STUDIO: ("lms",),
    RuntimeKind.LLAMA_CPP: ("llama-server",),
    RuntimeKind.MLX_LM: ("mlx_lm",),
}


class ExecutableDiscovery:
    """Reports whether a runtime executable is present on PATH."""

    def __init__(
        self,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._which = which

    def find(self, kind: RuntimeKind) -> str | None:
        for name in RUNTIME_EXECUTABLES.get(kind, ()):
            resolved = self._which(name)
            if resolved:
                return resolved
        return None

    def installed(self, kind: RuntimeKind) -> bool:
        return self.find(kind) is not None

    def installed_kinds(self) -> dict[RuntimeKind, str | None]:
        return {kind: self.find(kind) for kind in RUNTIME_EXECUTABLES}
