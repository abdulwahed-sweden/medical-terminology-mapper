"""SNOMED CT adapter -- interface only, deliberately without content.

SNOMED CT content requires an affiliate licence. Sweden is a member country and
the national licence is administered by the national release centre (see
LICENSING.md for the responsible authority and the date checked), but the
licence is granted to the user, not to this repository, so no SNOMED CT content
is shipped here and no loader is implemented in Phase 1.

The adapter exists anyway because the adapter boundary is the point: adding
SNOMED CT later must mean writing `load`, not restructuring the project.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from app.terminology.base import Concept, TerminologyLicenceRequired

# SNOMED CT identifiers are 6-18 digits, the last being a check digit
# (Verhoeff). Only the shape is checked here; verifying the check digit without
# any content to validate against would be a false assurance.
SNOMED_SCTID_RE = re.compile(r"^[0-9]{6,18}$")

_LICENCE_MESSAGE = (
    "SNOMED CT content is not shipped with this repository and no loader is "
    "implemented in Phase 1. SNOMED CT requires an affiliate licence, obtained "
    "through the Swedish national release centre. See LICENSING.md for the "
    "responsible authority, the licence route, and the date the information was "
    "checked. Implementing this loader is Phase 4."
)


class SnomedCT:
    system_id = "snomed"

    def load(self, path: Path, version: str) -> Iterable[Concept]:
        raise TerminologyLicenceRequired(_LICENCE_MESSAGE)

    def validate_code_format(self, code: str) -> bool:
        return bool(SNOMED_SCTID_RE.match(code.strip()))
