"""Make the sample pack from a reader that has prepared its samples (see server/.../sample_pack.py).

    python tools/make_sample_pack.py DATA_DIR OUT.tar.gz [--plan-end=-s2-wauto2/pencil] [--voice Charon]

DATA_DIR is that reader's data folder (imagine-reader.db and books/). For each sample, the pack holds its reading
and document (so its sentences match its recordings), its cast and storyboards, every picture of the plan whose id
ends with --plan-end in the story's current reading, and every recording in --voice; with the database rows that
describe them. Only the standard library is used, so it runs wherever that reader runs. Publish the pack as a
release asset and set IR_SAMPLE_PACK_URL (and IR_SAMPLE_PACK_SHA256, printed here) to it.
"""

import argparse
import hashlib
import io
import json
import sqlite3
import tarfile
import time
from pathlib import Path

SAMPLE_IDS = ("0" * 31 + "1", "0" * 31 + "2", "0" * 31 + "3")
VERSION = 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--plan-end", default="-s2-wauto2/pencil", help="the end of the picture plan's id")
    parser.add_argument("--voice", default="Charon")
    args = parser.parse_args()

    db = sqlite3.connect(f"file:{args.data_dir / 'imagine-reader.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    manifest = {"version": VERSION, "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "books": []}
    with tarfile.open(args.out, "w:gz") as pack:
        for book_id in SAMPLE_IDS:
            folder = args.data_dir / "books" / book_id
            book = db.execute("select sha256, filename from books where id = ?", (book_id,)).fetchone()
            if book is None or not (folder / "story.json").exists():
                print(f"{book_id}: not prepared, left out")
                continue
            bible = json.loads((folder / "story.json").read_text(encoding="utf-8"))["brain"]
            plan = f"{bible}{args.plan_end}"
            brain = plan.rsplit("/", 1)[0]
            scenes = [dict(row) for row in db.execute(
                "select plan, scene_id, section_id, first_sentence, last_sentence, spec, cache_key, rendered_style"
                " from scenes where book_id = ? and plan = ? and status = 'ready' order by first_sentence",
                (book_id, plan),
            )]  # fmt: skip
            for scene in scenes:
                scene["spec"] = json.loads(scene["spec"]) if isinstance(scene["spec"], str) else scene["spec"]
            clips = [dict(row) for row in db.execute(
                "select section_id, sentence_id, \"order\", text, voice, cache_key, pause_after, duration"
                " from narration_clips where book_id = ? and voice = ? and status = 'ready' order by \"order\"",
                (book_id, args.voice),
            )]  # fmt: skip
            files = [folder / name for name in ("reading.json", "document.json", "story.json")]
            files += sorted((folder / "scenes" / f"{bible}--{brain}").glob("sec-*.json"))
            files += [_asset(folder / "images", scene["cache_key"], (".webp", ".png")) for scene in scenes]
            files += [_asset(folder / "audio", clip["cache_key"], (".mp3", ".wav")) for clip in clips]
            missing = [str(path) for path in files if not path.exists()]
            if missing:
                raise SystemExit(f"{book_id}: {len(missing)} files missing, such as {missing[0]}")
            for path in files:
                pack.add(path, arcname=f"books/{book_id}/{path.relative_to(folder).as_posix()}")
            manifest["books"].append(
                {"id": book_id, "pdf_sha256": book["sha256"], "plan": plan, "scenes": scenes, "clips": clips}
            )
            print(f"{book['filename']}: {len(scenes)} pictures, {len(clips)} recordings ({plan})")
        data = json.dumps(manifest, ensure_ascii=False).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size, info.mtime = len(data), int(time.time())
        pack.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"{args.out}: {args.out.stat().st_size / 1e6:.1f} MB, sha256 {digest}")


def _asset(folder: Path, key: str, suffixes: tuple[str, ...]) -> Path:
    """The file kept under this key, whichever of its formats it is in (the first, when none is there)."""
    return next(
        (folder / f"{key}{s}" for s in suffixes if (folder / f"{key}{s}").exists()), folder / f"{key}{suffixes[0]}"
    )


if __name__ == "__main__":
    main()
