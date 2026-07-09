"""
GitHub repo access for the Streamlit UI.

Lets a reviewer paste a GitHub URL (optionally pinned to a branch/tag via the
`/tree/<ref>` form), shallow-clones it into a temp dir, and exposes helpers to
turn a pipeline finding's ``file:line`` into a clickable
``https://github.com/<owner>/<repo>/blob/<ref>/<path>#L<line>`` deep link.

The rest of the pipeline is unchanged: it still just walks a local ``repo_path``.
This module only handles (a) getting the code onto disk and (b) mapping the
absolute on-disk paths back to repo-relative paths for link building.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

# https://github.com/OWNER/REPO
# https://github.com/OWNER/REPO/tree/BRANCH[/subdir...]
# git@github.com:OWNER/REPO.git
_HTTPS_RE = re.compile(r"github\.com[/:]+(?P<owner>[^/]+)/(?P<repo>[^/#]+)")
_TREE_RE = re.compile(r"/tree/(?P<ref>[^/]+)")


@dataclass
class GitHubRepo:
    """A cloned GitHub repository plus everything needed to build blob links."""
    owner: str
    repo: str
    ref: str                 # branch / tag / commit actually checked out
    local_path: str          # on-disk clone root
    url: str                 # normalized https repo url

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def rel_path(self, file_path: str) -> str:
        """Map an absolute on-disk path back to a repo-relative POSIX path."""
        try:
            rel = os.path.relpath(file_path, self.local_path)
        except ValueError:
            rel = file_path
        return rel.replace(os.sep, "/")

    def blob_url(self, file_path: str, line: Optional[int] = None,
                 end_line: Optional[int] = None) -> str:
        """Clickable GitHub link to a file, optionally anchored to a line range."""
        rel = self.rel_path(file_path)
        url = f"https://github.com/{self.owner}/{self.repo}/blob/{self.ref}/{rel}"
        if line:
            url += f"#L{line}"
            if end_line and end_line != line:
                url += f"-L{end_line}"
        return url


def parse_github_url(url: str) -> tuple[str, str, Optional[str]]:
    """Return (owner, repo, ref_or_None) from a GitHub URL. Raises ValueError."""
    url = url.strip()
    m = _HTTPS_RE.search(url)
    if not m:
        raise ValueError(f"Not a recognized GitHub URL: {url!r}")
    owner = m.group("owner")
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref_match = _TREE_RE.search(urlparse(url).path)
    ref = ref_match.group("ref") if ref_match else None
    return owner, repo, ref


def clone_repo(url: str, dest_parent: Optional[str] = None) -> GitHubRepo:
    """Shallow-clone a public GitHub repo and return a :class:`GitHubRepo`.

    Raises ValueError on a bad URL and RuntimeError if the clone fails (private
    repo, network, bad ref, ...).
    """
    owner, repo, ref = parse_github_url(url)
    https_url = f"https://github.com/{owner}/{repo}.git"

    dest = tempfile.mkdtemp(prefix="aicr_gh_", dir=dest_parent)
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [https_url, dest]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed for {https_url} "
            f"(ref={ref or 'default'}):\n{proc.stderr.strip()}"
        )

    # Resolve the ref we actually landed on (so blob links are correct even
    # when the user pasted a plain repo URL and got the default branch).
    resolved_ref = ref
    if not resolved_ref:
        head = subprocess.run(
            ["git", "-C", dest, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        resolved_ref = head.stdout.strip() or "HEAD"

    return GitHubRepo(
        owner=owner, repo=repo, ref=resolved_ref,
        local_path=dest, url=f"https://github.com/{owner}/{repo}",
    )
