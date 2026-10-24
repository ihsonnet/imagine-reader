/** The Imagine-Reader wordmark, set in a sketched, pencil-hatched face. */
export function Logo({ size = "large" }: { size?: "large" | "small" }) {
  return <span className={`logo logo-${size}`}>Imagine-Reader</span>;
}
