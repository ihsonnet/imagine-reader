import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Library } from "./library/Library";
import { Reader } from "./reading/Reader";
import { StudyConsole } from "./study/StudyConsole";
import type { StudySession } from "./lib/types";

type Route =
  | { page: "library" } // the home page: your books and the samples
  | { page: "book"; id: string }
  | { page: "study" } // the study build's console
  | { page: "session"; id: string }; // a study session

/** The page lives in the URL hash (#/, #/book/<id>, #/study, …), so reload and back keep your place. */
function routeFromHash(): Route {
  const hash = window.location.hash;
  const book = /^#\/book\/([0-9a-f]{32})$/.exec(hash)?.[1];
  if (book) return { page: "book", id: book };
  const session = /^#\/study\/([0-9a-f]{24})$/.exec(hash)?.[1];
  if (session) return { page: "session", id: session };
  return hash === "#/study" ? { page: "study" } : { page: "library" };
}

export function App() {
  const [route, setRoute] = useState(routeFromHash);

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

  switch (route.page) {
    case "book":
      return <Reader bookId={route.id} onClose={() => go("")} />;
    case "study":
      return <StudyConsole onStart={(session) => go(`/study/${session.id}`)} />;
    case "session":
      return <StudySessionPage key={route.id} id={route.id} onClose={() => go("/study")} />;
    default:
      return <Library onOpen={(id) => go(`/book/${id}`)} />;
  }
}

/** A study session's reader, once the session's passage and pictures are known. */
function StudySessionPage({ id, onClose }: { id: string; onClose: () => void }) {
  const [session, setSession] = useState<StudySession | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.studySession(id).then(setSession, (e: Error) => setError(e.message));
  }, [id]);

  if (!session) {
    return (
      <main className="message">
        <p role="status">{error ?? "Opening the session…"}</p>
        <button onClick={onClose}>← Study console</button>
      </main>
    );
  }
  return <Reader bookId={session.book_id} study={session} onClose={onClose} />;
}
