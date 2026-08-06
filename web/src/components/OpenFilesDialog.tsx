import {useRef, useState} from 'react';
import {FileJson2, ScanLine, Upload, X} from 'lucide-react';
import type {ViewerDataset} from '../types';
import {loadLocalDataset} from '../lib/data';

type OpenFilesDialogProps = {
  open: boolean;
  onClose: () => void;
  onLoaded: (dataset: ViewerDataset) => void;
};

export function OpenFilesDialog({open, onClose, onLoaded}: OpenFilesDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [lasFile, setLasFile] = useState<File | null>(null);
  const [geoJsonFile, setGeoJsonFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (dialogRef.current && open && !dialogRef.current.open) dialogRef.current.showModal();
  if (dialogRef.current && !open && dialogRef.current.open) dialogRef.current.close();

  async function handleLoad() {
    if (!lasFile || !geoJsonFile) return;
    setBusy(true);
    setError('');
    try {
      onLoaded(await loadLocalDataset(lasFile, geoJsonFile));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The files could not be opened.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      className="file-dialog"
      ref={dialogRef}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="dialog-header">
        <div>
          <p className="eyebrow">Local dataset</p>
          <h2>Open augmented point cloud</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close file picker">
          <X size={18} />
        </button>
      </div>
      <p className="dialog-lede">
        Choose the augmented LAS and its registered BBED GeoJSON. Files stay in this browser.
      </p>
      <label className="file-drop">
        <ScanLine size={20} />
        <span>
          <strong>{lasFile?.name ?? 'Augmented LAS or LAZ'}</strong>
          <small>{lasFile ? `${(lasFile.size / 1_000_000).toFixed(1)} MB` : 'enriched.las'}</small>
        </span>
        <input
          type="file"
          accept=".las,.laz"
          onChange={(event) => setLasFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label className="file-drop">
        <FileJson2 size={20} />
        <span>
          <strong>{geoJsonFile?.name ?? 'Registered footprints'}</strong>
          <small>{geoJsonFile ? `${(geoJsonFile.size / 1000).toFixed(0)} KB` : 'registered_bbed.geojson'}</small>
        </span>
        <input
          type="file"
          accept=".geojson,.json"
          onChange={(event) => setGeoJsonFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <p className="dialog-note">
        For smoother viewing, one in every four LAS points is rendered. BBED point coloring is available
        in prepared datasets; hover and selection still use every footprint here.
      </p>
      {error && <p className="dialog-error">{error}</p>}
      <div className="dialog-actions">
        <button className="quiet-button" type="button" onClick={onClose}>Cancel</button>
        <button
          className="primary-button"
          type="button"
          disabled={!lasFile || !geoJsonFile || busy}
          onClick={handleLoad}
        >
          <Upload size={16} />
          {busy ? 'Opening…' : 'Open dataset'}
        </button>
      </div>
    </dialog>
  );
}
