# Finished street asset candidates

The following finished Poly Haven models were downloaded and converted with
`scripts/prepare_library_asset_blender.py` on 27 October 2026. Both are CC0;
source URLs, licences, download hashes and conversion hashes are recorded in
the private `asset-quality-pass/library/<id>/source.json` and
`asset-quality-pass/prepared/<id>/prepared.json` files.

| Model | Source | Prepared mesh | Status |
| --- | --- | --- | --- |
| Fire hydrant | [Poly Haven fire hydrant](https://polyhaven.com/a/fire_hydrant) | 86,316 triangles, 0.80 m high | No mapped hydrants were found in the infrastructure query for the current extent. No instance was placed. |
| Concrete road barrier | [Poly Haven concrete road barrier](https://polyhaven.com/a/concrete_road_barrier) | 60,928 triangles, 1.55 m long | A later native report records 38 source-linked instances imported and verified. A model is not evidence of a barrier location. |

The hydrant has not been placed: the current query found no mapped locations.
The barrier was subsequently imported into the private Unreal project. The
saved `finished-barrier-verification.json` reports 38 instances in two groups,
with the corresponding old actors hidden. A later `realism-pass-2-verification.json`
reports 38 visible finished instances after descriptor unload/reload. These
reports predate the later editor startup problem; the current post-recovery
state has not been independently checked.
They are illustrative generic models, not verified matches to local hardware.

To reproduce the downloads without changing the standard library manifest:

```powershell
.\.venv\Scripts\python.exe scripts/fetch_quality_assets.py --output C:/private/dynacity-data/engine/asset-quality-pass/library --only fire_hydrant concrete_road_barrier --resolution 1k
```

Run the existing Blender converter separately for each source glTF, and use
the normal `library_asset_core.read_prepared` licence and digest checks before
any native import. Heights and orientations without mapped evidence remain
illustrative. Never invent hydrants or additional barrier locations to fill
coverage gaps.

`scripts/prepare_mapped_barriers.py` derives display segments only from exported
`jersey_barrier` source lines and retains each OSM identity. Against the existing
greenery-refresh street context it produced 38 segments and no omitted lines,
in `asset-quality-pass/barrier-placements.json`. These units are subdivisions
of mapped line geometry, not surveyed individual barriers. A native importer
must align the authored model bounds centre using the recorded offset, and
replace only corresponding existing source-line geometry to avoid overlapping
the current barrier display. The manifest itself does not change the live scene;
the subsequent native import applied it.
