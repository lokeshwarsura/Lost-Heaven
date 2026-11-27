import React from 'react';

interface GradeSliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  defaultValue?: number;
  bipolar?: boolean;
  unit?: string;
  onChange: (nextValue: number) => void;
}

/**
 * Studio color-grading slider supporting:
 * - Bipolar center-origin mode (-100..+100 with `0` pinned at exact 50.0%, center tick notch, dual-direction accent fill)
 * - Unipolar mode (0..100% left-to-right fill)
 * - Double-click anywhere on label or track to reset to `defaultValue`
 */
export const GradeSlider: React.FC<GradeSliderProps> = ({
  label,
  value,
  min,
  max,
  step = 1,
  defaultValue = 0,
  bipolar = false,
  unit = '',
  onChange,
}) => {
  const clamped = Math.max(min, Math.min(max, Number.isFinite(value) ? value : defaultValue));
  const span = Math.max(1e-6, max - min);
  const pct = ((clamped - min) / span) * 100;
  const zeroPct = bipolar ? ((defaultValue - min) / span) * 100 : 0;

  const isModified = Math.abs(clamped - defaultValue) > step * 0.4;
  const rounded = step < 1 ? Number(clamped.toFixed(2)) : Math.round(clamped);
  const displayVal =
    bipolar && rounded > 0 ? `+${rounded}${unit}` : `${rounded}${unit}`;

  const fillLeft = bipolar ? Math.min(zeroPct, pct) : 0;
  const fillWidth = bipolar ? Math.abs(pct - zeroPct) : pct;

  return (
    <div
      className="select-none group"
      onDoubleClick={() => onChange(defaultValue)}
      title="Double-click to reset to neutral"
    >
      <div className="flex items-center justify-between text-[11px] mb-1 gap-2">
        <span className="text-[var(--text-secondary)] group-hover:text-[var(--text-primary)] transition truncate">
          {label}
        </span>
        <button
          type="button"
          onClick={() => isModified && onChange(defaultValue)}
          title={isModified ? 'Click or double-click slider to reset' : undefined}
          className={`font-mono font-bold text-[11px] tabular-nums transition shrink-0 ${
            isModified
              ? 'text-[var(--accent-primary)] hover:underline cursor-pointer'
              : 'text-[var(--text-muted)] cursor-default'
          }`}
        >
          {displayVal}
        </button>
      </div>

      <div className="relative h-5 flex items-center">
        {/* Track base */}
        <div className="relative w-full h-1.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)]/70 overflow-hidden">
          <div
            className="absolute top-0 bottom-0 bg-[var(--accent-primary)] transition-all duration-75"
            style={{
              left: `${fillLeft}%`,
              width: `${fillWidth}%`,
            }}
          />
        </div>

        {/* Exact 50.0% center tick notch for bipolar sliders */}
        {bipolar && (
          <div
            className="pointer-events-none absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-[2px] h-3 rounded-full bg-[var(--text-secondary)]/60 z-[1]"
            style={{ left: `${zeroPct}%` }}
          />
        )}

        {/* Transparent native range overlay preserves keyboard accessibility & smooth pointer scrubbing */}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={clamped}
          aria-label={label}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          onDoubleClick={() => onChange(defaultValue)}
          className="absolute inset-0 z-10 w-full h-full opacity-0 cursor-pointer"
        />

        {/* Crisp custom thumb */}
        <div
          className={`pointer-events-none absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 shadow-sm transition-transform duration-75 z-[2] group-hover:scale-110 ${
            isModified
              ? 'bg-[var(--accent-primary)] border-[var(--bg-canvas)]'
              : 'bg-[var(--text-primary)] border-[var(--bg-canvas)]'
          }`}
          style={{ left: `${pct}%` }}
        />
      </div>
    </div>
  );
};
