# Street twin material audit

Read-only audit, 29 October 2026. No Unreal assets, actors, materials or settings
were changed by this audit.

## Evidence

- Repository and installed `Content/Python/style_city.py` create world-projected
  albedo and roughness using a custom expression, but do not load normal maps.
- Existing CC0 packages are local in
  `C:/private/dynacity-data/assets-inbox/ambientCG/`: `Asphalt012`, `Concrete030`
  and `Plaster001`. Each includes Color, Roughness, NormalDX and NormalGL JPEGs.
  The local manifest records licences and archive hashes. Poly Haven's local
  `plastered_stone_wall` package also includes diffuse, roughness and DirectX normal.
- `Saved/DynaCity/city-finishes.json` records CityFinishes assignments to city
  actors. The actor labels use underscores as expected by `style_city.apply`;
  hyphens in actor object paths are not a label-selection defect.
- Historical project log
  `DynaCityBeirut-backup-2026.10.28-21.50.47.log`, lines 15255–15277, reports
  `M_Pavement` failing PCD3D_SM6 compilation with
  `(Node ComponentMask) Missing ComponentMask input`. These may be intermediate
  graph-construction recompiles; they do not establish the saved material's
  present status. The current session log did not reproduce this error during
  the read-only check.
- The proposed VertexNormalWS stage-restriction diagnosis is unconfirmed.
  Installed Unreal 5.8 source `HLSLMaterialTranslator.cpp`, function
  `FHLSLMaterialTranslator::VertexNormal`, explicitly supports nonvertex shader
  stages using `Parameters.TangentToWorld[2]`. An interpolator alone is therefore
  not an evidence-backed fix for the observed checker appearance.

## Smallest repair to verify

Inspect the assigned material's actual graph, especially each ComponentMask's
connected input. Use `MaterialEditingLibrary.get_material_expression_input_names`,
`get_material_expression_output_names` and `get_inputs_for_material_expression`
instead of assuming empty pin names work for every expression. Check every
connection result, recompile, and call `get_statistics` before interpreting a
capture. Installed `MaterialEditingLibrary.cpp` shows that `get_statistics`
calls `FinishCompilation`; a successful save alone is not proof of shader success.
Check fresh log messages and material usage flags for the representative mesh.

## World-projected PBR road material

Build and verify one new material before assigning it across the city:

1. Import existing Asphalt012 Color as sRGB, Roughness as linear, and NormalDX
   with normal-map compression, sRGB disabled, and no green-channel flip.
2. Use installed engine material functions:
   `/Engine/Functions/Engine_MaterialFunctions01/Texturing/WorldAlignedTexture`
   and `/Engine/Functions/Engine_MaterialFunctions01/Texturing/WorldAlignedNormal`.
   Their local assets were verified. Set each function on a material-function-call
   node, enumerate its available pins, and wire the correct texture object and
   a common TextureSize vector, initially 400 centimetres per repeat.
3. Connect the texture projections to Base Color and Roughness. Feed the
   WorldAlignedNormal world-space result to Normal with tangent-space normal
   disabled on the material. Do not use PixelNormalWS to drive its own normal
   projection. Keep metallic zero and avoid displacement while checking continuity.
4. Verify the appropriate Nanite/instanced usage flags for the actual mesh and
   save only after compilation completes. Inspect lit and unlit views on one
   street, then restore/reload and confirm the assigned material persists.

These textures are licensed illustrative finishes. Their physical scale, road
appearance and generic facade patterns are not measured Beirut observations.

## How I did it

Compared repository and installed Python, inspected asset manifests and texture
files, searched current and historical Unreal logs, and checked installed Unreal
5.8 C++ implementations and engine material-function assets. Live Unreal
verification remains with the single editor owner.
