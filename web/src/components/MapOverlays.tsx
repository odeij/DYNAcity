import {MousePointer2} from 'lucide-react';
import type {ColorMode, HoverState} from '../types';

type HoverTooltipProps = {hover: HoverState};

function compactValue(value: unknown, suffix = ''): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') {
    return `${new Intl.NumberFormat('en', {maximumFractionDigits: 1}).format(value)}${suffix}`;
  }
  return String(value);
}

export function HoverTooltip({hover}: HoverTooltipProps) {
  if (!hover) return null;
  const {properties} = hover.feature;
  return (
    <div
      className="hover-tooltip"
      style={{left: hover.x + 16, top: hover.y + 16}}
      role="status"
    >
      <p>{String(properties.ParcelID ?? `Building ${properties.dynacity_match_id ?? ''}`)}</p>
      <dl>
        <div>
          <dt>Use</dt>
          <dd>{compactValue(properties.Building_Use)}</dd>
        </div>
        <div>
          <dt>Floors</dt>
          <dd>{compactValue(properties.NoofFloor)}</dd>
        </div>
        <div>
          <dt>BBED height</dt>
          <dd>{compactValue(properties.Building_Hight_m, ' m')}</dd>
        </div>
        <div>
          <dt>Matched points</dt>
          <dd>{compactValue(properties.pc_point_count)}</dd>
        </div>
      </dl>
      <span>Click for complete record</span>
    </div>
  );
}

type MapLegendProps = {
  colorMode: ColorMode;
  elevationRange: number;
};

export function MapLegend({colorMode, elevationRange}: MapLegendProps) {
  return (
    <div className="map-meta">
      <div className="map-instruction">
        <MousePointer2 size={14} />
        Drag to orbit · Shift-drag to pan · Scroll to zoom
      </div>
      <div className="legend-row">
        <span className="legend-item">
          <i className="legend-swatch hover" /> Hover
        </span>
        <span className="legend-item">
          <i className="legend-swatch selected" /> Selected
        </span>
        <span className="legend-item">
          <i className={`legend-swatch ${colorMode}`} />{' '}
          {colorMode === 'rgb' ? 'RGB points' : 'BBED IDs'}
        </span>
      </div>
      <div
        className="elevation-range"
        aria-label={`${elevationRange.toFixed(1)} metre relative elevation range`}
      >
        <span>Relative elevation</span>
        <strong>0–{elevationRange.toFixed(1)} m</strong>
      </div>
    </div>
  );
}
