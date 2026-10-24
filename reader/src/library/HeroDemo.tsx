/**
 * A miniature of the reader: the sentence being read is highlighted while a
 * pencil sketch of its scene draws itself. Text from the bundled sample story.
 * With reduced motion it shows the finished sketch and the first highlight.
 */
export function HeroDemo() {
  return (
    <figure
      className="hero-demo"
      role="img"
      aria-label="Preview: a sentence highlighted on a book page while a pencil sketch of a lighthouse draws itself beside it."
    >
      <div className="demo-page" aria-hidden="true">
        <span className="demo-heading">The Lantern Keeper</span>
        <p>
          <span className="demo-sentence demo-sentence-1">
            Every evening, just before the sun slipped behind the hills, old Mr. Hale climbed the stone steps of the
            lighthouse at Gull Point.
          </span>{" "}
          <span className="demo-sentence demo-sentence-2">
            He carried a brass lantern in one hand and a tin of matches in the other, and he counted the steps aloud as
            he went.
          </span>{" "}
          <span className="demo-sentence demo-sentence-3">There were one hundred and twelve of them.</span>
        </p>
        <span className="margin-note margin-note-text">read aloud, line by line</span>
      </div>
      <div className="demo-sketch" aria-hidden="true">
        <svg viewBox="0 0 240 200" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <g className="sketch-part sketch-part-1">
            <path pathLength={1} d="M98 168 L108 62" />
            <path pathLength={1} d="M132 62 L142 168" />
            <path pathLength={1} d="M101 138 L139 138 M104 108 L136 108 M106 84 L134 84" />
            <path pathLength={1} d="M24 170 C60 160 80 173 110 166 S170 159 216 170" />
            <path pathLength={1} d="M18 184 C40 178 55 190 75 184 S110 178 130 186 S172 180 222 186" />
          </g>
          <g className="sketch-part sketch-part-2">
            <path pathLength={1} d="M100 62 L140 62 M106 62 L106 46 L134 46 L134 62" />
            <path pathLength={1} d="M102 46 L120 31 L138 46" />
            <path pathLength={1} d="M140 52 L182 40 M140 57 L186 59 M100 52 L58 40 M100 57 L54 59" />
          </g>
          <g className="sketch-part sketch-part-3">
            <path pathLength={1} d="M52 168 L52 160 L60 160 L60 152 L68 152 L68 144 L76 144 L76 136 L84 136" />
            <path pathLength={1} d="M70 120 a4 4 0 1 0 0.1 0 M70 124 L70 134 M70 134 L66 142 M70 134 L74 142" />
            <path pathLength={1} d="M70 127 L76 131 L76 135 M74 135 h5 v5 h-5 z" />
          </g>
        </svg>
        <span className="margin-note margin-note-sketch">a sketch of the scene</span>
      </div>
    </figure>
  );
}
