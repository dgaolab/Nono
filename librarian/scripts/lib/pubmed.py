#!/usr/bin/env python3
"""PubMed E-utilities seam — the single place nono-librarian talks to NCBI.

Replaces the Claude PubMed MCP tools for the Claude-free local build path. Like
the LLM seam (`scripts/lib/llm.py`), it uses only stdlib (`urllib`, stdlib XML),
so it adds no dependency, and every network call takes an injectable ``_opener``
so the unit suite runs with fixtures and never touches the live service.

Configuration is by environment, mirroring the existing E-utilities scripts
(`check_retractions.py`, `chase_citations.py`):

  NCBI_API_KEY  optional; when set, lifts the E-utilities rate limit and is
                appended to every request.

Any connection / HTTP / parse failure is normalized to `PubMedUnavailable` so
callers have a single thing to catch and can degrade gracefully — the same
philosophy as `LLMUnavailable` and the embeddings fallback.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUTILS_ESEARCH = EUTILS + "/esearch.fcgi"
EUTILS_EFETCH = EUTILS + "/efetch.fcgi"


class PubMedUnavailable(RuntimeError):
    """Raised when PubMed E-utilities cannot be reached or returns junk."""


def _api_key():
    return os.environ.get("NCBI_API_KEY") or None


def _get(url, _opener, timeout=30):
    """Fetch ``url`` and return the raw response bytes, or raise PubMedUnavailable."""
    try:
        with _opener(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise PubMedUnavailable(f"E-utilities request failed at {url}: {e}") from e


def esearch(query, *, retmax=100, mindate=None, maxdate=None, datetype="edat",
            _opener=urllib.request.urlopen):
    """Search PubMed and return a list of PMID strings (replaces search_articles).

    ``mindate``/``maxdate`` (``YYYY/MM/DD``) bound the search by ``datetype``
    (default ``edat`` — Entrez date added, matching the build's --since window).
    """
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": retmax,
        "datetype": datetype,
    }
    if mindate:
        params["mindate"] = mindate
    if maxdate:
        params["maxdate"] = maxdate
    key = _api_key()
    if key:
        params["api_key"] = key
    url = EUTILS_ESEARCH + "?" + urllib.parse.urlencode(params)
    raw = _get(url, _opener)
    try:
        data = json.loads(raw)
        return list(data["esearchresult"]["idlist"])
    except (ValueError, KeyError, TypeError) as e:
        raise PubMedUnavailable(f"unexpected esearch response from {url}: {e}") from e


def _text(node):
    """Joined, whitespace-collapsed text of an element subtree (or "" if None)."""
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def parse_metadata(xml_bytes):
    """Parse an efetch PubmedArticleSet into {pmid: metadata-dict} (pure).

    Each record carries title, abstract, journal, year, the structured authors
    list (first_name/last_name, used by stamp_literature.py), publication_types
    (the primary input to classify_evidence_tier.py), and pmcid (None if the
    article has no PMC full text). Articles without a PMID are skipped.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise PubMedUnavailable(f"could not parse efetch XML: {e}") from e

    out = {}
    for art in root.findall("PubmedArticle"):
        pmid = _text(art.find("MedlineCitation/PMID"))
        if not pmid:
            continue
        article = art.find("MedlineCitation/Article")
        abstracts = [_text(a) for a in article.findall("Abstract/AbstractText")]
        authors = []
        for a in article.findall("AuthorList/Author"):
            last = _text(a.find("LastName"))
            if not last:
                continue  # collective/consortium authors have no LastName
            authors.append({"first_name": _text(a.find("ForeName")), "last_name": last})
        pmcid = None
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "pmc":
                pmcid = _text(aid)
                break
        out[pmid] = {
            "title": _text(article.find("ArticleTitle")),
            "abstract": " ".join(t for t in abstracts if t),
            "journal": _text(article.find("Journal/Title")),
            "year": _text(article.find("Journal/JournalIssue/PubDate/Year")),
            "authors": authors,
            "publication_types": [
                _text(p) for p in article.findall("PublicationTypeList/PublicationType")
            ],
            "pmcid": pmcid,
        }
    return out


def fetch_metadata(pmids, *, _opener=urllib.request.urlopen):
    """efetch metadata for ``pmids`` → {pmid: dict} (replaces get_article_metadata).

    Invalid PMIDs simply do not appear in the returned dict.
    """
    pmids = list(pmids)
    if not pmids:
        return {}
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    key = _api_key()
    if key:
        params["api_key"] = key
    url = EUTILS_EFETCH + "?" + urllib.parse.urlencode(params)
    return parse_metadata(_get(url, _opener))


def parse_full_text(xml_bytes):
    """Extract body paragraph text from a PMC JATS article (pure).

    Returns the article body as paragraphs joined by blank lines, or "" when
    the article has no retrievable body (abstract-only PMC record). Callers
    treat "" as "no full text" and defer/flag per the full-text-required rule.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise PubMedUnavailable(f"could not parse PMC XML: {e}") from e
    paras = [_text(p) for p in root.findall(".//body//p")]
    return "\n\n".join(p for p in paras if p)


def fetch_full_text(pmcid, *, _opener=urllib.request.urlopen):
    """efetch PMC full text for ``pmcid`` (replaces get_full_text_article).

    Returns the body text, or "" if PMC has no full-text body for it.
    """
    params = {"db": "pmc", "id": pmcid, "retmode": "xml"}
    key = _api_key()
    if key:
        params["api_key"] = key
    url = EUTILS_EFETCH + "?" + urllib.parse.urlencode(params)
    return parse_full_text(_get(url, _opener))
