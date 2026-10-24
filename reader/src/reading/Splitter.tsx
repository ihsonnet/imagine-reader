import { useRef } from "react";

interface Props {
  /** "columns" sits between two columns and drags sideways; "rows" sits under a panel and drags up and down. */
  between: "columns" | "rows";
  label: string;
  value: number | null; // pixels; null until the reader first resizes it
  measure: () => number; // the size on screen now, to start from
  min: number;
  max: number;
  /** Dragging right (columns) or down (rows) adds this many pixels per pixel moved: 1, or -1 for the reverse. */
  direction?: 1 | -1;
  onChange: (value: number) => void;
  onReset: () => void;
}

const STEP = 24; // pixels per arrow key press

/** A handle for resizing part of the reader by dragging, or with the arrow keys; double-click resets it. */
export function Splitter({ between, label, value, measure, min, max, direction = 1, onChange, onReset }: Props) {
  const start = useRef<{ pointer: number; value: number } | null>(null);
  const clamp = (next: number) => Math.round(Math.min(max, Math.max(min, next)));
  const axis = (event: { clientX: number; clientY: number }) => (between === "columns" ? event.clientX : event.clientY);

  return (
    <div
      className={`splitter splitter-${between}`}
      role="separator"
      aria-label={label}
      aria-orientation={between === "columns" ? "vertical" : "horizontal"}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value ?? measure())}
      tabIndex={0}
      title={`${label}: drag, or use the arrow keys. Double-click to reset.`}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        start.current = { pointer: axis(event), value: value ?? measure() };
      }}
      onPointerMove={(event) => {
        if (!start.current) return;
        onChange(clamp(start.current.value + direction * (axis(event) - start.current.pointer)));
      }}
      onPointerUp={() => {
        start.current = null;
      }}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        const now = value ?? measure();
        const forward = between === "columns" ? "ArrowRight" : "ArrowDown";
        const backward = between === "columns" ? "ArrowLeft" : "ArrowUp";
        if (event.key === forward) onChange(clamp(now + direction * STEP));
        else if (event.key === backward) onChange(clamp(now - direction * STEP));
        else if (event.key === "Home") onChange(min);
        else if (event.key === "End") onChange(max);
        else if (event.key === "Enter") onReset();
        else return;
        event.preventDefault();
      }}
    >
      <span className="splitter-grip" aria-hidden="true" />
    </div>
  );
}
