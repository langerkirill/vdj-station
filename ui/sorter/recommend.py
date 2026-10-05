"""Gemini folder recommendations for the HOUSE FORK of Music Sorter.

Listens to the track and picks exactly one of the EXISTING subfolders of the House
library (``sorter.house_folders.list_existing_folders``). Only when nothing fits may
it propose ONE new folder name (the cap of 3 new folders applies). Organic /
melodic / deep house only — there is no Zouk library and no BPM gate.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field

from . import house_folders as _hf
from . import profile as _profile
from .autocue_path import ensure_autocue_on_path
from vdj_cuer.gemini_call import generate_json

from .llm import models_to_try, resolve_sorter_model
from .relocate import summarize_cues

ensure_autocue_on_path()

DEFAULT_SORTER_MODEL = resolve_sorter_model()
MODEL_FALLBACKS = models_to_try(DEFAULT_SORTER_MODEL)

# Optional short hints for well-known House folders (anything else is judged by its name).
FOLDER_HINTS: dict[str, str] = {}


class HouseFolderPickSchema(BaseModel):
    """One House destination pick."""

    relative_path: str = Field(
        description=(
            "EXACTLY one of the existing folder paths from the list. Leave empty ONLY "
            "when use_new_folder is true."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence")
    reasoning: str = Field(description="Short why this folder fits")
    alternatives: list[str] = Field(
        default_factory=list,
        description="Up to 3 other EXISTING folder paths, best first",
    )
    use_new_folder: bool = Field(
        default=False,
        description="true ONLY if no existing folder fits at all",
    )
    new_folder_name: str = Field(
        default="",
        description="Name of the proposed new folder (no slashes) when use_new_folder is true",
    )


class HouseRecommendationSchema(BaseModel):
    house: HouseFolderPickSchema = Field(description="Best existing House folder")
    description: str = Field(
        default="",
        description="ONE line (max ~140 chars) describing how the track sounds and feels",
    )
    vibe_tags: list[str] = Field(
        default_factory=list,
        description="4-8 short descriptors: sub-genre (e.g. Deep House), mood, energy, texture",
    )


@dataclass
class LibraryPick:
    relative_path: str
    confidence: float
    reasoning: str
    alternatives: list[str] = field(default_factory=list)
    new_folder: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationResult:
    library: str
    relative_path: str
    confidence: float
    reasoning: str
    vibe_tags: list[str] = field(default_factory=list)
    description: str = ""
    alternatives: list[str] = field(default_factory=list)
    house: Optional[dict[str, Any]] = None
    bpm: Optional[float] = None
    model: str = ""
    cached: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_api_key() -> str:
    ui_root = Path(__file__).resolve().parents[1]  # ui/
    repo_root = Path(__file__).resolve().parents[2]  # repo root (fork)
    load_dotenv(ui_root / ".env")
    load_dotenv(repo_root / ".env")
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not found. Set it in the repo .env (or ui/.env).")
    return key


def build_folder_catalog() -> dict[str, list[str]]:
    """The REAL House subfolders (relative paths, nested ok)."""
    return {"House": _hf.list_existing_folders()}


def snap_to_approved(name: str) -> Optional[str]:
    """Map a model-returned name to a canonical EXISTING House folder (or None)."""
    raw = (name or "").strip().strip("/")
    if not raw:
        return None
    existing = _hf.list_existing_folders()
    for a in existing:
        if raw.lower() == a.lower():
            return a
    low = raw.lower()
    for a in existing:  # leaf-name match, e.g. "Journey" -> "Journey" (top-level wins first)
        if low == a.rsplit("/", 1)[-1].lower():
            return a
    return None


def _pick_from_schema(pick: HouseFolderPickSchema) -> LibraryPick:
    alts: list[str] = []
    main = snap_to_approved(pick.relative_path)
    for a in pick.alternatives or []:
        snapped = snap_to_approved(a)
        if snapped and snapped != main and snapped not in alts:
            alts.append(snapped)
    if main is None and pick.use_new_folder and (pick.new_folder_name or "").strip():
        # Only when nothing fits: validate the proposal and respect the 3-folder cap.
        name = (pick.new_folder_name or "").strip()
        try:
            canon_parent, cleaned = _hf.validate_new_folder("", name)
            _hf.assert_new_folder_allowed()
        except ValueError:
            cleaned = ""
        if cleaned:
            return LibraryPick(
                relative_path=cleaned,
                confidence=float(pick.confidence),
                reasoning=(pick.reasoning or "").strip(),
                alternatives=alts[:3],
                new_folder=True,
            )
    if main is None and alts:
        main, alts = alts[0], alts[1:]
    if main is None:
        raise ValueError(
            f"Model picked '{pick.relative_path}', which is not an existing House folder"
        )
    return LibraryPick(
        relative_path=main,
        confidence=float(pick.confidence),
        reasoning=(pick.reasoning or "").strip(),
        alternatives=alts[:3],
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"(\d+\.\d{10,})", lambda m: f"{float(m.group(1)):.2f}", text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def build_prompt(filename: str, bpm: Optional[float], catalog: list[str]) -> str:
    folder_lines = "\n".join(
        f"- {n}" + (f": {FOLDER_HINTS[n]}" if FOLDER_HINTS.get(n) else "") for n in catalog
    )
    bpm_line = f"{bpm:.1f}" if bpm is not None else "unknown"
    state = _hf.new_folder_state()
    new_ok = state["remaining"] is None or state["remaining"] > 0
    new_rule = (
        "- Strongly prefer an existing folder. ONLY if NOTHING in the list fits, set "
        "use_new_folder=true and give a short new_folder_name (one word or two, no slashes, "
        "not an existing name); then leave relative_path empty."
        if new_ok
        else "- Creating new folders is NOT allowed right now: always pick an existing folder "
        "and set use_new_folder=false."
    )
    return f"""You are helping a DJ copy a cued track into the right folder of his existing {_profile.GENRE_LABEL} library.

Pick the ONE best destination from these EXISTING House folders (the track is COPIED there; the original stays):
{folder_lines}

Musical BPM from VirtualDJ: {bpm_line} (target tempo for this library is about {_profile.TARGET_BPM:g}).

Listen to the full audio. Infer energy, groove, darkness/lightness, vocal prominence,
percussion weight, and how a DJ would file it by *feeling*, using each folder's name.

Rules:
- relative_path MUST be exactly one folder path from the list above
{new_rule}
- confidence is 0-1
- alternatives: up to 3 OTHER existing folder paths, best first
- description: ONE short line (max ~140 characters) saying how the track sounds and feels
- vibe_tags: 4-8 short descriptors, each 1-3 words: the sub-genre (e.g. Deep House), mood, energy, texture
- Keep reasoning to 1-2 short sentences

Track filename: {filename}
"""


class FolderRecommender:
    """Uploads a track to Gemini and picks one existing House folder."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or _load_api_key()
        self.model_name = model_name or DEFAULT_SORTER_MODEL
        self.client = genai.Client(api_key=self.api_key)
        self._cache: dict[str, RecommendationResult] = {}
        self._lock = threading.Lock()
        # Kept for API compatibility with older callers/tests.
        self._catalog_cache: Optional[dict[str, list[str]]] = None
        self._catalog_mtime: float = 0.0

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._catalog_cache = None

    def _get_catalog(self) -> dict[str, list[str]]:
        return build_folder_catalog()

    def _track_bpm(self, path: Path) -> Optional[float]:
        try:
            return summarize_cues(path).bpm
        except Exception:
            return None

    def recommend(
        self,
        audio_path: str | Path,
        *,
        force: bool = False,
        preferred_library: Optional[str] = None,  # accepted, ignored (single library)
    ) -> RecommendationResult:
        del preferred_library
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio not found: {path}")

        bpm = self._track_bpm(path)
        cache_key = f"{path}|house|v5|{len(_hf.list_existing_folders())}|{_hf.new_folder_state()['count']}"
        if not force:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None and cached.error is None:
                return RecommendationResult(**{**cached.to_dict(), "cached": True})

        catalog = self._get_catalog()["House"]
        prompt = build_prompt(path.name, bpm, catalog)

        uploaded = None
        upload_path = path
        temp_upload_path: Optional[Path] = None
        try:
            try:
                path.name.encode("ascii")
            except UnicodeEncodeError:
                import shutil
                import tempfile

                suffix = path.suffix or ".audio"
                fd, tmp = tempfile.mkstemp(prefix="sorter_upload_", suffix=suffix)
                os.close(fd)
                temp_upload_path = Path(tmp)
                shutil.copy2(path, temp_upload_path)
                upload_path = temp_upload_path

            uploaded = self.client.files.upload(file=str(upload_path))
            for _ in range(60):
                state = getattr(getattr(uploaded, "state", None), "name", None) or str(
                    getattr(uploaded, "state", "")
                )
                if not state or state in {"ACTIVE", "FileState.ACTIVE", "STATE_ACTIVE"}:
                    break
                if "FAILED" in state.upper():
                    raise RuntimeError(f"Gemini file processing failed: {state}")
                time.sleep(1)
                if getattr(uploaded, "name", None):
                    uploaded = self.client.files.get(name=uploaded.name)

            data, used_model = generate_json(
                self.client,
                [prompt, uploaded],
                HouseRecommendationSchema,
                models=models_to_try(self.model_name),
                timeout_seconds=180,
                thinking=False,
            )
            self.model_name = used_model
            parsed = HouseRecommendationSchema.model_validate(data)
            pick = _pick_from_schema(parsed.house)
            result = RecommendationResult(
                library="House",
                relative_path=pick.relative_path,
                confidence=pick.confidence,
                reasoning=pick.reasoning,
                vibe_tags=list(parsed.vibe_tags or []),
                description=(parsed.description or "").strip(),
                alternatives=list(pick.alternatives),
                house=pick.to_dict(),
                bpm=bpm,
                model=used_model,
                cached=False,
            )
            with self._lock:
                self._cache[cache_key] = result
            return result
        except Exception as exc:
            return RecommendationResult(
                library="House",
                relative_path="",
                confidence=0.0,
                reasoning="",
                bpm=bpm,
                model=self.model_name,
                error=str(exc),
            )
        finally:
            if temp_upload_path is not None and temp_upload_path.exists():
                try:
                    temp_upload_path.unlink()
                except OSError:
                    pass
            if uploaded is not None and getattr(uploaded, "name", None):
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    pass


_recommender: Optional[FolderRecommender] = None
_recommender_lock = threading.Lock()


def get_recommender() -> FolderRecommender:
    global _recommender
    with _recommender_lock:
        if _recommender is None:
            _recommender = FolderRecommender()
        return _recommender
