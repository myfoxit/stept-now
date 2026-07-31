"""Knowledge connectors: sitemap, recursive crawl, GitHub, Notion.

Each connector turns remote content into `FetchedDoc`s; the sync task in
`app.rag.tasks` upserts them into Documents by (source_id, uri) and ingests.
All HTTP goes through httpx.AsyncClient (10s timeout, redirects followed).
Sitemap/crawl page fetches reuse the limits enforced by `app.rag.tasks`
(2MB cap, text/html only); listing failures raise `FetchError`.
"""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from app.core.config import get_settings
from app.rag import parsers
from app.rag.parsers import ParseError
from app.rag.tasks import URL_FETCH_TIMEOUT_SECONDS, FetchError, fetch_html_bytes

GITHUB_API = "https://api.github.com"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

SITEMAP_DEFAULT_MAX_PAGES = 50
SITEMAP_INDEX_DEPTH = 2  # sitemapindex recursion depth
CRAWL_DEFAULT_MAX_PAGES = 30
CRAWL_DEFAULT_MAX_DEPTH = 3
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


@dataclass
class FetchedDoc:
    """One remote document in the shape the sync task upserts + ingests."""

    title: str
    text: str
    uri: str
    mime: str = "text/html"


def check_public_url(url: str) -> None:
    """SSRF guard for user-supplied fetch targets (urls/sitemap/crawl syncs).

    Only http(s) URLs whose hostname resolves exclusively to global (public)
    addresses pass. The resolution check is skipped for the ASGI "testserver"
    host and under env=test, because tests fetch respx-mocked hostnames that
    must never hit real DNS — the scheme check still applies there.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"Unsupported URL scheme: {url}")
    hostname = parts.hostname
    if not hostname:
        raise FetchError(f"Invalid URL: {url}")
    if hostname == "testserver" or get_settings().env == "test":
        return
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError) as exc:
        raise FetchError(f"Could not resolve host: {hostname}") from exc
    if not infos:
        raise FetchError(f"Could not resolve host: {hostname}")
    for info in infos:
        address = str(info[4][0]).split("%")[0]  # strip IPv6 zone id
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError as exc:
            raise FetchError(f"{hostname} resolves to an invalid address") from exc
        if not resolved.is_global:
            raise FetchError(f"{hostname} resolves to a non-public address")


# ---------------------------------------------------------------------------
# sitemap
# ---------------------------------------------------------------------------


async def fetch_sitemap(config: dict[str, Any]) -> list[str]:
    """Return page URLs listed in the configured sitemap (recursing indexes)."""
    sitemap_url = str(config.get("sitemap_url") or "").strip()
    max_pages = int(config.get("max_pages") or SITEMAP_DEFAULT_MAX_PAGES)
    urls: list[str] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        await _collect_sitemap_urls(client, sitemap_url, urls, max_pages=max_pages, depth=0)
    if not urls:
        raise FetchError(f"{sitemap_url}: sitemap contains no URLs")
    return urls


async def _collect_sitemap_urls(
    client: httpx.AsyncClient, sitemap_url: str, urls: list[str], *, max_pages: int, depth: int
) -> None:
    if len(urls) >= max_pages:
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
                    client, child, urls, max_pages=max_pages, depth=depth + 1
                )
            if len(urls) >= max_pages:
                return
    elif tag == "urlset":
        for loc in root.iterfind(f"{namespace}url/{namespace}loc"):
            text = (loc.text or "").strip()
            if text:
                urls.append(text)
            if len(urls) >= max_pages:
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
    """
    base_url = str(config.get("base_url") or "").strip()
    max_pages = int(config.get("max_pages") or CRAWL_DEFAULT_MAX_PAGES)
    raw_depth = config.get("max_depth")
    max_depth = CRAWL_DEFAULT_MAX_DEPTH if raw_depth is None else int(raw_depth)
    check_public_url(base_url)

    start = _normalize_link(base_url)
    docs: list[FetchedDoc] = []
    errors: list[str] = []
    seen_content: set[int] = set()
    visited: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    fetched_count = 0
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        while queue and fetched_count < max_pages:
            url, depth = queue.popleft()
            fetched_count += 1
            try:
                check_public_url(url)
                body = await fetch_html_bytes(url, client=client)
                parsed = parsers.extract(url, body, "text/html")
            except (FetchError, ParseError) as exc:
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
                queue.append((link, depth + 1))
    return docs, errors


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


async def fetch_notion(config: dict[str, Any], secrets: dict[str, Any]) -> list[FetchedDoc]:
    """Fetch Notion pages (search API, or traversal from root_page_id) and
    render their blocks to markdown-ish text."""
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
