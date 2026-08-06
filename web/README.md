# DYNAcity Explorer

Interactive 3D web viewer for the Task 2 augmented point cloud and registered BBED building footprints.

## Run it

From this `web` directory:

```powershell
npm install
python scripts/prepare_web_data.py
npm run dev
```

Then open `http://127.0.0.1:4173`.

The preparation step samples the large LAS into browser-friendly binary buffers. It does not change
`enriched.las`; selection and hover still use the complete building footprint GeoJSON. Use **RGB** to
see the original point colors and **BBED** to color points by their assigned building ID.

Drag to orbit around the point cloud, shift-drag to pan, and use the mouse wheel to zoom. **Fit view**
restores the default angled 3D camera. Point elevations are normalized from the LAS minimum Z value,
so the original vertical differences are preserved without losing coordinate precision.

You can also use **Open files** in the interface to choose another LAS/LAZ and registered GeoJSON
without uploading either file to a server.
