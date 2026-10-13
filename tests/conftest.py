import os

# Legacy fixtures here are LF-only. The CRLF hard gate is tested explicitly in
# ui/tests/test_crlf_hard_gate.py (it clears this flag). Production never sets it.
os.environ.setdefault("MUSIC_SORTER_ALLOW_LF_DB", "1")

# The write fast path remembers verified databases in a cache file; keep tests off the real one.
import tempfile as _tempfile  # noqa: E402

os.environ["VDJ_DB_VERIFIED_CACHE"] = os.path.join(
    _tempfile.mkdtemp(prefix="vdj-verified-cache-"), "verified_databases.json"
)

# R-88 writer guard: AutoCue's write record must never land in the real Notes folder from a test.
os.environ["AUTOCUE_WRITTEN_RECORD_PATH"] = os.path.join(
    _tempfile.mkdtemp(prefix="autocue-written-"), "autocue_written.json"
)
