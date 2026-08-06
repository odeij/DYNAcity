import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import DeckGL from '@deck.gl/react';
import {COORDINATE_SYSTEM, OrthographicView} from '@deck.gl/core';
import {GeoJsonLayer, PointCloudLayer} from '@deck.gl/layers';
import type {ColorMode, HoverState, ViewerDataset, BuildingFeature} from '../types';
import {getFeatureId} from '../lib/data';
import {HoverTooltip, MapLegend} from './MapOverlays';

type ViewState = {target: [number, number, number]; zoom: number};

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
  const paddedWidth = Math.max(1, width - 120);
  const paddedHeight = Math.max(1, height - 120);
  const zoom = Math.log2(Math.min(paddedWidth / mapWidth, paddedHeight / mapHeight));
  return {
    target: [(bounds.minX + bounds.maxX) / 2, (bounds.minY + bounds.maxY) / 2, 0],
    zoom
  };
}

function niceScale(zoom: number): {metres: number; pixels: number} {
  const pixelsPerMetre = 2 ** zoom;
  const targetMetres = 90 / Math.max(pixelsPerMetre, 0.0001);
  const exponent = 10 ** Math.floor(Math.log10(targetMetres));
  const normalized = targetMetres / exponent;
  const factor = normalized < 2 ? 1 : normalized < 5 ? 2 : 5;
  const metres = factor * exponent;
  return {metres, pixels: metres * pixelsPerMetre};
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
  }, [dataset, fitSignal]);

  const selectedId = getFeatureId(selected);
  const hoverId = getFeatureId(hover?.feature ?? null);
  const pointColors = colorMode === 'rgb' ? dataset.rgbColors : dataset.bbedColors;

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
        pointSize: 1.45,
        material: false,
        opacity: colorMode === 'rgb' ? 0.92 : 0.96,
        parameters: {depthTest: false}
      }),
      new GeoJsonLayer<any>({
        id: 'bbed-footprints',
        data: dataset.buildings as any,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        pickable: true,
        autoHighlight: false,
        filled: true,
        stroked: true,
        lineJointRounded: true,
        getFillColor: (feature: any): [number, number, number, number] => {
          const id = getFeatureId(feature as BuildingFeature);
          if (id === selectedId) return [242, 92, 57, 62];
          if (id === hoverId) return [59, 211, 220, 45];
          return [255, 255, 255, 2];
        },
        getLineColor: (feature: any): [number, number, number, number] => {
          const id = getFeatureId(feature as BuildingFeature);
          if (id === selectedId) return [255, 111, 78, 255];
          if (id === hoverId) return [86, 231, 236, 255];
          return [181, 212, 223, 64];
        },
        getLineWidth: (feature: any): number => {
          const id = getFeatureId(feature as BuildingFeature);
          return id === selectedId ? 2.4 : id === hoverId ? 2 : 0.65;
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
    [dataset, pointColors, colorMode, selectedId, hoverId, onSelected]
  );

  const handleViewStateChange = useCallback(({viewState: next}: any) => {
    const target = next.target ?? [0, 0, 0];
    setViewState({
      target: [target[0], target[1], target[2] ?? 0],
      zoom: next.zoom
    });
  }, []);
  const scale = niceScale(viewState.zoom);

  return (
    <div className="viewer" ref={hostRef}>
      <DeckGL
        views={new OrthographicView({id: 'top', controller: true, flipY: false})}
        viewState={viewState}
        onViewStateChange={handleViewStateChange}
        layers={layers}
        controller={{dragPan: true, scrollZoom: true, doubleClickZoom: true, keyboard: true}}
        onClick={(info) => {
          if (!info.object) onSelected(null);
        }}
        getCursor={({isDragging, isHovering}) =>
          isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'
        }
      />
      <div className="top-view-badge">
        <span>N</span>
        Top view · CRS {dataset.manifest.crs}
      </div>
      <HoverTooltip hover={hover} />
      <MapLegend colorMode={colorMode} scaleMetres={scale.metres} scalePixels={scale.pixels} />
    </div>
  );
}
