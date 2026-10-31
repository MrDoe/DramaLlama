from __future__ import annotations

import json
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from .models import (
    EngineName,
    Language,
    ParseReport,
    Project,
    ProjectSettings,
    ProjectSummary,
    Script,
    utcnow,
)
from .parser import parse_script

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def slugify(value: str, limit: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:limit].strip("-") or "untitled"


class ProjectStore:
    """File-based project store. SQLite is only for jobs, never for content."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        if not ID_RE.match(project_id):
            raise ValueError(f"invalid project id {project_id!r}")
        return self.root / project_id

    def create(
        self,
        title: str,
        language: Language,
        raw_script: str,
        *,
        project_id: str | None = None,
        default_engine: EngineName | None = None,
    ) -> Project:
        parsed = parse_script(raw_script, language, title or None)
        final_title = title.strip() or parsed.script.title or "Untitled drama"
        parsed.script.title = final_title
        base = slugify(final_title)
        project_id = project_id or f"{base}-{uuid.uuid4().hex[:6]}"
        project = Project(
            id=project_id,
            title=final_title,
            language=language,
            default_engine=default_engine,
            raw_script=raw_script,
            script=parsed.script,
            report=parsed.report,
            settings=ProjectSettings(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.save(project)
        return project

    def save(self, project: Project) -> Project:
        project.updated_at = utcnow()
        directory = self.project_dir(project.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "project.json"
        temp = directory / "project.json.tmp"
        temp.write_text(
            json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)
        # A plain-text copy is handy for git and external tools.
        (directory / "script.md").write_text(project.raw_script, encoding="utf-8")
        return project

    def get(self, project_id: str) -> Project:
        path = self.project_dir(project_id) / "project.json"
        if not path.exists():
            raise KeyError(project_id)
        project = Project.model_validate_json(path.read_text(encoding="utf-8"))
        # Legacy take_id=None cuts follow the selected take; stamp them so
        # old projects stay readable after multi-track cuts landed.
        # (Legacy Stem.engine / Character.engine keys from pre-project-level
        # projects are ignored by pydantic's default extra=ignore.)
        from .cuts import normalize_alignment

        for stem in project.stems:
            alignment = project.alignments.get(stem.id)
            if alignment is not None:
                normalize_alignment(alignment, stem.takes)
        return project

    def find(self, project_id: str) -> Project | None:
        try:
            return self.get(project_id)
        except (KeyError, ValueError):
            return None

    def list(self) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            project = self.find(directory.name)
            if project is None:
                continue
            lines = project.script.iter_lines()
            summaries.append(
                ProjectSummary(
                    id=project.id,
                    title=project.title,
                    language=project.language,
                    scenes=len(project.script.scenes),
                    lines=len(lines),
                    stems=len(project.stems),
                    runtime_estimate_s=(
                        project.report.runtime_estimate_s if project.report else 0.0
                    ),
                    coverage=project.report.coverage if project.report else 0.0,
                    updated_at=project.updated_at,
                )
            )
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries

    def delete(self, project_id: str) -> None:
        directory = self.project_dir(project_id)
        if not directory.exists():
            raise KeyError(project_id)
        shutil.rmtree(directory)

    def reparse(
        self,
        project: Project,
        *,
        method: str = "deterministic",
        report: ParseReport | None = None,
        script: Script | None = None,
    ) -> Project:
        if script is not None:
            project.script = script
            project.report = report
        else:
            parsed = parse_script(project.raw_script, project.language, project.title)
            parsed.script.title = project.title
            project.script = parsed.script
            project.report = parsed.report
        if project.report is not None:
            project.report.method = "llm" if method == "llm" else "deterministic"
        return self.save(project)
