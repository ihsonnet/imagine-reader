import { useEffect, useState } from "react";
import { Library } from "./library/Library";
import { Reader } from "./reading/Reader";

type Route =
  | { page: "library" } // the home page: your books and the samples
  | { page: "book"; id: string };

/** The page lives in the URL hash (#/, #/book/<id>), so reload and back keep your place. */
function routeFromHash(): Route {
  const book = /^#\/book\/([0-9a-f]{32})$/.exec(window.location.hash)?.[1];
  return book ? { page: "book", id: book } : { page: "library" };
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

  if (route.page === "book") return <Reader bookId={route.id} onClose={() => go("")} />;
  return <Library onOpen={(id) => go(`/book/${id}`)} />;
}
