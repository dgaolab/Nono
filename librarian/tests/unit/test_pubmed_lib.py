import io
import json
import os
import sys

import pytest

from nono_librarian.lib import pubmed


class _Resp(io.BytesIO):
    """A BytesIO that doubles as a context manager, like urlopen's return."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener_returning(body):
    """Build an _opener that returns ``body`` (str or bytes) and records the URL."""
    calls = {}

    def opener(url, timeout=None):
        calls["url"] = url
        data = body.encode("utf-8") if isinstance(body, str) else body
        return _Resp(data)

    opener.calls = calls
    return opener


# --------------------------------------------------------------------------
# graceful-degradation contract
# --------------------------------------------------------------------------

def test_unavailable_is_runtimeerror():
    assert issubclass(pubmed.PubMedUnavailable, RuntimeError)


# --------------------------------------------------------------------------
# esearch
# --------------------------------------------------------------------------

def test_esearch_returns_idlist():
    opener = _opener_returning(json.dumps({"esearchresult": {"idlist": ["111", "222"]}}))
    out = pubmed.esearch("epilepsy", _opener=opener)
    assert out == ["111", "222"]


def test_esearch_passes_date_window_and_retmax():
    opener = _opener_returning(json.dumps({"esearchresult": {"idlist": []}}))
    pubmed.esearch("epilepsy", retmax=50, mindate="2020/01/01", maxdate="2021/01/01",
                   _opener=opener)
    url = opener.calls["url"]
    assert "retmax=50" in url
    assert "mindate=2020%2F01%2F01" in url
    assert "maxdate=2021%2F01%2F01" in url
    assert "datetype=edat" in url


def test_esearch_includes_api_key_when_set(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "secret123")
    opener = _opener_returning(json.dumps({"esearchresult": {"idlist": []}}))
    pubmed.esearch("epilepsy", _opener=opener)
    assert "api_key=secret123" in opener.calls["url"]


def test_esearch_raises_on_endpoint_failure():
    def boom(url, timeout=None):
        raise OSError("connection refused")

    with pytest.raises(pubmed.PubMedUnavailable):
        pubmed.esearch("epilepsy", _opener=boom)


def test_esearch_raises_on_bad_shape():
    opener = _opener_returning(json.dumps({"unexpected": True}))
    with pytest.raises(pubmed.PubMedUnavailable):
        pubmed.esearch("epilepsy", _opener=opener)


# --------------------------------------------------------------------------
# fetch_metadata / efetch XML parsing
# --------------------------------------------------------------------------

_EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <Journal><Title>Nature Neuroscience</Title>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>SCN1A and epilepsy</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">First part.</AbstractText>
          <AbstractText Label="RESULTS">Second part.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author>
          <Author><LastName>Doe</LastName><ForeName>John</ForeName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
          <PublicationType>Randomized Controlled Trial</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">111</ArticleId>
        <ArticleId IdType="pmc">PMC7654321</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_fetch_metadata_parses_record():
    opener = _opener_returning(_EFETCH_XML)
    out = pubmed.fetch_metadata(["111"], _opener=opener)
    rec = out["111"]
    assert rec["title"] == "SCN1A and epilepsy"
    assert rec["journal"] == "Nature Neuroscience"
    assert rec["year"] == "2021"
    assert rec["abstract"] == "First part. Second part."
    assert rec["publication_types"] == ["Journal Article", "Randomized Controlled Trial"]
    assert rec["pmcid"] == "PMC7654321"
    assert rec["authors"] == [
        {"first_name": "Jane", "last_name": "Smith"},
        {"first_name": "John", "last_name": "Doe"},
    ]


def test_fetch_metadata_omits_invalid_pmid():
    opener = _opener_returning("<PubmedArticleSet></PubmedArticleSet>")
    out = pubmed.fetch_metadata(["999"], _opener=opener)
    assert out == {}


def test_fetch_metadata_pmcid_none_when_absent():
    xml = _EFETCH_XML.replace(
        '<ArticleId IdType="pmc">PMC7654321</ArticleId>', "")
    opener = _opener_returning(xml)
    out = pubmed.fetch_metadata(["111"], _opener=opener)
    assert out["111"]["pmcid"] is None


def test_fetch_metadata_raises_on_endpoint_failure():
    def boom(url, timeout=None):
        raise OSError("connection refused")

    with pytest.raises(pubmed.PubMedUnavailable):
        pubmed.fetch_metadata(["111"], _opener=boom)


# --------------------------------------------------------------------------
# fetch_full_text / JATS body parsing
# --------------------------------------------------------------------------

_PMC_XML = """<?xml version="1.0"?>
<pmc-articleset>
  <article>
    <body>
      <sec><title>Introduction</title><p>Epilepsy is common.</p></sec>
      <sec><p>SCN1A variants matter.</p></sec>
    </body>
  </article>
</pmc-articleset>"""


def test_parse_full_text_joins_paragraphs():
    text = pubmed.parse_full_text(_PMC_XML)
    assert "Epilepsy is common." in text
    assert "SCN1A variants matter." in text
    # paragraphs are separated, not run together
    assert "common.SCN1A" not in text


def test_fetch_full_text_uses_pmc_db():
    opener = _opener_returning(_PMC_XML)
    text = pubmed.fetch_full_text("PMC7654321", _opener=opener)
    assert "Epilepsy is common." in text
    assert "db=pmc" in opener.calls["url"]
    assert "PMC7654321" in opener.calls["url"]


def test_fetch_full_text_empty_when_no_body():
    opener = _opener_returning(
        "<pmc-articleset><article></article></pmc-articleset>")
    assert pubmed.fetch_full_text("PMC1", _opener=opener) == ""


def test_fetch_full_text_raises_on_endpoint_failure():
    def boom(url, timeout=None):
        raise OSError("connection refused")

    with pytest.raises(pubmed.PubMedUnavailable):
        pubmed.fetch_full_text("PMC1", _opener=boom)
