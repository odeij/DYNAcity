import {Building2, Database, MapPin, ScanLine, X} from 'lucide-react';
import type {BuildingFeature, BuildingProperties} from '../types';

type BuildingInspectorProps = {
  feature: BuildingFeature | null;
  onClose: () => void;
};

type Field = {key: string; label: string; suffix?: string};

const SECTIONS: Array<{
  title: string;
  icon: typeof Building2;
  fields: Field[];
}> = [
  {
    title: 'Identity',
    icon: MapPin,
    fields: [
      {key: 'dynacity_match_id', label: 'BBED match ID'},
      {key: 'OBJECTID_12', label: 'ArcGIS object ID'},
      {key: 'ParcelID', label: 'Parcel'},
      {key: 'BULBuildingID', label: 'Building ID'},
      {key: 'EnglishName', label: 'English name'},
      {key: 'Cadastral', label: 'Cadastral area'},
      {key: 'Sector', label: 'Sector'}
    ]
  },
  {
    title: 'Urban profile',
    icon: Building2,
    fields: [
      {key: 'Building_Use', label: 'Building use'},
      {key: 'UseDescription', label: 'Use description'},
      {key: 'NoofFloor', label: 'Floors'},
      {key: 'TypicalFloorHeight', label: 'Typical floor height', suffix: ' m'},
      {key: 'Ground_Floor_Height', label: 'Ground-floor height', suffix: ' m'},
      {key: 'Building_Hight_m', label: 'BBED building height', suffix: ' m'},
      {key: 'NoofApartments', label: 'Apartments'},
      {key: 'YearCompleted', label: 'Year completed'},
      {key: 'PermitNumber', label: 'Permit number'},
      {key: 'PermitYear', label: 'Permit year'},
      {key: 'GroundFloorUse', label: 'Ground-floor use'},
      {key: 'GroundFloorCommercialUse', label: 'Commercial use'},
      {key: 'NoofBasementFloor', label: 'Basement floors'},
      {key: 'BasementFloorUse', label: 'Basement use'},
      {key: 'RooftopFloorUse', label: 'Rooftop use'},
      {key: 'Status2018', label: 'Status 2018'},
      {key: 'Status2022', label: 'Status 2022'}
    ]
  },
  {
    title: 'Point-cloud match',
    icon: ScanLine,
    fields: [
      {key: 'pc_point_count', label: 'Matched points'},
      {key: 'pc_evaluation_point_count', label: 'Evaluation points'},
      {key: 'pc_point_density_m2', label: 'Point density', suffix: ' pts/m²'},
      {key: 'pc_z_min', label: 'Minimum elevation', suffix: ' m'},
      {key: 'pc_z_max', label: 'Maximum elevation', suffix: ' m'},
      {key: 'pc_z_mean', label: 'Mean elevation', suffix: ' m'},
      {key: 'pc_height_range', label: 'Height range', suffix: ' m'},
      {key: 'pc_red_mean', label: 'Mean red'},
      {key: 'pc_green_mean', label: 'Mean green'},
      {key: 'pc_blue_mean', label: 'Mean blue'}
    ]
  },
  {
    title: 'Provenance',
    icon: Database,
    fields: [
      {key: 'DataSource', label: 'Data source'},
      {key: 'FootprintSource', label: 'Footprint source'}
    ]
  }
];

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== '' && value !== 'Not Available Info';
}

function formatValue(value: unknown, suffix = ''): string {
  if (typeof value === 'number') {
    const digits = Number.isInteger(value) ? 0 : Math.abs(value) >= 100 ? 1 : 2;
    return `${new Intl.NumberFormat('en', {maximumFractionDigits: digits}).format(value)}${suffix}`;
  }
  return `${String(value)}${suffix}`;
}

function FieldRows({properties, fields}: {properties: BuildingProperties; fields: Field[]}) {
  const visible = fields.filter(({key}) => hasValue(properties[key]));
  if (!visible.length) return <p className="empty-section">No data supplied</p>;
  return (
    <dl className="field-list">
      {visible.map(({key, label, suffix}) => (
        <div className="field-row" key={key}>
          <dt>{label}</dt>
          <dd>{formatValue(properties[key], suffix)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function BuildingInspector({feature, onClose}: BuildingInspectorProps) {
  if (!feature) return null;
  const properties = feature.properties;
  const title =
    (hasValue(properties.ParcelID) && String(properties.ParcelID)) ||
    (hasValue(properties.BULBuildingID) && `Building ${properties.BULBuildingID}`) ||
    `Building ${properties.dynacity_match_id ?? ''}`;
  const usedKeys = new Set(SECTIONS.flatMap((section) => section.fields.map((field) => field.key)));
  usedKeys.add('OBJECTID');
  const additional = Object.keys(properties)
    .filter((key) => !usedKeys.has(key) && hasValue(properties[key]))
    .sort()
    .map((key) => ({key, label: key.replaceAll('_', ' ')}));

  return (
    <aside className="inspector" aria-label={`${title} details`}>
      <div className="inspector-header">
        <div>
          <p className="eyebrow">Selected building</p>
          <h1>{title}</h1>
          <p>{String(properties.Building_Use ?? 'BBED footprint')}</p>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close inspector">
          <X size={18} />
        </button>
      </div>

      <div className="inspector-scroll">
        {SECTIONS.map((section) => {
          const Icon = section.icon;
          return (
            <section className="inspector-section" key={section.title}>
              <h2>
                <Icon size={15} />
                {section.title}
              </h2>
              <FieldRows properties={properties} fields={section.fields} />
            </section>
          );
        })}
        {additional.length > 0 && (
          <section className="inspector-section">
            <h2>
              <Database size={15} />
              Additional BBED fields
            </h2>
            <FieldRows properties={properties} fields={additional} />
          </section>
        )}
      </div>
    </aside>
  );
}
