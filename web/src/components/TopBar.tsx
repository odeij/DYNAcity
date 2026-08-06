import {Focus, FolderOpen, Layers3} from 'lucide-react';
import type {ColorMode, DatasetManifest} from '../types';

type TopBarProps = {
  manifest: DatasetManifest | null;
  colorMode: ColorMode;
  hasBbedColors: boolean;
  onColorModeChange: (mode: ColorMode) => void;
  onFit: () => void;
  onOpenFiles: () => void;
};

const compactNumber = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumFractionDigits: 1
});

export function TopBar({
  manifest,
  colorMode,
  hasBbedColors,
  onColorModeChange,
  onFit,
  onOpenFiles
}: TopBarProps) {
  return (
    <header className="topbar">
      <div className="brand" aria-label="DYNAcity Explorer">
        <span className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="brand-copy">
          <strong>DYNAcity</strong>
          <small>Explorer</small>
        </span>
      </div>

      <div className="dataset-summary" aria-live="polite">
        <span className="dataset-dot" />
        <span>
          <strong>{manifest?.name ?? 'Loading dataset'}</strong>
          {manifest && (
            <small>
              {compactNumber.format(manifest.sourcePointCount)} points ·{' '}
              {compactNumber.format(manifest.renderedPointCount)} rendered
            </small>
          )}
        </span>
      </div>

      <div className="topbar-actions">
        <button className="quiet-button" type="button" onClick={onFit}>
          <Focus size={16} />
          <span>Fit view</span>
        </button>

        <div className="mode-switch" aria-label="Point color" role="group">
          <Layers3 size={15} aria-hidden="true" />
          <button
            type="button"
            aria-pressed={colorMode === 'rgb'}
            onClick={() => onColorModeChange('rgb')}
          >
            RGB
          </button>
          <button
            type="button"
            aria-pressed={colorMode === 'bbed'}
            disabled={!hasBbedColors}
            title={!hasBbedColors ? 'Prepare the file first to enable BBED colors' : undefined}
            onClick={() => onColorModeChange('bbed')}
          >
            BBED
          </button>
        </div>

        <button className="primary-button" type="button" onClick={onOpenFiles}>
          <FolderOpen size={16} />
          <span>Open files</span>
        </button>
      </div>
    </header>
  );
}
