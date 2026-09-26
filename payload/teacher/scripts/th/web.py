"""Bounded web access, executed by this Skill.

The model never reaches the network. It may *ask* for `arxiv_search` or
`fetch_url`; this Skill checks the policy, performs the request itself with a
timeout and a byte ceiling, strips markup, and hands the model a bounded
excerpt. Fetched text is untrusted data: it can inform an answer, it cannot
change the workflow, because every model output is a closed structure that the
Skill validates.

Policy (from the configuration, never from the model):

    enabled         network operations are refused when false
    allow_domains   `fetch_url` accepts only these hosts (and their subdomains)
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Sequence

USER_AGENT = "TeacherSkill/1.0 (local research assistant)"
MAX_RESPONSE_BYTES = 1_500_000
DEFAULT_TIMEOUT = 20.0
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"

DEFAULT_ALLOW_DOMAINS = (
    "arxiv.org",
    "openreview.net",
    "aclanthology.org",
    "proceedings.neurips.cc",
    "papers.nips.cc",
    "proceedings.mlr.press",
    "jmlr.org",
    "github.com",
    "raw.githubusercontent.com",
    "pytorch.org",
    "docs.python.org",
    "numpy.org",
    "scikit-learn.org",
    "huggingface.co",
    "en.wikipedia.org",
    "zh.wikipedia.org",
)


class WebRefused(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def host_allowed(url: str, allow_domains: Sequence[str]) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return False
    host = parsed.hostname.casefold()
    return any(host == domain or host.endswith("." + domain) for domain in allow_domains)


def _get(url: str, *, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - policy-checked
            content_type = response.headers.get("Content-Type", "")
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise WebRefused("operation_failed", f"http {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WebRefused("operation_failed", "network error") from exc
    return data[:MAX_RESPONSE_BYTES], content_type


_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = re.sub(r"<(br|p|div|li|h[1-6]|tr)[^>]*>", "\n", text, flags=re.I)
    text = _ANY_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_url(url: str, policy: Mapping[str, Any], *, max_chars: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    if not policy.get("enabled"):
        raise WebRefused("operation_outside_scope", "network disabled")
    allow = tuple(policy.get("allow_domains") or DEFAULT_ALLOW_DOMAINS)
    if not host_allowed(url, allow):
        raise WebRefused("operation_outside_scope", "host not allowed")
    data, content_type = _get(url, timeout=timeout)
    if "pdf" in content_type.casefold() or url.casefold().endswith(".pdf"):
        raise WebRefused("operation_failed", "pdf responses are not parsed; use the abs page or a local copy")
    text = html_to_text(data.decode("utf-8", errors="replace")) if "html" in content_type.casefold() or b"<html" in data[:2000].lower() else data.decode("utf-8", errors="replace")
    return {"url": url, "text": text[:max_chars], "truncated": len(text) > max_chars, "chars": len(text)}


def arxiv_search(query: str, policy: Mapping[str, Any], *, max_results: int = 6, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    if not policy.get("enabled"):
        raise WebRefused("operation_outside_scope", "network disabled")
    params = urllib.parse.urlencode({
        "search_query": "all:" + " AND all:".join(part for part in query.split() if part),
        "start": 0,
        "max_results": max(1, min(max_results, 10)),
        "sortBy": "relevance",
    })
    data, _ = _get(f"{ARXIV_ENDPOINT}?{params}", timeout=timeout)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise WebRefused("operation_failed", "unparseable response") from exc
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for entry in root.findall("a:entry", ns):
        identifier = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
        published = (entry.findtext("a:published", default="", namespaces=ns) or "")[:10]
        authors = [" ".join((node.findtext("a:name", default="", namespaces=ns) or "").split())
                   for node in entry.findall("a:author", ns)][:4]
        entries.append({
            "id": identifier.rsplit("/", 1)[-1],
            "url": identifier,
            "title": title,
            "published": published,
            "authors": authors,
            "summary": summary[:900],
        })
    total_text = root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") or str(len(entries))
    try:
        total = int(total_text)
    except ValueError:
        total = len(entries)
    return {"entries": entries, "total_matches": total, "omitted": max(0, total - len(entries))}
