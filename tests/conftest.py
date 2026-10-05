import os

# Legacy fixtures here are LF-only. The CRLF hard gate is tested explicitly in
# ui/tests/test_crlf_hard_gate.py (it clears this flag). Production never sets it.
os.environ.setdefault("MUSIC_SORTER_ALLOW_LF_DB", "1")
