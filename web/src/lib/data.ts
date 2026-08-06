import type {
  BuildingFeature,
  BuildingsCollection,
  DatasetManifest,
  ViewerDataset
} from '../types';

const DATA_ROOT = '/data';

async function fetchChecked(path: string): Promise<Response> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Could not load ${path} (${response.status})`);
  }
  return response;
}

export async function loadPreparedDataset(): Promise<ViewerDataset> {
  const manifestResponse = await fetchChecked(`${DATA_ROOT}/manifest.json`);
  const manifest = (await manifestResponse.json()) as DatasetManifest;
  const [positionsResponse, rgbResponse, bbedResponse, buildingsResponse] =
    await Promise.all([
      fetchChecked(`${DATA_ROOT}/${manifest.positions}`),
      fetchChecked(`${DATA_ROOT}/${manifest.rgbColors}`),
      fetchChecked(`${DATA_ROOT}/${manifest.bbedColors}`),
      fetchChecked(`${DATA_ROOT}/${manifest.buildings}`)
    ]);

  const [positionsBuffer, rgbBuffer, bbedBuffer, buildings] = await Promise.all([
    positionsResponse.arrayBuffer(),
    rgbResponse.arrayBuffer(),
    bbedResponse.arrayBuffer(),
    buildingsResponse.json() as Promise<BuildingsCollection>
  ]);

  return {
    manifest,
    positions: new Float32Array(positionsBuffer),
    rgbColors: new Uint8Array(rgbBuffer),
    bbedColors: new Uint8Array(bbedBuffer),
    buildings,
    hasBbedColors: true
  };
}

function translateCoordinates(value: unknown, originX: number, originY: number): unknown {
  if (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    typeof value[1] === 'number'
  ) {
    return [value[0] - originX, value[1] - originY, ...value.slice(2)];
  }
  if (Array.isArray(value)) {
    return value.map((item) => translateCoordinates(item, originX, originY));
  }
  return value;
}

function toByteColors(source: ArrayLike<number> | undefined, pointCount: number): Uint8Array {
  if (!source || source.length < pointCount * 3) {
    return new Uint8Array(pointCount * 3).fill(188);
  }
  const output = new Uint8Array(pointCount * 3);
  let maximum = 0;
  for (let index = 0; index < Math.min(source.length, 3000); index += 1) {
    maximum = Math.max(maximum, Number(source[index]));
  }
  const divisor = maximum > 255 ? 256 : 1;
  for (let index = 0; index < output.length; index += 1) {
    output[index] = Math.max(0, Math.min(255, Math.round(Number(source[index]) / divisor)));
  }
  return output;
}

type LasAttribute = {value: ArrayLike<number>};
type LasResult = {
  attributes?: Record<string, LasAttribute>;
  header?: {vertexCount?: number};
};

export async function loadLocalDataset(
  lasFile: File,
  geoJsonFile: File
): Promise<ViewerDataset> {
  const [{load}, {LASLoader}] = await Promise.all([
    import('@loaders.gl/core'),
    import('@loaders.gl/las')
  ]);
  const [parsed, rawBuildings] = await Promise.all([
    load(lasFile, LASLoader, {las: {skip: 4, colorDepth: 'auto'}}) as Promise<LasResult>,
    geoJsonFile.text().then((text) => JSON.parse(text) as BuildingsCollection)
  ]);
  const positionAttribute = parsed.attributes?.POSITION;
  if (!positionAttribute) {
    throw new Error('The LAS file did not expose XYZ positions. LAS 1.2 or 1.3 is recommended.');
  }

  const source = positionAttribute.value;
  const pointCount = Math.floor(source.length / 3);
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let minZ = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let maxZ = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < pointCount; index += 1) {
    const x = Number(source[index * 3]);
    const y = Number(source[index * 3 + 1]);
    const z = Number(source[index * 3 + 2]);
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    minZ = Math.min(minZ, z);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
    maxZ = Math.max(maxZ, z);
  }

  const positions = new Float32Array(pointCount * 3);
  for (let index = 0; index < pointCount; index += 1) {
    positions[index * 3] = Number(source[index * 3]) - minX;
    positions[index * 3 + 1] = Number(source[index * 3 + 1]) - minY;
    positions[index * 3 + 2] = Number(source[index * 3 + 2]) - minZ;
  }

  const colorAttribute = parsed.attributes?.COLOR_0;
  const rgbColors = toByteColors(colorAttribute?.value, pointCount);
  const bbedColors = new Uint8Array(pointCount * 3);
  for (let index = 0; index < pointCount; index += 1) {
    bbedColors[index * 3] = 34;
    bbedColors[index * 3 + 1] = 49;
    bbedColors[index * 3 + 2] = 62;
  }

  const buildings: BuildingsCollection = {
    ...rawBuildings,
    features: rawBuildings.features.map((feature: BuildingFeature) => ({
      ...feature,
      geometry: {
        ...feature.geometry,
        coordinates: translateCoordinates(feature.geometry.coordinates, minX, minY)
      }
    }))
  };
  const sourceCount = parsed.header?.vertexCount ?? pointCount * 4;
  const manifest: DatasetManifest = {
    name: lasFile.name.replace(/\.(las|laz)$/i, ''),
    sourceFile: lasFile.name,
    sourcePointCount: sourceCount,
    renderedPointCount: pointCount,
    sampleStride: Math.max(1, Math.round(sourceCount / Math.max(pointCount, 1))),
    crs: 'EPSG:32636',
    origin: [minX, minY, minZ],
    bounds: {
      minX: 0,
      minY: 0,
      minZ: 0,
      maxX: maxX - minX,
      maxY: maxY - minY,
      maxZ: maxZ - minZ
    },
    positions: '',
    rgbColors: '',
    bbedColors: '',
    buildings: ''
  };

  return {manifest, positions, rgbColors, bbedColors, buildings, hasBbedColors: false};
}

export function getFeatureId(feature: BuildingFeature | null): string {
  if (!feature) return '';
  const id = feature.properties.dynacity_match_id;
  return id === null || id === undefined ? '' : String(id);
}
