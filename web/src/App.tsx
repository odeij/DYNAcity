import {useCallback, useEffect, useState} from 'react';
import {AlertTriangle, LoaderCircle} from 'lucide-react';
import type {BuildingFeature, ColorMode, ViewerDataset} from './types';
import {loadPreparedDataset} from './lib/data';
import {TopBar} from './components/TopBar';
import {PointCloudViewer} from './components/PointCloudViewer';
import {BuildingInspector} from './components/BuildingInspector';
import {OpenFilesDialog} from './components/OpenFilesDialog';

export default function App() {
  const [dataset, setDataset] = useState<ViewerDataset | null>(null);
  const [selected, setSelected] = useState<BuildingFeature | null>(null);
  const [colorMode, setColorMode] = useState<ColorMode>('rgb');
  const [fitSignal, setFitSignal] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    loadPreparedDataset()
      .then((loaded) => {
        if (!active) return;
        setDataset(loaded);
        const preferred = loaded.buildings.features.find(
          (feature) => feature.properties.dynacity_match_id === 13
        );
        const firstMatched = loaded.buildings.features.find(
          (feature) => Number(feature.properties.pc_point_count ?? 0) > 0
        );
        setSelected(preferred ?? firstMatched ?? null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Dataset failed to load.');
      });
    return () => {
      active = false;
    };
  }, []);

  const handleSelected = useCallback((feature: BuildingFeature | null) => {
    setSelected(feature);
  }, []);

  function handleLocalDataset(loaded: ViewerDataset) {
    setDataset(loaded);
    setSelected(loaded.buildings.features[0] ?? null);
    setColorMode('rgb');
    setFitSignal((value) => value + 1);
  }

  return (
    <div className="app-shell">
      <TopBar
        manifest={dataset?.manifest ?? null}
        colorMode={colorMode}
        hasBbedColors={dataset?.hasBbedColors ?? false}
        onColorModeChange={setColorMode}
        onFit={() => setFitSignal((value) => value + 1)}
        onOpenFiles={() => setDialogOpen(true)}
      />

      <main className={`workspace ${selected ? 'has-inspector' : ''}`}>
        {dataset ? (
          <PointCloudViewer
            dataset={dataset}
            colorMode={colorMode}
            selected={selected}
            fitSignal={fitSignal}
            onSelected={handleSelected}
          />
        ) : (
          <div className="loading-state">
            {error ? <AlertTriangle size={26} /> : <LoaderCircle className="spinner" size={26} />}
            <h1>{error ? 'Could not open the prepared dataset' : 'Preparing the top view'}</h1>
            <p>{error || 'Loading browser point tiles and registered BBED footprints…'}</p>
            {error && (
              <button className="primary-button" type="button" onClick={() => setDialogOpen(true)}>
                Open local files
              </button>
            )}
          </div>
        )}
        <BuildingInspector feature={selected} onClose={() => setSelected(null)} />
      </main>

      <OpenFilesDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onLoaded={handleLocalDataset}
      />
    </div>
  );
}
