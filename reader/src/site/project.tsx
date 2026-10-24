import { Bug, Code, HeartHandshake, Scale, Star, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo } from "./Logo";

// Where the project lives. The server reports the same repository, with its star count.
const REPOSITORY = "https://github.com/ihsonnet/imagine-reader";
export const page = (repository: string, path: string) => `${repository}/${path}`;

interface ProjectInfo {
  repository: string;
  stars: number | null;
}

let lookup: Promise<ProjectInfo | null> | null = null; // asked once per page load, shared

/** The repository and its stars, as the server last saw them on GitHub. */
export function useProject(): ProjectInfo {
  const [info, setInfo] = useState<ProjectInfo | null>(null);
  useEffect(() => {
    lookup ??= fetch("/api/project").then(
      (response) => (response.ok ? response.json() : null),
      () => null,
    );
    let current = true;
    lookup.then((value) => current && setInfo(value));
    return () => {
      current = false;
    };
  }, []);
  return info ?? { repository: REPOSITORY, stars: null };
}

/** GitHub's mark (Octicons, MIT licence), which icon sets no longer include. */
function GitHubMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  );
}

/** A link to the repository, with its star count when known. */
export function GitHubButton({ label }: { label: string }) {
  const { repository, stars } = useProject();
  return (
    <a className="github-button" href={repository} target="_blank" rel="noopener">
      <span className="github-label">
        <GitHubMark />
        {label}
      </span>
      {stars !== null && (
        <span className="github-stars" aria-label={`${stars} stars on GitHub`}>
          <Star size={14} aria-hidden="true" />
          {stars.toLocaleString()}
        </span>
      )}
    </a>
  );
}

/** The home page footer: what the project is, where it lives, and how to help. */
export function SiteFooter() {
  const { repository } = useProject();
  return (
    <footer className="site-footer">
      <div className="site-inner footer-grid">
        <div className="footer-brand">
          <Logo size="small" />
          <p>Listen &amp; imagine a book read from its own pages. Free and open source.</p>
        </div>
        <nav className="footer-links" aria-labelledby="footer-open-source">
          <h2 id="footer-open-source">Open source</h2>
          <ul>
            <li>
              <a href={repository} target="_blank" rel="noopener">
                <Code size={16} aria-hidden="true" /> Source code
              </a>
            </li>
            <li>
              <a href={page(repository, "blob/main/CONTRIBUTING.md")} target="_blank" rel="noopener">
                <HeartHandshake size={16} aria-hidden="true" /> How to contribute
              </a>
            </li>
            <li>
              <a href={page(repository, "issues")} target="_blank" rel="noopener">
                <Bug size={16} aria-hidden="true" /> Report a problem
              </a>
            </li>
            <li>
              <a href={page(repository, "blob/main/LICENSE")} target="_blank" rel="noopener">
                <Scale size={16} aria-hidden="true" /> MIT licence
              </a>
            </li>
          </ul>
        </nav>
        <div className="footer-star">
          <h2>Like it?</h2>
          <p>A star on GitHub helps other readers find Imagine-Reader, and keeps it growing.</p>
          <GitHubButton label="Star on GitHub" />
        </div>
      </div>
      <div className="site-inner footer-legal">
        The sample books are in the public domain. Imagine-Reader is not affiliated with Studio Ghibli.
      </div>
    </footer>
  );
}

const ASKS = [
  { text: "Enjoying it? Star Imagine-Reader on GitHub", path: "", Icon: Star },
  { text: "Imagine-Reader is open source. Help build it", path: "blob/main/CONTRIBUTING.md", Icon: HeartHandshake },
  { text: "Did a page read wrongly? Report it on GitHub", path: "issues", Icon: Bug },
];

/** The reader's footer: one small, changing invitation to help the project. */
export function ReaderFooter() {
  const { repository } = useProject();
  const [ask] = useState(() => ASKS[Math.floor(Math.random() * ASKS.length)]);
  return (
    <footer className="reader-footer">
      <a href={ask.path ? page(repository, ask.path) : repository} target="_blank" rel="noopener">
        <ask.Icon size={14} aria-hidden="true" />
        {ask.text}
      </a>
    </footer>
  );
}

/** Shown once the last sentence of a book has been read aloud. */
export function EndOfBook({ onClose }: { onClose: () => void }) {
  return (
    <div className="end-of-book" role="status">
      <p>
        <strong>That is the end of the book.</strong> If you enjoyed reading it here, a star helps other readers find
        Imagine-Reader.
      </p>
      <GitHubButton label="Star on GitHub" />
      <button className="icon quiet" aria-label="Close" onClick={onClose}>
        <X size={18} />
      </button>
    </div>
  );
}
