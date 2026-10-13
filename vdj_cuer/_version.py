"""The AutoCue version: the only place it is written (pyproject.toml reads it).

Semantic versioning (docs/autocue-lab/RELEASING.md): PATCH fixes without intended changes to
the cues written, MINOR changes to what AutoCue writes or new features, MAJOR breaking changes
(database rules, model features, the job interface the sorters use). Each release is the git
tag v<this version>.
"""

__version__ = "1.2.0"
