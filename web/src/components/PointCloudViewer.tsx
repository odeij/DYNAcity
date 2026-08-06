import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import DeckGL from '@deck.gl/react';
import {COORDINATE_SYSTEM, OrbitView} from '@deck.gl/core';
import {GeoJsonLayer, PointCloudLayer} from '@deck.gl/layers';
import type {BuildingFeature, ColorMode, HoverState, ViewerDataset} from '../types';
import {getFeatureId} from '../lib/data';
import {HoverTooltip, MapLegend} from './MapOverlays';

type ViewState = {
  target: [number, number, number];
  zoom: number;
  rotationOrbit: number;
  rotationX: number;
  minRotationX: number;
  maxRotationX: number;
};

type PointCloudViewerProps = {
  dataset: ViewerDataset;
  colorMode: ColorMode;
  selected: BuildingFeature | null;
  fitSignal: number;
  onSelected: (feature: BuildingFeature | null) => void;
};

function fittedView(dataset: ViewerDataset, width: number, height: number): ViewState {
  const {bounds} = dataset.manifest;
  const mapWidth = Math.max(1, bounds.maxX - bounds.minX);
  const mapHeight = Math.max(1, bounds.maxY - bounds.minY);
  const mapDepth = Math.max(1, bounds.maxZ - bounds.minZ);
  const paddedWidth = Math.max(1, width - 120);
  const paddedHeight = Math.max(1, height - 120);
  const zoom =
    Math.log2(Math.min(paddedWidth / mapWidth, paddedHeight / (mapHeight + mapDepth * 1.8))) -
    0.2;

  return {
    target: [
      (bounds.minX + bounds.maxX) / 2,
      (bounds.minY + bounds.maxY) / 2,
      bounds.minZ + mapDepth * 0.28
    ],
    zoom,
    rotationOrbit: -18,
    rotationX: 52,
    minRotationX: 8,
    maxRotationX: 88
  };
}

function addElevation(value: unknown, elevation: number): unknown {
  if (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    typeof value[1] === 'number'
  ) {
    return [value[0], value[1], elevation];
  }
  return Array.isArray(value) ? value.map((item) => addElevation(item, elevation)) : value;
}

export function PointCloudViewer({
  dataset,
  colorMode,
  selected,
  fitSignal,
  onSelected
}: PointCloudViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({width: 900, height: 600});
  const [hover, setHover] = useState<HoverState>(null);
  const [viewState, setViewState] = useState<ViewState>(() => fittedView(dataset, 900, 600));

  useEffect(() => {
    if (!hostRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({width: entry.contentRect.width, height: entry.contentRect.height});
    });
    observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setViewState(fittedView(dataset, size.width, size.height));
  }, [dataset, fitSignal, size.width, size.height]);

  const selectedId = getFeatureId(selected);
  const hoverId = getFeatureId(hover?.feature ?? null);
  const pointColors = colorMode === 'rgb' ? dataset.rgbColors : dataset.bbedColors;

  const elevatedBuildings = useMemo(() => {
    const originZ = dataset.manifest.origin[2] ?? 0;
    const maxRelativeZ = dataset.manifest.bounds.maxZ ?? 0;
    return {
      ...dataset.buildings,
      features: dataset.buildings.features.map((feature) => {
        const absoluteTop = Number(feature.properties.pc_z_max);
        const relativeTop = Number.isFinite(absoluteTop)
          ? Math.max(0.25, Math.min(maxRelativeZ + 1, absoluteTop - originZ + 0.35))
          : 0.25;
        return {
          ...feature,
          geometry: {
            ...feature.geometry,
            coordinates: addElevation(feature.geometry.coordinates, relativeTop)
          }
        };
      })
    };
  }, [dataset]);

  const layers = useMemo(
    () => [
      new PointCloudLayer({
        id: `point-cloud-${colorMode}`,
        data: {
          length: dataset.manifest.renderedPointCount,
          attributes: {
            getPosition: {value: dataset.positions, size: 3},
            getColor: {value: pointColors, size: 3, type: 'unorm8'}
          }
        },
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getNormal: [0, 0, 1],
        pointSize: 1.7,
        material: false,
        opacity: colorMode === 'rgb' ? 0.92 : 0.96,
        parameters: {depthTest: true, depthMask: true} as any
      }),
      new GeoJsonLayer<any>({
        id: 'bbed-footprints-3d',
        data: elevatedBuildings as any,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: true,
        autoHighlight: false,
        filled: true,
        stroked: true,
        lineJointRounded: true,
        getFillColor: (feature: any): [number, number, number, number] => {
          const id = getFeatureId(feature as BuildingFeature);
          if (id === selectedId) return [242, 92, 57, 72];
          if (id === hoverId) return [59, 211, 220, 52];
          return [255, 255, 255, 2];
        },
        getLineColor: (feature: any): [number, number, number, number] => {
          const id = getFeatureId(feature as BuildingFeature);
          if (id === selectedId) return [255, 111, 78, 255];
          if (id === hoverId) return [86, 231, 236, 255];
          return [181, 212, 223, 58];
        },
        getLineWidth: (feature: any): number => {
          const id = getFeatureId(feature as BuildingFeature);
          return id === selectedId ? 2.6 : id === hoverId ? 2.2 : 0.65;
        },
        lineWidthUnits: 'pixels',
        lineWidthMinPixels: 0.5,
        parameters: {depthTest: false} as any,
        updateTriggers: {
          getFillColor: [selectedId, hoverId],
          getLineColor: [selectedId, hoverId],
          getLineWidth: [selectedId, hoverId]
        },
        onHover: (info: {object?: BuildingFeature; x: number; y: number}) => {
          setHover(info.object ? {feature: info.object, x: info.x, y: info.y} : null);
        },
        onClick: (info: {object?: BuildingFeature}) => {
          if (info.object) onSelected(info.object);
        }
      })
    ],
    [colorMode, dataset, elevatedBuildings, hoverId, onSelected, pointColors, selectedId]
  );

  const handleViewStateChange = useCallback(({viewState: next}: any) => {
    const target = next.target ?? [0, 0, 0];
    setViewState((current) => ({
      ...current,
      ...next,
      target: [target[0], target[1], target[2] ?? 0],
      zoom: next.zoom ?? current.zoom,
      rotationOrbit: next.rotationOrbit ?? current.rotationOrbit,
      rotationX: next.rotationX ?? current.rotationX
    }));
  }, []);

  const elevationRange = dataset.manifest.bounds.maxZ - dataset.manifest.bounds.minZ;

  return (
    <div className="viewer" ref={hostRef}>
      <DeckGL
        views={
          new OrbitView({
            id: 'scene-3d',
            orbitAxis: 'Z',
            fovy: 45,
            near: 0.1,
            far: 2000,
            controller: {
              dragMode: 'rotate',
              dragRotate: true,
              dragPan: true,
              scrollZoom: {smooth: true},
              touchRotate: true,
              keyboard: true,
              inertia: 180
            } as any
          })
        }
        viewState={viewState}
        onViewStateChange={handleViewStateChange}
        layers={layers}
        pickingRadius={3}
        onClick={(info) => {
          if (!info.object) onSelected(null);
        }}
        getCursor={({isDragging, isHovering}) =>
          isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'
        }
      />
      <div className="top-view-badge">
        <span>3D</span>
        Orbit view · CRS {dataset.manifest.crs}
      </div>
      <HoverTooltip hover={hover} />
      <MapLegend colorMode={colorMode} elevationRange={elevationRange} />
    </div>
  );
}
