"""Runtime configuration with private data kept outside the repository.

Locates the two private roots — raw/derived data and generated artifacts — and
enforces that neither lives inside the working tree.

The `.gitignore` patterns are a second line of defence, not the first: they can
be bypassed with `git add -f`, and they do nothing about a file being written
somewhere unexpected in the first place. `validate_private_roots` makes the
boundary a startup failure instead of a review-time catch.

Nothing here has a usable default for the roots. Requiring them to be set
explicitly means a fresh checkout cannot silently start writing BBED extracts or
trained models into the repository.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BBED_LAYER_URL = (
    "https://services3.arcgis.com/tuNLpt6Wfhd22qmO/ArcGIS/rest/services/"
    "BBED2024_/FeatureServer/1"
)


@dataclass(frozen=True)
class Settings:
    data_root: Path
    artifact_root: Path
    source_crs: str = "EPSG:32636"
    las_acquisition_year: int = 2020
    bbed_layer_url: str = DEFAULT_BBED_LAYER_URL

    @classmethod
    def from_env(cls) -> "Settings":
        data_root = os.environ.get("DYNACITY_DATA_ROOT")
        artifact_root = os.environ.get("DYNACITY_ARTIFACT_ROOT")
        if not data_root or not artifact_root:
            raise ValueError(
                "DYNACITY_DATA_ROOT and DYNACITY_ARTIFACT_ROOT must point to "
                "private locations outside the Git repository."
            )
        return cls(
            data_root=Path(data_root),
            artifact_root=Path(artifact_root),
            source_crs=os.environ.get("DYNACITY_SOURCE_CRS", "EPSG:32636"),
            las_acquisition_year=int(
                os.environ.get("DYNACITY_LAS_ACQUISITION_YEAR", "2020")
            ),
            bbed_layer_url=os.environ.get(
                "DYNACITY_BBED_LAYER_URL", DEFAULT_BBED_LAYER_URL
            ),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "Settings":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            data_root=Path(payload["data_root"]),
            artifact_root=Path(payload["artifact_root"]),
            source_crs=payload.get("source_crs", "EPSG:32636"),
            las_acquisition_year=int(payload.get("las_acquisition_year", 2020)),
            bbed_layer_url=payload.get(
                "bbed_layer_url", DEFAULT_BBED_LAYER_URL
            ),
        )

    def validate_private_roots(self, repository_root: str | Path) -> None:
        """Refuse roots that resolve inside the repository.

        Paths are resolved before comparison so symlinks and `..` segments
        cannot smuggle a root back inside the tree. Checking `repo in
        resolved.parents` catches nesting at any depth, not just direct
        children.
        """

        repo = Path(repository_root).resolve()
        for label, path in (
            ("data_root", self.data_root),
            ("artifact_root", self.artifact_root),
        ):
            resolved = path.resolve()
            if resolved == repo or repo in resolved.parents:
                raise ValueError(f"{label} must be outside the repository: {resolved}")

