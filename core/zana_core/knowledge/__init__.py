"""Knowledge intake, parsing, embeddings, and persistent local retrieval.

Optional provider dependencies (Docling and LanceDB) are imported lazily inside
their owning modules, so importing this package never fails when they are
absent.  Availability is reported honestly through ``BackendUnavailableError``
and ``PARSER_UNAVAILABLE`` states rather than fabricated success.
"""

from zana_core.knowledge.docling import DoclingParser
from zana_core.knowledge.lancedb_index import (
    IndexCorruptionError,
    IndexError,
    IndexIncompatibleError,
    IndexNotFoundError,
    LanceDBIndex,
)
from zana_core.knowledge.snapshots import (
    build_index_identity,
    chunk_config_digest,
    read_snapshot,
    write_snapshot,
)

__all__ = [
    "DoclingParser",
    "IndexCorruptionError",
    "IndexError",
    "IndexIncompatibleError",
    "IndexNotFoundError",
    "LanceDBIndex",
    "build_index_identity",
    "chunk_config_digest",
    "read_snapshot",
    "write_snapshot",
]
