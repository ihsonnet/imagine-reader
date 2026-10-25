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
    max_upload_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000
    max_words: int = 400_000
    job_timeout_seconds: float = 1800
    job_memory_mb: int = 3072  # address-space cap for the process that parses one PDF
    max_job_attempts: int = 2
    worker_threads: int = 4  # jobs the worker runs at once, so narration and pictures prepare side by side

    # Your AI key. One OpenRouter key does everything: it plans the pictures (OpenAI's model), reads aloud (Google's
    # Gemini storyteller), checks the reading, and draws the pictures (FLUX.2 [klein]). Without it, placeholders.
    openrouter_api_key: SecretStr | None = _key("OPENROUTER_API_KEY")
    # Optional: a Cloudflare account draws the same pictures for about a tenth of OpenRouter's price (and its first
    # 60 or so a day are free). The token needs the Workers AI permission.
    cloudflare_account_id: str | None = _key("CLOUDFLARE_ACCOUNT_ID")
    cloudflare_api_token: SecretStr | None = _key("CLOUDFLARE_API_TOKEN")

    # The narrator: Gemini's storyteller (voice Charon) reads every book. Bangla books use the Flash-Lite model,
    # which read Bangla more clearly in a listening test; the rest use Flash.
    speech_lanes: int = 3  # runs of sentences recorded at once for one section
    bangla_speech_model: str = "gemini-3.8-flash-lite-tts"  # about $0.009 a minute in 2026, $0.018 from 2027
    english_speech_model: str = "gemini-3.8-flash-tts"  # about $0.0135 a minute in 2026, $0.027 from 2027
    speech_voice: str = "Charon"
    timing_model: str = "gemini-3.8-flash"  # hears where each sentence of a run starts, for the highlight
    transcribe_model: str = "gpt-4o-transcribe"  # hears each clip, so a misplaced cut is read again

    # Pictures: FLUX.2 [klein] 4B, through OpenRouter.
    image_lanes: int = 4  # pictures drawn at once for one section

    # Spending on paid AI, in US dollars per UTC day, from the estimates of each request: work waits for the next
    # day once it is reached, never using a cheaper voice or picture instead.
    daily_spend_limit_usd: float = 5.0
    narration_character_budget: int = 2_000_000  # per book; preparation stops when it would be exceeded
    sketch_budget: int = 5000  # pictures per book; drawing stops when it would be exceeded
    max_clip_attempts: int = 3

    # The story: auto has a language model read each book once (its people, objects, places, and what happens in
    # each chapter), then plan each chapter's pictures as a storyboard; without a key, text rules. board | cast
    # (the looks only, with scenes by text rules) | heuristic choose one.
    story_brain: str = "auto"
    story_model: str = "gpt-5.4-mini"  # about $0.03 to read a novel the length of Alice; about $0.01 a chapter plan
    # Readers choose the picture style and how often a new picture appears; these are the defaults.
    sketch_style: str = "pencil"  # pencil | color | color-hd | ghibli (see sketches.STYLES)
    # A new picture after about this many words, 0 for Auto (4 or 5 a minute). None: about every 25 seconds, as the
    # pictures cost more than the voice; Auto for the placeholders without a key.
    sketch_words: int | None = None
    picture_styles: str = "pencil,color,color-hd,ghibli"
    prepare_on_upload: bool = True  # once a PDF is read, prepare the voice and pictures of its first chapter
    # The samples: their voice and pictures come ready-made in a sample pack, downloaded on first start.
    samples: bool = True
    sample_pack_url: str = "https://github.com/ihsonnet/imagine-reader/releases/download/samples-1/samples-1.tar.gz"
    # The pack's SHA-256, checked before it is installed; "" skips the check (for a pack of your own).
    sample_pack_sha256: str = "d24a944d5cfb416aaeb1f0bcf293d23b77a67473d1a76134de566be93af10ac3"
    prepare_samples: bool = False  # make every style and pace of the samples, with your key (for a sample pack)
    project_repository: str = "ihsonnet/imagine-reader"  # on GitHub; the reader links to it and shows its stars

    @field_validator("openrouter_api_key", "cloudflare_account_id", "cloudflare_api_token", mode="before")
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
    def ai(self) -> bool:
        """Whether the OpenRouter key is set, so the voice and pictures are real."""
        return self.openrouter_api_key is not None

    @property
    def cloudflare(self) -> bool:
        """Whether a Cloudflare account draws the pictures, in place of OpenRouter."""
        return bool(self.cloudflare_account_id and self.cloudflare_api_token)
