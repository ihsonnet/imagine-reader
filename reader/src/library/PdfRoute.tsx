import { KeyRound, LockKeyhole, Scale, Trash2 } from "lucide-react";
import type { ServiceInfo } from "../lib/types";

/** A service a book's text goes to, what it gets, and why. */
interface Stop {
  who: string;
  gets: string;
  why: string;
}

/** Who gets what from a book with the keys set: OpenRouter, for the planning, the voice and its check, and the
 * pictures, unless a Cloudflare account draws them. */
function stopsFor(info: ServiceInfo): Stop[] {
  const { voice, pictures } = info.services;
  const stops: Stop[] = [];
  if (voice === "openrouter")
    stops.push({
      who: "OpenRouter",
      gets: "The text once, then each chapter as you reach it",
      why:
        "It passes each request to the model that does it: OpenAI's to learn how the story's people and places look " +
        "and plan each chapter's pictures, Google's Gemini to read the sentences aloud" +
        (pictures === "openrouter" ? ", and FLUX.2 to draw the pictures." : ".") +
        " Each recording is heard by OpenAI's recogniser, to check it was cut between the right sentences.",
    });
  if (pictures === "cloudflare")
    stops.push({ who: "Cloudflare", gets: "A written description of each picture", why: "Drawn with FLUX.2 [klein]." });
  return stops;
}

/**
 * "What happens to your PDF", drawn as a route: the file stays on your computer, and lines lead from it to each
 * service that gets some of its text, with what it gets and why. Below, the rules that keep a book yours.
 */
export function PdfRoute({ info }: { info: ServiceInfo | null }) {
  if (!info) return null;
  const stops = stopsFor(info);
  const shown: Stop[] = stops.length
    ? stops
    : [{ who: "Nothing", gets: "Until you add a key", why: "Nothing from your books leaves your computer." }];
  return (
    <section className="privacy" aria-labelledby="privacy-title">
      <h2 id="privacy-title">What happens to your PDF</h2>
      <div className="route">
        <figure className="route-file">
          <div className="route-sheet" aria-hidden="true">
            <span>.pdf</span>
          </div>
          <figcaption>The file stays on your computer</figcaption>
        </figure>
        <div>
          <ul className="route-stops">
            {shown.map((stop) => (
              <li key={`${stop.who}-${stop.gets}`}>
                <span className="route-who">{stop.who}</span>
                <span className="route-gets">{stop.gets}</span>
                <span className="route-why">{stop.why}</span>
              </li>
            ))}
          </ul>
          <p className="route-own">
            <KeyRound size={17} aria-hidden="true" />
            <span>
              <strong>Your own keys, on your own computer.</strong> They are read from the <code>.env</code> file and
              sent only to their own service, by the reader running there.
            </span>
          </p>
        </div>
      </div>

      <dl className="promises">
        <div>
          <dt>
            <LockKeyhole size={18} aria-hidden="true" />
            Yours alone
          </dt>
          <dd>No account, no cookies, no tracking. Your books are files in the reader's data folder.</dd>
        </div>
        <div>
          <dt>
            <Trash2 size={18} aria-hidden="true" />
            Gone when you delete it
          </dt>
          <dd>Its PDF, voice, and pictures are deleted at once.</dd>
        </div>
        <div>
          <dt>
            <Scale size={18} aria-hidden="true" />
            Only books you may use
          </dt>
          <dd>Your own writing, works in the public domain, or books you own.</dd>
        </div>
      </dl>
    </section>
  );
}
