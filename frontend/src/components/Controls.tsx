import type { ReactNode } from 'react';
import { useControls, type Unit } from '../lib/controls';

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function YearPicker() {
  const { available, years, setYears, currentYear } = useControls();
  const toggle = (y: number) => {
    const next = years.includes(y) ? years.filter((x) => x !== y) : [...years, y];
    if (next.length) setYears(next);
  };
  const recent = available.slice(-10);
  return (
    <div className="control">
      <span className="label">Years</span>
      <div className="years" role="group" aria-label="Years to overlay">
        {[...recent].reverse().map((y) => (
          <button key={y} type="button" className={`year-chip${y === currentYear ? ' current' : ''}`}
                  aria-pressed={years.includes(y)} onClick={() => toggle(y)}>
            {y}
          </button>
        ))}
      </div>
      <div className="seg hide-sm" role="group" aria-label="Year presets">
        <button type="button" aria-pressed={false} onClick={() => setYears(available.slice(-3))}>3Y</button>
        <button type="button" aria-pressed={false} onClick={() => setYears(available.slice(-5))}>5Y</button>
        <button type="button" aria-pressed={false} onClick={() => setYears(available.slice(-10))}>10Y</button>
      </div>
    </div>
  );
}

export function UnitToggle() {
  const { unit, setUnit } = useControls();
  return (
    <div className="control">
      <span className="label">Units</span>
      <Segmented<Unit> label="Units" value={unit} onChange={setUnit}
                       options={[{ value: 'usd', label: '$/TN' }, { value: 'local', label: 'Local' }]} />
    </div>
  );
}
