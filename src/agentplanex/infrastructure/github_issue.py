"""Create GitHub Issues for a registered local Git repository."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from agentplanex.services.web.to_issue import CreatedIssue, GitHubIssueError

_REPOSITORY_SUFFIX = re.compile(r"(?P<owner>[^/:]+)/(?P<repository>[^/]+)$")


@dataclass(slots=True)
class GitHubIssuePublisher:
    """Hide credential, Git remote, and GitHub REST details behind one operation."""

    data_home: Path
    api_base_url: str = "https://api.github.com"

    def create(
        self,
        *,
        repository_path: Path,
        title: str,
        body: str,
    ) -> CreatedIssue:
        owner, repository = _origin_repository(repository_path)
        token = _github_token(self.data_home)
        payload = json.dumps({"title": title, "body": body}).encode()
        endpoint = (
            f"{self.api_base_url.rstrip('/')}/repos/"
            f"{quote(owner, safe='')}/{quote(repository, safe='')}/issues"
        )
        request = Request(
            endpoint,
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                response_payload = json.load(response)
        except HTTPError as error:
            raise GitHubIssueError(
                f"GitHub rejected Issue creation with HTTP {error.code}"
            ) from error
        except OSError as error:
            raise GitHubIssueError("GitHub Issue creation could not reach GitHub") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise GitHubIssueError("GitHub returned an invalid Issue response") from error

        if not isinstance(response_payload, dict):
            raise GitHubIssueError("GitHub returned an invalid Issue response")
        number = response_payload.get("number")
        url = response_payload.get("html_url")
        if not isinstance(number, int) or isinstance(number, bool) or not isinstance(url, str):
            raise GitHubIssueError("GitHub returned an invalid Issue response")
        return CreatedIssue(number=number, url=url)


def _github_token(data_home: Path) -> str:
    environment_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if environment_token:
        return environment_token
    token_path = data_home / "secrets" / "github" / "token"
    try:
        file_token = token_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise GitHubIssueError(
            "GitHub token is missing; set GITHUB_TOKEN or create "
            f"{token_path}"
        ) from error
    if not file_token:
        raise GitHubIssueError(f"GitHub token file is empty: {token_path}")
    return file_token


def _origin_repository(repository_path: Path) -> tuple[str, str]:
    try:
        remote_url = subprocess.run(
            ["git", "-C", str(repository_path), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitHubIssueError("Registered repository has no readable origin remote") from error

    match = _REPOSITORY_SUFFIX.search(remote_url.removesuffix(".git"))
    if match is None:
        raise GitHubIssueError("Registered repository origin does not identify a GitHub repository")
    owner = match.group("owner")
    repository = match.group("repository")
    if owner in {".", ".."} or repository in {".", ".."}:
        raise GitHubIssueError("Registered repository origin does not identify a GitHub repository")
    return owner, repository
