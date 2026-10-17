"""Server settings, read from IR_* environment variables or the .env file."""

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from imagine_reader.ingestion import Limits


def _key(name: str):
    return Field(default=None, validation_alias=AliasChoices(name, f"IR_{name}"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IR_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data/server")
    reader_dist: Path = Path("reader/dist")  # built reader UI, served at / when present
    # Adding your own books. Off for a public demo that offers only the samples, so nobody's books or keys are kept
    # on someone else's server.
    uploads: bool = True
    max_upload_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000
    max_words: int = 400_000
    job_timeout_seconds: float = 1800
    job_memory_mb: int = 3072  # address-space cap for the process that parses one PDF
    max_job_attempts: int = 2
    worker_threads: int = 4  # jobs the worker runs at once, so narration and pictures prepare side by side

    # Your AI keys. One OpenRouter key does everything but the cheapest pictures: it plans the pictures (OpenAI's
    # model), reads aloud (Google's Gemini storyteller), and checks the reading. A Cloudflare account draws the
    # pictures for about a tenth of OpenRouter's price (and its first 60 or so a day are free); without one,
    # OpenRouter draws them too. Direct OpenAI and Gemini keys work as well. Without any key, placeholders stand in.
    openrouter_api_key: SecretStr | None = _key("OPENROUTER_API_KEY")
    openai_api_key: SecretStr | None = _key("OPENAI_API_KEY")
    gemini_api_key: SecretStr | None = _key("GEMINI_API_KEY")
    cloudflare_account_id: str | None = _key("CLOUDFLARE_ACCOUNT_ID")
    cloudflare_api_token: SecretStr | None = _key("CLOUDFLARE_API_TOKEN")

    # The narrator: Gemini's storyteller (voice Charon) reads every book. Bangla books use the Flash-Lite model,
    # which read Bangla more clearly than OpenAI in a listening test; the rest use Flash. "openai" chooses OpenAI's
    # narrator instead, with an OpenAI key.
    speech_provider: str = "auto"  # auto | gemini | openai | mock
    speech_lanes: int = 3  # runs of sentences recorded at once for one section
    bangla_speech_model: str = "gemini-3.8-flash-lite-tts"  # about $0.009 a minute in 2026, $0.018 from 2027
    english_speech_model: str = "gemini-3.8-flash-tts"  # about $0.0135 a minute in 2026, $0.027 from 2027
    speech_voice: str = "Charon"
    timing_model: str = "gemini-3.8-flash"  # hears where each sentence of a run starts, for the highlight
    transcribe_model: str = "gpt-4o-transcribe"  # hears each clip, so a misplaced cut is read again
    openai_speech_model: str = "gpt-4o-mini-tts"  # OpenAI's narrator, with speech_provider=openai
    openai_speech_voice: str = "marin"
    speech_usd_per_million_characters: float = 17.0  # gpt-4o-mini-tts, about $0.015 per minute

    # Pictures: auto is Cloudflare (FLUX.2 [klein] 4B) with its account and token, else OpenRouter's FLUX.2 [klein]
    # 4B, else OpenAI with a key, else placeholders. The study build keeps OpenAI, as its paper describes.
    image_provider: str = "auto"  # auto | cloudflare | openrouter | openai | mock
    image_model: str = "gpt-image-1-mini"  # OpenAI's, with image_provider=openai
    image_size: str = "1536x1024"
    image_lanes: int = 4  # pictures drawn at once for one section
    image_usd_each: float | None = None  # None: the provider's list price

    # Spending on paid AI, in US dollars per UTC day, from the estimates of each request: work waits for the next
    # day once it is reached, never using a cheaper voice or picture instead.
    daily_spend_limit_usd: float = 5.0
    narration_character_budget: int = 2_000_000  # per book; preparation stops when it would be exceeded
    sketch_budget: int = 5000  # pictures per book; drawing stops when it would be exceeded
    max_clip_attempts: int = 3

    # The story: auto has a language model read each book once (its people, objects, places, and what happens in
    # each chapter), then plan each chapter's pictures as a storyboard; without a key, text rules. board | cast
    # (the looks only, with scenes by text rules) | heuristic choose one. The study build uses cast.
    story_brain: str = "auto"
    story_model: str = "gpt-5.4-mini"  # about $0.03 to read a novel the length of Alice; about $0.01 a chapter plan
    # Readers choose the picture style and how often a new picture appears; these are the defaults.
    sketch_style: str = "pencil"  # pencil | color | color-hd | ghibli (see sketches.STYLES)
    # A new picture after about this many words, 0 for Auto (4 or 5 a minute). None: Auto when Cloudflare draws,
    # and about every 25 seconds when OpenRouter does, where pictures cost about ten times as much.
    sketch_words: int | None = None
    picture_styles: str = "pencil,color,color-hd,ghibli"
    prepare_on_upload: bool = True  # once a PDF is read, prepare the voice and pictures of its first chapter
    # The samples: their voice and pictures come ready-made in a sample pack, downloaded on first start. A public
    # demo may keep them prepared in every style and pace too, with its own keys.
    samples: bool = True
    sample_pack_url: str = "https://github.com/ihsonnet/imagine-reader/releases/download/samples-1/samples-1.tar.gz"
    # The pack's SHA-256, checked before it is installed; "" skips the check (for a pack of your own).
    sample_pack_sha256: str = "d24a944d5cfb416aaeb1f0bcf293d23b77a67473d1a76134de566be93af10ac3"
    prepare_samples: bool = False  # make every style and pace of the samples, with your keys
    project_repository: str = "ihsonnet/imagine-reader"  # on GitHub; the reader links to it and shows its stars
    # The study build (docs/study.md): a folder holding the study's passages as A.pdf, B.pdf, C.pdf and D.pdf.
    # When set, the site offers the study's picture styles, a console at /#/study, and an interaction log.
    study_dir: Path | None = None
    build_version: str = "dev"  # recorded with every logged event; set it to the study build's commit or tag

    # Limits for a server others can reach (a public demo); generous for your own computer.
    client_ip_header: str | None = None  # e.g. "x-real-ip" behind a reverse proxy; trusted only when set
    requests_per_minute: int = 600  # per visitor, API requests
    uploads_per_hour: int = 60  # per visitor
    preparations_per_hour: int = 600  # per visitor, narration and sketch requests
    max_storage_mb: int = 50_000  # all stored books together; uploads pause beyond it
    max_queued_ingests: int = 20  # uploads pause while this many wait to be read

    @field_validator(
        "openrouter_api_key",
        "openai_api_key",
        "gemini_api_key",
        "cloudflare_account_id",
        "cloudflare_api_token",
        mode="before",
    )
    @classmethod
    def _blank_key_is_no_key(cls, value):
        return value or None  # an empty OPENROUTER_API_KEY= line in .env means "not configured"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'imagine-reader.db'}"

    @property
    def limits(self) -> Limits:
        return Limits(max_bytes=self.max_upload_bytes, max_pages=self.max_pages, max_words=self.max_words)

    @property
    def cloudflare(self) -> bool:
        return bool(self.cloudflare_account_id and self.cloudflare_api_token)

    @property
    def text_key(self) -> bool:
        """Whether a key for this site's language model is set (OpenRouter or OpenAI)."""
        return bool(self.openrouter_api_key or self.openai_api_key)
