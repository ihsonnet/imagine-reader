import { Logo } from "./Logo";
import { GitHubButton } from "./project";

/** The header on every page but the reader: the logo (home), and the project's GitHub button. */
export function SiteHeader({ pill = "Early preview" }: { pill?: string }) {
  return (
    <header className="site-header">
      <div className="site-inner masthead">
        <div className="masthead-brand">
          <a className="logo-link" href="#/" aria-label="Imagine-Reader home">
            <Logo />
          </a>
          <span className="preview-pill">{pill}</span>
        </div>
        <div className="masthead-actions">
          <GitHubButton label="Star on GitHub" />
        </div>
      </div>
    </header>
  );
}
