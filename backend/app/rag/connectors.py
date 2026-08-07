"""Knowledge connectors: sitemap, crawl, GitHub, Notion, Confluence, Drive, Zendesk.

Each connector turns remote content into `FetchedDoc`s; the sync task in
`app.rag.tasks` upserts them into Documents by (source_id, uri) and ingests.
All HTTP goes through httpx.AsyncClient (10s timeout, redirects followed).
Sitemap/crawl page fetches reuse the limits enforced by `app.rag.tasks`
(2MB cap, text/html only); listing failures raise `FetchError`.

Sitemaps surface each URL's optional <lastmod> so the sync task can skip
refetching unchanged pages. Crawls honor robots.txt `User-agent: *` rules
(unless respect_robots=false), filter discovered links through include/exclude
path globs (exclude wins), and fetch each depth level concurrently
(Semaphore(4)) with a per-fetch politeness delay.

OAuth-connected sources (Notion/Confluence "oauth" mode, Google Drive) resolve
bearer tokens through the `app.integrations.tokens` seam — the sync task plumbs
its (session, workspace_id) into the fetch for that. Every FetchedDoc.uri is
the human web URL (webui link / webViewLink / html_url): citations point users
there, never at an API endpoint. 429 responses are retried after the provider's
Retry-After, capped so a sync worker is never parked for minutes.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import re
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any
from urllib.parse import urldefrag, urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from app.core.net import UnsafeUrlError, assert_public_url
from app.rag import parsers
from app.rag.parsers import ParseError
from app.rag.tasks import (
    URL_FETCH_MAX_BYTES,
    URL_FETCH_TIMEOUT_SECONDS,
    FetchError,
    fetch_html_bytes,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.integration import IntegrationConnection

GITHUB_API = "https://api.github.com"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

SITEMAP_DEFAULT_MAX_PAGES = 50
SITEMAP_INDEX_DEPTH = 2  # sitemapindex recursion depth
CRAWL_DEFAULT_MAX_PAGES = 30
CRAWL_DEFAULT_MAX_DEPTH = 3
CRAWL_CONCURRENCY = 4  # simultaneous page fetches within one depth level
CRAWL_DEFAULT_DELAY_MS = 250  # politeness pause before each fetch
CRAWL_MAX_DELAY_MS = 2000
ROBOTS_MAX_CHARS = 200_000  # oversized robots.txt bodies are truncated, not fatal
NOTION_DEFAULT_MAX_PAGES = 100
NOTION_BLOCK_DEPTH = 3  # recursion into has_children blocks

GITHUB_MAX_FILES = 200
GITHUB_MAX_FILE_BYTES = 1024 * 1024
GITHUB_MARKDOWN_EXTENSIONS = (".md", ".mdx", ".markdown")
GITHUB_INDEXABLE_EXTENSIONS = frozenset({".md", ".mdx", ".markdown", ".rst", ".txt"})
GITHUB_WELL_KNOWN_FILES = frozenset(
    {
        "readme",
        "license",
        "changelog",
        "contributing",
        "authors",
        "notice",
        "security",
        "support",
        "codeowners",
    }
)
GITHUB_DENIED_SEGMENTS = frozenset(
    {".git", "node_modules", "vendor", "dist", "build", ".venv", "__pycache__"}
)

CONFLUENCE_CLOUD_API = "https://api.atlassian.com/ex/confluence"
CONFLUENCE_DEFAULT_MAX_PAGES = 100
CONFLUENCE_MAX_PAGES_CAP = 500
CONFLUENCE_PAGE_LIMIT = 100  # per-request limit for v2 space/page listings

GDRIVE_API = "https://www.googleapis.com/drive/v3"
GDRIVE_DEFAULT_MAX_FILES = 100
GDRIVE_MAX_FILES_CAP = 500
GDRIVE_RAW_MAX_BYTES = URL_FETCH_MAX_BYTES  # raw .md/.txt/.html download cap
GDRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
GDRIVE_LIST_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size)"
# Google-native types exported server-side: source mimeType → export mimeType.
GDRIVE_EXPORTS = {
    "application/vnd.google-apps.document": "text/markdown",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
GDRIVE_RAW_TEXT_MIMES = frozenset({"text/markdown", "text/plain", "text/html"})
GDRIVE_RAW_TEXT_EXTENSIONS = (".md", ".markdown", ".txt", ".html", ".htm")
# Recorded-skip types this wave: reusing the pdf/docx parsers is a follow-up.
GDRIVE_SKIPPED_MIMES = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
}

ZENDESK_DEFAULT_LOCALE = "en-us"
ZENDESK_SUBDOMAIN_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
ZENDESK_MAX_SYNC_PAGES = 20  # incremental pages per sync (up to 1000 articles each)

RATE_LIMIT_MAX_RETRIES = 2  # extra attempts after a 429 before giving up
RATE_LIMIT_MAX_SLEEP_SECONDS = 30.0  # Retry-After is honored, but never longer than this
RATE_LIMIT_DEFAULT_SLEEP_SECONDS = 1.0  # missing/unparseable Retry-After


@dataclass
class FetchedDoc:
    """One remote document in the shape the sync task upserts + ingests."""

    title: str
    text: str
    uri: str
    mime: str = "text/html"
    # Merged into Document.meta on upsert (sitemap sets {"lastmod": ...}).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SitemapEntry:
    """One <url> element: the location plus its optional <lastmod> stamp."""

    url: str
    lastmod: str | None = None


def check_public_url(url: str) -> None:
    """SSRF guard for user-supplied fetch targets (urls/sitemap/crawl syncs).

    Thin adapter over :func:`app.core.net.assert_public_url` — the one egress
    policy shared with outbound webhooks and custom agent actions — re-raised as
    the :class:`FetchError` every sync path already handles.
    """
    try:
        assert_public_url(url)
    except UnsafeUrlError as exc:
        raise FetchError(str(exc)) from exc


# ---------------------------------------------------------------------------
# sitemap
# ---------------------------------------------------------------------------


async def fetch_sitemap(config: dict[str, Any]) -> list[SitemapEntry]:
    """Return the entries listed in the configured sitemap (recursing indexes).

    Each entry carries the <loc> URL plus its <lastmod> when the sitemap
    provides one; the sync task stores it on the document and skips refetching
    pages whose stamp is unchanged.
    """
    sitemap_url = str(config.get("sitemap_url") or "").strip()
    max_pages = int(config.get("max_pages") or SITEMAP_DEFAULT_MAX_PAGES)
    entries: list[SitemapEntry] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        await _collect_sitemap_urls(client, sitemap_url, entries, max_pages=max_pages, depth=0)
    if not entries:
        raise FetchError(f"{sitemap_url}: sitemap contains no URLs")
    return entries


async def _collect_sitemap_urls(
    client: httpx.AsyncClient,
    sitemap_url: str,
    entries: list[SitemapEntry],
    *,
    max_pages: int,
    depth: int,
) -> None:
    if len(entries) >= max_pages:
        return
    check_public_url(sitemap_url)
    try:
        response = await client.get(sitemap_url)
    except httpx.HTTPError as exc:
        raise FetchError(f"{sitemap_url}: fetch failed: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(f"{sitemap_url}: HTTP {response.status_code}")
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise FetchError(f"{sitemap_url}: not valid sitemap XML: {exc}") from exc
    match = re.match(r"\{.*\}", root.tag)
    namespace = match.group(0) if match else ""
    tag = root.tag.removeprefix(namespace)
    if tag == "sitemapindex":
        if depth >= SITEMAP_INDEX_DEPTH:
            return
        for loc in root.iterfind(f"{namespace}sitemap/{namespace}loc"):
            child = (loc.text or "").strip()
            if child:
                await _collect_sitemap_urls(
                    client, child, entries, max_pages=max_pages, depth=depth + 1
                )
            if len(entries) >= max_pages:
                return
    elif tag == "urlset":
        for element in root.iterfind(f"{namespace}url"):
            loc_element = element.find(f"{namespace}loc")
            text = (loc_element.text or "").strip() if loc_element is not None else ""
            if text:
                lastmod_element = element.find(f"{namespace}lastmod")
                lastmod = (
                    (lastmod_element.text or "").strip() if lastmod_element is not None else ""
                )
                entries.append(SitemapEntry(url=text, lastmod=lastmod or None))
            if len(entries) >= max_pages:
                return
    else:
        raise FetchError(f"{sitemap_url}: unexpected sitemap root element <{tag}>")


# ---------------------------------------------------------------------------
# recursive crawl
# ---------------------------------------------------------------------------


async def crawl_site(config: dict[str, Any]) -> tuple[list[FetchedDoc], list[str]]:
    """BFS-crawl same-site pages under the base URL's path.

    Returns (docs, per-page errors). Near-duplicate pages — same (title, text)
    after extraction — are suppressed. Page fetches enforce the shared 2MB /
    text/html limits; a single broken page is recorded, never fatal.

    Config knobs beyond base_url/max_pages/max_depth:
    - `include_patterns` / `exclude_patterns`: fnmatch globs matched against a
      discovered link's URL path (exclude wins). They filter the frontier, not
      the explicitly configured base URL — a mistyped include never leaves the
      crawl with nothing to start from.
    - `respect_robots` (default true): robots.txt is fetched once per sync and
      its `User-agent: *` rules skip disallowed URLs (including the base URL).
    - `delay_ms` (default 250): pause before each fetch. Fetches inside one
      depth level run concurrently (Semaphore(4)); levels stay strictly
      ordered, so BFS semantics and the max_pages cap are unchanged.
    """
    base_url = str(config.get("base_url") or "").strip()
    max_pages = int(config.get("max_pages") or CRAWL_DEFAULT_MAX_PAGES)
    raw_depth = config.get("max_depth")
    max_depth = CRAWL_DEFAULT_MAX_DEPTH if raw_depth is None else int(raw_depth)
    raw_delay = config.get("delay_ms")
    delay_ms = CRAWL_DEFAULT_DELAY_MS if raw_delay is None else int(raw_delay)
    delay_ms = max(0, min(delay_ms, CRAWL_MAX_DELAY_MS))
    include_patterns = _pattern_list(config.get("include_patterns"))
    exclude_patterns = _pattern_list(config.get("exclude_patterns"))
    check_public_url(base_url)

    start = _normalize_link(base_url)
    docs: list[FetchedDoc] = []
    errors: list[str] = []
    seen_content: set[int] = set()
    visited: set[str] = {start}
    frontier: list[str] = [start]
    depth = 0
    fetched_count = 0
    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        robots = RobotsRules()
        if bool(config.get("respect_robots", True)):
            robots = await fetch_robots(client, base_url)
        while frontier and fetched_count < max_pages:
            batch = [url for url in frontier if robots.allows(url)][: max_pages - fetched_count]
            fetched_count += len(batch)
            results = await asyncio.gather(
                *(
                    _crawl_fetch(client, url, semaphore=semaphore, delay_ms=delay_ms)
                    for url in batch
                )
            )
            next_frontier: list[str] = []
            for url, body, error in results:  # sequential: BFS order stays deterministic
                if body is None:
                    errors.append(f"{url}: {error}")
                    continue
                try:
                    parsed = parsers.extract(url, body, "text/html")
                except ParseError as exc:
                    errors.append(f"{url}: {exc}")
                    continue
                digest = hash((parsed.title, parsed.text))
                if digest not in seen_content:
                    seen_content.add(digest)
                    docs.append(FetchedDoc(title=parsed.title, text=parsed.text, uri=url))
                if depth >= max_depth:
                    continue
                for link in _extract_links(url, body):
                    if link in visited or not _same_site(base_url, link):
                        continue
                    visited.add(link)
                    if not _path_matches(link, include_patterns, exclude_patterns):
                        continue
                    next_frontier.append(link)
            frontier = next_frontier
            depth += 1
    return docs, errors


async def _crawl_fetch(
    client: httpx.AsyncClient, url: str, *, semaphore: asyncio.Semaphore, delay_ms: int
) -> tuple[str, bytes | None, str | None]:
    """Politeness-delayed, SSRF-guarded page fetch → (url, body, error)."""
    async with semaphore:
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        try:
            check_public_url(url)
            return url, await fetch_html_bytes(url, client=client), None
        except FetchError as exc:
            return url, None, str(exc)


def _pattern_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _path_matches(url: str, include: list[str], exclude: list[str]) -> bool:
    """fnmatch the URL path against the configured globs — exclude wins, and an
    empty include list means "everything not excluded"."""
    path = urlsplit(url).path or "/"
    if any(fnmatch(path, pattern) for pattern in exclude):
        return False
    if include:
        return any(fnmatch(path, pattern) for pattern in include)
    return True


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


@dataclass
class RobotsRules:
    """`User-agent: *` prefix rules from a robots.txt.

    Matching follows the de-facto standard: the longest matching path prefix
    wins and Allow beats Disallow on equal length. An empty rule set (no
    robots.txt, a non-200, or an unparseable body) allows everything.
    """

    rules: list[tuple[str, bool]] = field(default_factory=list)  # (path prefix, allowed)

    def allows(self, url: str) -> bool:
        path = urlsplit(url).path or "/"
        allowed = True
        best = -1
        for prefix, rule_allows in self.rules:
            if not path.startswith(prefix) or len(prefix) < best:
                continue
            if len(prefix) > best or rule_allows:  # Allow wins ties
                best = len(prefix)
                allowed = rule_allows
        return allowed


def parse_robots(text: str) -> RobotsRules:
    """Parse the `User-agent: *` group(s) of a robots.txt into prefix rules.

    Consecutive User-agent lines share the following directives; a User-agent
    line after a directive starts a new group. Empty Disallow/Allow values are
    "allow all" markers and carry no prefix, so they are dropped. Anything
    unparseable simply yields no rules — i.e. allow-all.
    """
    rules: list[tuple[str, bool]] = []
    in_star_group = False
    saw_directive = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name == "user-agent":
            if saw_directive:  # directives ended the previous group
                in_star_group = False
                saw_directive = False
            in_star_group = in_star_group or value == "*"
        elif name in ("allow", "disallow"):
            saw_directive = True
            if in_star_group and value:
                rules.append((value, name == "allow"))
    return RobotsRules(rules=rules)


async def fetch_robots(client: httpx.AsyncClient, base_url: str) -> RobotsRules:
    """Fetch {origin}/robots.txt once per sync.

    Strictly best-effort: any failure at all — transport error, non-200,
    undecodable body — means allow-all, because a missing robots.txt must never
    turn into a failed sync.
    """
    parts = urlsplit(base_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        check_public_url(robots_url)
        response = await client.get(robots_url)
        if response.status_code != 200:
            return RobotsRules()
        return parse_robots(response.text[:ROBOTS_MAX_CHARS])
    except Exception:
        return RobotsRules()


def _normalize_link(url: str) -> str:
    """Strip #fragments and a bare trailing '?' (query-only dupes); keep real
    querystrings — /page?tab=1 is a distinct page."""
    url, _ = urldefrag(url)
    return url.removesuffix("?")


def _same_site(base_url: str, candidate: str) -> bool:
    """Same-site rule: equal netloc after removing "www.", and candidate path
    equal to the base path or nested under base_path + "/"."""
    base = urlsplit(base_url)
    other = urlsplit(candidate)
    if other.scheme not in ("http", "https"):
        return False
    if other.netloc.lower().removeprefix("www.") != base.netloc.lower().removeprefix("www."):
        return False
    base_path = base.path.rstrip("/")
    if not base_path:  # base is the site root — the whole host is in scope
        return True
    return other.path in (base.path, base_path) or other.path.startswith(base_path + "/")


def _extract_links(page_url: str, body: bytes) -> list[str]:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(body, "lxml")
    except Exception:
        return []
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")  # type: ignore[union-attr]
        if isinstance(href, list):
            href = href[0] if href else ""
        href = str(href or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = _normalize_link(urljoin(page_url, href))
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
    return links


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


async def fetch_github(config: dict[str, Any], secrets: dict[str, Any]) -> list[FetchedDoc]:
    """Fetch indexable repo files and/or issues/PRs via the GitHub REST API.

    Works unauthenticated for public repos; secrets["token"] is sent as a
    Bearer token when present. API/permission errors raise FetchError.
    """
    owner = str(config.get("repo_owner") or "").strip()
    repo = str(config.get("repo") or "").strip()
    if not owner or not repo:
        raise FetchError("github sources need config.repo_owner and config.repo")
    headers = {"Accept": "application/vnd.github+json"}
    token = str((secrets or {}).get("token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    include_files = bool(config.get("include_files", True))
    include_issues = bool(config.get("include_issues", False))
    include_prs = bool(config.get("include_prs", False))

    docs: list[FetchedDoc] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=headers
    ) as client:
        if include_files:
            branch = str(config.get("branch") or "").strip()
            if not branch:
                repo_info = await _github_json(client, f"{GITHUB_API}/repos/{owner}/{repo}")
                branch = str(repo_info.get("default_branch") or "main")
            docs.extend(await _github_files(client, owner, repo, branch))
        if include_issues:
            docs.extend(await _github_issues(client, owner, repo))
        if include_prs:
            docs.extend(await _github_prs(client, owner, repo))
    return docs


async def _github_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise FetchError(f"GitHub API error: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(
            f"GitHub API {response.status_code}: {_github_message(response) or 'request failed'}"
        )
    return response.json()


def _github_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("message") or "") if isinstance(payload, dict) else ""
    except Exception:
        return response.text[:200]


def _github_indexable_path(path: str) -> bool:
    if not path:
        return False
    segments = path.split("/")
    if any(segment in GITHUB_DENIED_SEGMENTS for segment in segments):
        return False
    basename = segments[-1].lower()
    if "." in basename:
        stem, extension = basename.rsplit(".", 1)
        return f".{extension}" in GITHUB_INDEXABLE_EXTENSIONS or stem in GITHUB_WELL_KNOWN_FILES
    return basename in GITHUB_WELL_KNOWN_FILES


async def _github_files(
    client: httpx.AsyncClient, owner: str, repo: str, branch: str
) -> list[FetchedDoc]:
    tree = await _github_json(
        client,
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}",
        params={"recursive": "1"},
    )
    entries = [
        entry
        for entry in tree.get("tree", [])
        if entry.get("type") == "blob"
        and _github_indexable_path(str(entry.get("path") or ""))
        and int(entry.get("size") or 0) <= GITHUB_MAX_FILE_BYTES
    ][:GITHUB_MAX_FILES]
    docs: list[FetchedDoc] = []
    for entry in entries:
        path = str(entry["path"])
        text = await _github_file_content(client, owner, repo, branch, path)
        mime = (
            "text/markdown" if path.lower().endswith(GITHUB_MARKDOWN_EXTENSIONS) else "text/plain"
        )
        docs.append(
            FetchedDoc(
                title=path,
                text=text,
                uri=f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
                mime=mime,
            )
        )
    return docs


async def _github_file_content(
    client: httpx.AsyncClient, owner: str, repo: str, branch: str, path: str
) -> str:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    try:
        response = await client.get(
            url, params={"ref": branch}, headers={"Accept": "application/vnd.github.raw+json"}
        )
    except httpx.HTTPError as exc:
        raise FetchError(f"GitHub API error: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code != 200:
        raise FetchError(
            f"GitHub API {response.status_code} for {path}: "
            f"{_github_message(response) or 'request failed'}"
        )
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/json":
        # Raw media type unsupported → JSON metadata with base64 "content".
        payload = response.json()
        encoded = str(payload.get("content") or "") if isinstance(payload, dict) else ""
        try:
            return base64.b64decode(encoded).decode("utf-8", errors="replace")
        except Exception as exc:
            raise FetchError(f"Could not decode GitHub content for {path}") from exc
    return response.text


async def _github_issues(client: httpx.AsyncClient, owner: str, repo: str) -> list[FetchedDoc]:
    issues = await _github_json(
        client,
        f"{GITHUB_API}/repos/{owner}/{repo}/issues",
        params={"state": "all", "per_page": "50", "sort": "updated"},
    )
    docs: list[FetchedDoc] = []
    for issue in issues:
        if "pull_request" in issue:  # the issues endpoint interleaves PRs
            continue
        number = issue.get("number")
        title = str(issue.get("title") or "")
        parts = [title, str(issue.get("body") or "")]
        if int(issue.get("comments") or 0) > 0:
            comments = await _github_json(
                client,
                f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments",
                params={"per_page": "20"},
            )
            parts.extend(str(comment.get("body") or "") for comment in comments[:20])
        docs.append(
            FetchedDoc(
                title=f"#{number} {title}",
                text="\n\n".join(part for part in parts if part.strip()),
                uri=str(issue.get("html_url") or ""),
                mime="text/markdown",
            )
        )
    return docs


async def _github_prs(client: httpx.AsyncClient, owner: str, repo: str) -> list[FetchedDoc]:
    pulls = await _github_json(
        client,
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        params={"state": "all", "per_page": "50"},
    )
    docs: list[FetchedDoc] = []
    for pull in pulls:
        number = pull.get("number")
        title = str(pull.get("title") or "")
        body = str(pull.get("body") or "")
        docs.append(
            FetchedDoc(
                title=f"#{number} {title}",
                text="\n\n".join(part for part in (title, body) if part.strip()),
                uri=str(pull.get("html_url") or ""),
                mime="text/markdown",
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------


async def fetch_notion(
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    workspace_id: str | None = None,
) -> list[FetchedDoc]:
    """Fetch Notion pages (search API, or traversal from root_page_id) and
    render their blocks to markdown-ish text.

    Auth modes: "token" (secrets.token, internal integration — the default) or
    "oauth" (config.connection_id, bearer via the integrations token seam).
    """
    if _connector_auth_mode(config) == "oauth":
        token, _ = await _connection_access_token(
            session, workspace_id, str(config.get("connection_id") or "").strip(), provider="notion"
        )
    else:
        token = str((secrets or {}).get("token") or "").strip()
        if not token:
            raise FetchError("notion sources need secrets.token (internal integration token)")
    max_pages = int(config.get("max_pages") or NOTION_DEFAULT_MAX_PAGES)
    root_page_id = str(config.get("root_page_id") or "").strip()
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}

    docs: list[FetchedDoc] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=headers
    ) as client:
        if root_page_id:
            # The search API misses pages in some workspaces — traverse instead,
            # discovering child_page blocks (they count toward max_pages).
            queue: deque[str] = deque([root_page_id])
            seen: set[str] = {root_page_id}
            while queue and len(docs) < max_pages:
                page_id = queue.popleft()
                page = await _notion_json(client, "GET", f"{NOTION_API}/pages/{page_id}")
                child_ids: list[str] = []
                text = await _notion_page_text(client, page_id, child_pages=child_ids)
                docs.append(_notion_doc(page, text))
                for child_id in child_ids:
                    if child_id not in seen:
                        seen.add(child_id)
                        queue.append(child_id)
        else:
            for page in await _notion_search_pages(client, max_pages):
                page_id = str(page.get("id") or "")
                text = await _notion_page_text(client, page_id, child_pages=None)
                docs.append(_notion_doc(page, text))
    return docs


def _notion_doc(page: dict[str, Any], text: str) -> FetchedDoc:
    return FetchedDoc(
        title=_notion_page_title(page),
        text=text,
        uri=str(page.get("url") or ""),
        mime="text/markdown",
    )


async def _notion_search_pages(client: httpx.AsyncClient, max_pages: int) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(pages) < max_pages:
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        data = await _notion_json(client, "POST", f"{NOTION_API}/search", json=body)
        pages.extend(
            page
            for page in data.get("results", [])
            if isinstance(page, dict) and page.get("object") == "page"
        )
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    return pages[:max_pages]


async def _notion_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, json=json, params=params)
    except httpx.HTTPError as exc:
        raise FetchError(f"Notion API error: {exc.__class__.__name__}: {exc}") from exc
    if response.status_code in (401, 403):
        raise FetchError(
            f"Notion API {response.status_code}: check the integration token + page permissions"
        )
    if response.status_code != 200:
        message = ""
        try:
            payload = response.json()
            message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
        except Exception:
            message = response.text[:200]
        raise FetchError(f"Notion API {response.status_code}: {message or 'request failed'}")
    data = response.json()
    return data if isinstance(data, dict) else {}


def _notion_page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = _notion_plain_text(prop.get("title"))
                if title:
                    return title
    return "Untitled"


async def _notion_page_text(
    client: httpx.AsyncClient, page_id: str, *, child_pages: list[str] | None
) -> str:
    lines: list[str] = []
    await _notion_render_children(client, page_id, lines, depth=0, child_pages=child_pages)
    return "\n\n".join(lines)


async def _notion_render_children(
    client: httpx.AsyncClient,
    block_id: str,
    lines: list[str],
    *,
    depth: int,
    child_pages: list[str] | None,
) -> None:
    cursor: str | None = None
    while True:
        params = {"page_size": "100"}
        if cursor:
            params["start_cursor"] = cursor
        data = await _notion_json(
            client, "GET", f"{NOTION_API}/blocks/{block_id}/children", params=params
        )
        for block in data.get("results", []):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "child_page":
                # Only followed when traversing from a root page; search mode
                # already surfaces child pages as their own results.
                if child_pages is not None:
                    child_id = str(block.get("id") or "")
                    if child_id:
                        child_pages.append(child_id)
                continue
            line = _notion_render_block(block, block_type)
            if line:
                lines.append(line)
            if block.get("has_children") and depth < NOTION_BLOCK_DEPTH:
                await _notion_render_children(
                    client,
                    str(block.get("id") or ""),
                    lines,
                    depth=depth + 1,
                    child_pages=child_pages,
                )
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            return


_NOTION_BLOCK_PREFIXES = {
    "paragraph": "",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
    "callout": "",
    "toggle": "",
}


def _notion_render_block(block: dict[str, Any], block_type: str) -> str | None:
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return None
    text = _notion_plain_text(payload.get("rich_text"))
    if not text:
        return None
    if block_type == "to_do":
        marker = "x" if payload.get("checked") else " "
        return f"- [{marker}] {text}"
    if block_type == "code":
        language = str(payload.get("language") or "")
        return f"```{language}\n{text}\n```"
    prefix = _NOTION_BLOCK_PREFIXES.get(block_type)
    if prefix is None:  # unknown block type — skip
        return None
    return f"{prefix}{text}"


def _notion_plain_text(rich_text: Any) -> str:
    if not isinstance(rich_text, list):
        return ""
    return "".join(
        str(part.get("plain_text") or "") for part in rich_text if isinstance(part, dict)
    ).strip()


# ---------------------------------------------------------------------------
# shared connector plumbing: auth modes, token seam, rate limits, html→text
# ---------------------------------------------------------------------------


def _connector_auth_mode(config: dict[str, Any]) -> str:
    """ "oauth" | "token": explicit config.auth wins, else a connection_id
    implies oauth. Unvalidated configs may return other strings — callers that
    care validate in `app.services.knowledge`."""
    auth = config.get("auth")
    if auth is None:
        return "oauth" if str(config.get("connection_id") or "").strip() else "token"
    return str(auth)


async def _connection_access_token(
    session: AsyncSession | None,
    workspace_id: str | None,
    connection_id: str,
    *,
    provider: str,
) -> tuple[str, IntegrationConnection]:
    """Resolve a live access token for an OAuth-connected source through the
    `app.integrations.tokens` seam (late-bound import: BE-A owns the module,
    tests monkeypatch its functions). Never log the returned token."""
    if not connection_id:
        raise FetchError(f"{provider} OAuth sources need config.connection_id")
    if session is None or not workspace_id:
        raise FetchError("OAuth-connected sources can only sync inside a workspace sync task")
    tokens: Any = importlib.import_module("app.integrations.tokens")
    connection: IntegrationConnection = await tokens.get_connection(
        session, workspace_id, connection_id
    )
    if connection.provider != provider:
        raise FetchError(
            f"Connection {connection_id} is a {connection.provider!r} connection; "
            f"this source needs {provider!r}"
        )
    token = str(await tokens.get_valid_access_token(session, connection))
    return token, connection


def retry_after_seconds(response: httpx.Response) -> float:
    """Sleep budget for a 429: the Retry-After header, capped — a provider
    asking for a 15-minute pause must not park the sync worker that long."""
    raw = str(response.headers.get("retry-after") or "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = RATE_LIMIT_DEFAULT_SLEEP_SECONDS
    return max(0.0, min(seconds, RATE_LIMIT_MAX_SLEEP_SECONDS))


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    api_name: str,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """One API request that waits out 429s (Retry-After, capped) before failing.
    Transport errors and terminal 429s raise FetchError; other statuses are the
    caller's to judge."""
    attempts = RATE_LIMIT_MAX_RETRIES + 1
    for attempt in range(attempts):
        try:
            response = await client.request(method, url, params=params)
        except httpx.HTTPError as exc:
            raise FetchError(f"{api_name} error: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code != 429:
            return response
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_after_seconds(response))
    raise FetchError(f"{api_name} rate limited (HTTP 429) — will retry on the next sync")


def _html_fragment_text(name: str, html: str) -> str:
    """HTML fragment → text via the same extractor the crawl connector uses
    (Confluence storage-format XHTML, Zendesk article bodies, Drive .html)."""
    if not html.strip():
        return ""
    try:
        return parsers.extract(f"{name or 'page'}.html", html.encode("utf-8"), "text/html").text
    except ParseError:
        return ""


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------


async def fetch_confluence(
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    workspace_id: str | None = None,
) -> list[FetchedDoc]:
    """Fetch Confluence Cloud pages (v2 API) as text documents.

    Auth modes: "oauth" (Atlassian 3LO connection via the token seam, API base
    api.atlassian.com/ex/confluence/{cloud_id}) or "token" (config.base_url +
    config.email + secrets.api_token Basic auth). Global spaces are enumerated,
    filtered to config.space_keys when set, and each page's storage-format
    XHTML is converted with the crawl connector's HTML→text extractor.
    Incremental sync stays content-hash based (no CQL lastmod watermark this
    wave). FetchedDoc.uri is the page's absolute webui URL.
    """
    max_pages = min(
        int(config.get("max_pages") or CONFLUENCE_DEFAULT_MAX_PAGES), CONFLUENCE_MAX_PAGES_CAP
    )
    space_keys = {
        key.strip().lower()
        for key in config.get("space_keys") or []
        if isinstance(key, str) and key.strip()
    }
    auth: tuple[str, str] | None = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if _connector_auth_mode(config) == "oauth":
        token, connection = await _connection_access_token(
            session,
            workspace_id,
            str(config.get("connection_id") or "").strip(),
            provider="confluence",
        )
        meta = connection.meta or {}
        cloud_id = str(config.get("cloud_id") or meta.get("cloud_id") or "").strip()
        if not cloud_id:
            raise FetchError(
                "confluence oauth sources need a site: set config.cloud_id "
                "(the connection can access several sites)"
            )
        api_root = f"{CONFLUENCE_CLOUD_API}/{cloud_id}"
        web_base = _confluence_site_url(meta, cloud_id)
        headers["Authorization"] = f"Bearer {token}"
    else:
        base_url = str(config.get("base_url") or "").strip().rstrip("/").removesuffix("/wiki")
        email = str(config.get("email") or "").strip()
        api_token = str((secrets or {}).get("api_token") or "").strip()
        if not base_url or not email or not api_token:
            raise FetchError(
                "confluence token-auth sources need config.base_url, config.email "
                "and secrets.api_token"
            )
        check_public_url(base_url)
        api_root = base_url
        web_base = base_url
        auth = (email, api_token)

    docs: list[FetchedDoc] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=headers, auth=auth
    ) as client:
        spaces = await _confluence_list(
            client,
            api_root,
            "/wiki/api/v2/spaces",
            params={"type": "global", "limit": str(CONFLUENCE_PAGE_LIMIT)},
        )
        for space in spaces:
            key = str(space.get("key") or "")
            if space_keys and key.lower() not in space_keys:
                continue
            if len(docs) >= max_pages:
                break
            pages = await _confluence_list(
                client,
                api_root,
                f"/wiki/api/v2/spaces/{space.get('id')}/pages",
                params={"body-format": "storage", "limit": str(CONFLUENCE_PAGE_LIMIT)},
                cap=max_pages - len(docs),
            )
            for page in pages:
                docs.append(_confluence_doc(page, web_base=web_base, space_key=key))
    return docs


def _confluence_site_url(meta: dict[str, Any], cloud_id: str) -> str:
    """Human site URL for webui links: meta.site_url, else the matching entry
    in meta.sites (multi-site connections store the accessible-resources list)."""
    site_url = str(meta.get("site_url") or "").strip()
    if not site_url:
        for site in meta.get("sites") or []:
            if isinstance(site, dict) and str(site.get("id") or "") == cloud_id:
                site_url = str(site.get("url") or "").strip()
                break
    if not site_url:
        raise FetchError(
            "The Confluence connection has no site URL for this cloud_id — reconnect it"
        )
    return site_url.rstrip("/").removesuffix("/wiki")


def _confluence_doc(page: dict[str, Any], *, web_base: str, space_key: str) -> FetchedDoc:
    title = str(page.get("title") or "Untitled")
    body = page.get("body") or {}
    storage = body.get("storage") if isinstance(body, dict) else None
    value = str(storage.get("value") or "") if isinstance(storage, dict) else ""
    links = page.get("_links") or {}
    webui = str(links.get("webui") or "") if isinstance(links, dict) else ""
    if webui.startswith(("http://", "https://")):
        uri = webui
    elif webui:
        uri = f"{web_base}/wiki{webui}"
    else:  # v2 always sends webui; keep upserts collision-free if it ever misses
        uri = f"{web_base}/wiki/pages/{page.get('id')}"
    return FetchedDoc(
        title=title,
        text=_html_fragment_text(title, value),
        uri=uri,
        mime="text/html",
        meta={"space": space_key} if space_key else {},
    )


async def _confluence_list(
    client: httpx.AsyncClient,
    api_root: str,
    path: str,
    *,
    params: dict[str, str] | None,
    cap: int = CONFLUENCE_MAX_PAGES_CAP,
) -> list[dict[str, Any]]:
    """GET a v2 collection, following `_links.next` cursor links up to `cap`.
    Absolute next links are only followed when they stay on the API host."""
    results: list[dict[str, Any]] = []
    url: str | None = api_root + path
    while url and len(results) < cap:
        response = await _request_with_retry(
            client, "GET", url, api_name="Confluence API", params=params
        )
        params = None  # `_links.next` embeds the cursor query
        if response.status_code in (401, 403):
            raise FetchError(
                f"Confluence API {response.status_code}: check the credentials and scopes"
            )
        if response.status_code != 200:
            raise FetchError(
                f"Confluence API {response.status_code}: {response.text[:200] or 'request failed'}"
            )
        data = response.json()
        next_link = ""
        if isinstance(data, dict):
            results.extend(item for item in data.get("results") or [] if isinstance(item, dict))
            links = data.get("_links") or {}
            next_link = str(links.get("next") or "") if isinstance(links, dict) else ""
        if not next_link:
            break
        if next_link.startswith(("http://", "https://")):
            url = next_link if next_link.startswith(api_root) else None
        else:
            url = api_root + next_link
    return results[:cap]


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


async def fetch_gdrive(
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    workspace_id: str | None = None,
) -> tuple[list[FetchedDoc], list[str]]:
    """Fetch files from the configured Drive folders (recursing subfolders)
    via an OAuth google connection. Returns (docs, skip notes).

    Export routing: Google Docs → markdown, Sheets → CSV (rendered as the same
    markdown table the CSV upload parser makes), Slides → plain text; raw
    .md/.txt/.html files ≤2MB are downloaded directly. PDF/DOCX and oversized
    raw files are skipped with a note — returned separately so the sync records
    them in the source error summary without treating the folder listing as
    incomplete. uri = webViewLink.
    """
    folder_ids = [item.strip() for item in config.get("folder_ids") or [] if isinstance(item, str)]
    folder_ids = [item for item in folder_ids if item]
    if not folder_ids:
        raise FetchError("gdrive sources need config.folder_ids (at least one Drive folder id)")
    max_files = min(int(config.get("max_files") or GDRIVE_DEFAULT_MAX_FILES), GDRIVE_MAX_FILES_CAP)
    token, _ = await _connection_access_token(
        session, workspace_id, str(config.get("connection_id") or "").strip(), provider="google"
    )

    docs: list[FetchedDoc] = []
    notes: list[str] = []
    queue: deque[str] = deque(folder_ids)
    visited: set[str] = set(folder_ids)
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        while queue and len(docs) < max_files:
            folder_id = queue.popleft()
            for file in await _gdrive_list_folder(client, folder_id):
                mime = str(file.get("mimeType") or "")
                file_id = str(file.get("id") or "")
                if mime == GDRIVE_FOLDER_MIME:
                    if file_id and file_id not in visited:
                        visited.add(file_id)
                        queue.append(file_id)
                    continue
                if len(docs) >= max_files:
                    break
                doc, note = await _gdrive_file_doc(client, file, mime)
                if doc is not None:
                    docs.append(doc)
                if note:
                    notes.append(note)
    return docs, notes


async def _gdrive_list_folder(client: httpx.AsyncClient, folder_id: str) -> list[dict[str, Any]]:
    """files.list for one folder, following nextPageToken."""
    escaped = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    files: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params = {
            "q": f"'{escaped}' in parents and trashed=false",
            "fields": GDRIVE_LIST_FIELDS,
            "pageSize": "100",
        }
        if page_token:
            params["pageToken"] = page_token
        response = await _request_with_retry(
            client, "GET", f"{GDRIVE_API}/files", api_name="Google Drive API", params=params
        )
        if response.status_code in (401, 403):
            raise FetchError(
                f"Google Drive API {response.status_code}: reconnect Google in "
                "Settings → Integrations (drive.readonly scope required)"
            )
        if response.status_code != 200:
            raise FetchError(
                f"Google Drive API {response.status_code}: "
                f"{response.text[:200] or 'request failed'}"
            )
        data = response.json()
        files.extend(item for item in data.get("files") or [] if isinstance(item, dict))
        page_token = str(data.get("nextPageToken") or "")
        if not page_token:
            return files


async def _gdrive_file_doc(
    client: httpx.AsyncClient, file: dict[str, Any], mime: str
) -> tuple[FetchedDoc | None, str | None]:
    """One Drive file → (doc, skip note). Non-indexable types (images, zips…)
    yield neither; PDF/DOCX/oversized yield a note; export/download failures
    raise (they mean the listing cannot be trusted as complete)."""
    file_id = str(file.get("id") or "")
    name = str(file.get("name") or "Untitled")
    uri = str(file.get("webViewLink") or "") or f"https://drive.google.com/file/d/{file_id}/view"
    meta: dict[str, Any] = {}
    if file.get("modifiedTime"):
        meta["modified"] = str(file["modifiedTime"])

    export_mime = GDRIVE_EXPORTS.get(mime)
    if export_mime is not None:
        response = await _request_with_retry(
            client,
            "GET",
            f"{GDRIVE_API}/files/{file_id}/export",
            api_name="Google Drive API",
            params={"mimeType": export_mime},
        )
        if response.status_code != 200:
            raise FetchError(f"Google Drive export failed for {name}: HTTP {response.status_code}")
        text = _sheet_markdown(name, response.text) if export_mime == "text/csv" else response.text
        return FetchedDoc(title=name, text=text, uri=uri, mime=export_mime, meta=meta), None

    lowered = name.lower()
    if mime in GDRIVE_RAW_TEXT_MIMES or lowered.endswith(GDRIVE_RAW_TEXT_EXTENSIONS):
        if int(file.get("size") or 0) > GDRIVE_RAW_MAX_BYTES:
            return None, f"Skipped {name}: exceeds the 2MB raw file limit"
        response = await _request_with_retry(
            client,
            "GET",
            f"{GDRIVE_API}/files/{file_id}",
            api_name="Google Drive API",
            params={"alt": "media"},
        )
        if response.status_code != 200:
            raise FetchError(
                f"Google Drive download failed for {name}: HTTP {response.status_code}"
            )
        if len(response.content) > GDRIVE_RAW_MAX_BYTES:
            return None, f"Skipped {name}: exceeds the 2MB raw file limit"
        raw = response.content.decode("utf-8", errors="replace")
        if mime == "text/html" or lowered.endswith((".html", ".htm")):
            return FetchedDoc(
                title=name,
                text=_html_fragment_text(name, raw),
                uri=uri,
                mime="text/html",
                meta=meta,
            ), None
        doc_mime = "text/markdown" if lowered.endswith((".md", ".markdown")) else "text/plain"
        return FetchedDoc(title=name, text=raw, uri=uri, mime=doc_mime, meta=meta), None

    skipped = GDRIVE_SKIPPED_MIMES.get(mime)
    if skipped is not None:
        return None, f"Skipped {name}: {skipped} files are not indexed yet"
    return None, None


def _sheet_markdown(name: str, csv_text: str) -> str:
    """Sheets CSV export → the markdown table the CSV upload parser produces."""
    try:
        return parsers.extract(f"{name or 'sheet'}.csv", csv_text.encode("utf-8"), "text/csv").text
    except ParseError:
        return csv_text


# ---------------------------------------------------------------------------
# Zendesk help center
# ---------------------------------------------------------------------------


async def fetch_zendesk(
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    workspace_id: str | None = None,
) -> tuple[list[FetchedDoc], list[str]]:
    """Incrementally fetch Help Center articles changed since config.sync_cursor.

    Uses the incremental articles API (Basic auth `email/token:api_token`,
    rate-limited to 10 req/min — 429s are waited out) and writes the returned
    end_time back into config["sync_cursor"]; the sync task persists the
    mutated config. Only the configured locale is kept, drafts are skipped,
    and archived articles come back as the second element so the sync prunes
    exactly those — unchanged articles are absent from an incremental listing,
    so full-listing pruning must never run for this type. uri = html_url.
    """
    subdomain = str(config.get("subdomain") or "").strip().lower()
    if not ZENDESK_SUBDOMAIN_RE.fullmatch(subdomain):
        raise FetchError("zendesk sources need config.subdomain (letters/digits/hyphens only)")
    email = str((secrets or {}).get("email") or "").strip()
    api_token = str((secrets or {}).get("api_token") or "").strip()
    if not email or not api_token:
        raise FetchError("zendesk sources need secrets.email and secrets.api_token")
    locale = str(config.get("locale") or ZENDESK_DEFAULT_LOCALE).strip().lower()
    cursor = config.get("sync_cursor")
    start_time = cursor if isinstance(cursor, int) and not isinstance(cursor, bool) else 0
    start_time = max(start_time, 0)
    base = f"https://{subdomain}.zendesk.com"
    check_public_url(base)

    docs: list[FetchedDoc] = []
    archived: list[str] = []
    end_time = start_time
    url = f"{base}/api/v2/help_center/incremental/articles"
    params: dict[str, str] | None = {"start_time": str(start_time)}
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        auth=(f"{email}/token", api_token),
    ) as client:
        for _ in range(ZENDESK_MAX_SYNC_PAGES):
            response = await _request_with_retry(
                client, "GET", url, api_name="Zendesk API", params=params
            )
            params = None  # next_page embeds the query
            if response.status_code in (401, 403):
                raise FetchError(
                    f"Zendesk API {response.status_code}: check secrets.email/api_token "
                    "and that API token access is enabled"
                )
            if response.status_code != 200:
                raise FetchError(
                    f"Zendesk API {response.status_code}: {response.text[:200] or 'failed'}"
                )
            data = response.json()
            if not isinstance(data, dict):
                raise FetchError("Zendesk API returned an unexpected payload")
            articles = [a for a in data.get("articles") or [] if isinstance(a, dict)]
            for article in articles:
                if str(article.get("locale") or "").lower() != locale:
                    continue
                uri = str(article.get("html_url") or "")
                if not uri:
                    continue
                if article.get("archived"):
                    archived.append(uri)
                    continue
                if article.get("draft"):
                    continue
                title = str(article.get("title") or "Untitled")
                docs.append(
                    FetchedDoc(
                        title=title,
                        text=_html_fragment_text(title, str(article.get("body") or "")),
                        uri=uri,
                        mime="text/html",
                    )
                )
            raw_end = data.get("end_time")
            if isinstance(raw_end, int) and not isinstance(raw_end, bool):
                end_time = max(end_time, raw_end)
            next_page = str(data.get("next_page") or "")
            # Only follow same-host continuation links the API hands back.
            if not articles or not next_page.startswith(base):
                break
            url = next_page
    if end_time != start_time:
        config["sync_cursor"] = end_time
    return docs, archived
