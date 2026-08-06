export type ColorMode = 'rgb' | 'bbed';

export type Bounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
};

export type DatasetManifest = {
  name: string;
  sourceFile: string;
  sourcePointCount: number;
  renderedPointCount: number;
  sampleStride: number;
  crs: string;
  origin: [number, number];
  bounds: Bounds;
  positions: string;
  rgbColors: string;
  bbedColors: string;
  buildings: string;
  generatedAt?: string;
};

export type BuildingProperties = Record<string, unknown>;

export type BuildingFeature = {
  type: 'Feature';
  geometry: {
    type: string;
    coordinates: unknown;
  };
  properties: BuildingProperties;
};

export type BuildingsCollection = {
  type: 'FeatureCollection';
  features: BuildingFeature[];
};

export type ViewerDataset = {
  manifest: DatasetManifest;
  positions: Float32Array;
  rgbColors: Uint8Array;
  bbedColors: Uint8Array;
  buildings: BuildingsCollection;
  hasBbedColors: boolean;
};

export type HoverState = {
  feature: BuildingFeature;
  x: number;
  y: number;
} | null;
