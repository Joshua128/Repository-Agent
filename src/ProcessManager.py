"""Safe subprocess helpers used by repository agent tools."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0

    def as_text(self, max_characters: int = 8_000) -> str:
        output = self.stdout if self.succeeded else self.stderr
        output = output.strip() or "(command produced no output)"
        return (
            f"Exit code: {self.returncode}\n"
            f"Command: {' '.join(self.command)}\n"
            f"{output[-max_characters:]}"
        )


class ProcessManager:
    """Run narrowly-scoped Git commands inside a controlled workspace."""

    def __init__(self, repositories_dir: str | Path) -> None:
        self.repositories_dir = Path(repositories_dir).resolve()
        self.repositories_dir.mkdir(parents=True, exist_ok=True)

    def clone(self, repository_url: str, directory_name: str) -> ProcessResult:
        parsed_url = urlparse(repository_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
            raise ValueError("Only HTTPS GitHub repository URLs are allowed.")

        destination = (self.repositories_dir / directory_name).resolve()
        if destination.parent != self.repositories_dir:
            raise ValueError("The destination must be a single directory name.")
        if destination.exists():
            raise ValueError(f"Destination already exists: {destination}")

        command = ["git", "clone", "--", repository_url, str(destination)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repositories_dir,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessResult(
                command=command,
                returncode=124,
                stdout=exc.stdout or "",
                stderr="git clone timed out after 180 seconds.",
            )

        return ProcessResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
