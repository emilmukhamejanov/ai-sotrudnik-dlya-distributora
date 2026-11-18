import { useState } from "react";

export interface Column {
  label: string;
  value: number;
  tooltip: string;
}

interface Props {
  data: Column[];
  yMax: number;
  ticks: number[];
  formatTick?: (v: number) => string;
  highlight?: number | null; // индекс столбца с подписью значения
  formatValue?: (v: number) => string;
  onSelect?: (index: number) => void;
  ariaLabel: string;
  height?: number;
}

/** Столбики одной серии: ≤24px, скруглённый верх, общая базовая линия, подпись только у выбранного. */
export default function ColumnChart({
  data,
  yMax,
  ticks,
  formatTick = String,
  highlight = null,
  formatValue = String,
  onSelect,
  ariaLabel,
  height = 180,
}: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 560;
  const pad = { top: 22, right: 8, bottom: 26, left: 36 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const slot = innerW / Math.max(1, data.length);
  const barW = Math.min(24, slot * 0.6);
  const y = (v: number) => pad.top + innerH - (Math.max(0, v) / (yMax || 1)) * innerH;

  const bar = (x: number, top: number, w: number, bottom: number) => {
    const h = bottom - top;
    if (h <= 0) return "";
    const r = Math.min(4, h, w / 2);
    return `M${x},${bottom} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${bottom} Z`;
  };

  const labelEvery = Math.ceil(data.length / 10);

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={pad.left - 6} y={y(t) + 3.5} textAnchor="end">
              {formatTick(t)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = pad.left + slot * i + slot / 2;
          const x = cx - barW / 2;
          const active = hover === i || highlight === i;
          return (
            <g
              key={i}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(i)}
              style={{ cursor: onSelect ? "pointer" : "default" }}
            >
              {/* зона наведения шире самого столбика */}
              <rect x={pad.left + slot * i} y={pad.top} width={slot} height={innerH} fill="transparent" />
              <path
                d={bar(x, y(d.value), barW, y(0))}
                fill="var(--series-1)"
                opacity={highlight === null || active ? 1 : 0.55}
              />
              {highlight === i && (
                <text className="value-label" x={cx} y={y(d.value) - 6} textAnchor="middle">
                  {formatValue(d.value)}
                </text>
              )}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - 8} textAnchor="middle">
                  {d.label}
                </text>
              )}
            </g>
          );
        })}
        <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} stroke="var(--rule-strong)" strokeWidth={1} />
      </svg>
      {hover !== null && data[hover] && (
        <div
          className="tooltip"
          style={{
            left: `${((pad.left + slot * hover + slot / 2) / width) * 100}%`,
            top: `${(y(data[hover].value) / height) * 100}%`,
          }}
        >
          {data[hover].tooltip}
        </div>
      )}
    </div>
  );
}
