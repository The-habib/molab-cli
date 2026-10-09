"""
Unit tests for MoLab Gallery and Template Subsystem.
"""

from unittest.mock import MagicMock, patch
import pytest

from molab_cli.gallery import GalleryError, GalleryManager


def test_gallery_list_templates():
    mock_client = MagicMock()
    mock_html = '''
    <html>
        <a href="/gallery/l/neural-thickets">Neural</a>
        <a href="/gallery/l/spectral-denoising">Denoising</a>
        <a href="/gallery/l/training-neural-networks">Training</a>
    </html>
    '''
    mock_client.fetch_url.return_value = (200, mock_html, {})

    gm = GalleryManager(client=mock_client)
    templates = gm.list_templates()
    assert len(templates) == 3
    slugs = [t["slug"] for t in templates]
    assert "neural-thickets" in slugs
    assert "spectral-denoising" in slugs
    assert "training-neural-networks" in slugs


def test_gallery_search_templates():
    mock_client = MagicMock()
    mock_html = '''
    <html>
        <a href="/gallery/l/neural-thickets">Neural</a>
        <a href="/gallery/l/spectral-denoising">Denoising</a>
        <a href="/gallery/l/training-neural-networks">Training</a>
    </html>
    '''
    mock_client.fetch_url.return_value = (200, mock_html, {})

    gm = GalleryManager(client=mock_client)
    results = gm.search_templates("neural")
    assert len(results) == 2
    assert results[0]["slug"] == "neural-thickets"
    assert results[1]["slug"] == "training-neural-networks"


def test_gallery_get_template_info():
    mock_client = MagicMock()
    template_html = '''
    <html>
        <head>
            <title>Neural Thickets - molab</title>
            <meta name="description" content="Interactive loss landscapes for task experts.">
        </head>
        <body>
            <a href="https://github.com/marimo-team/gallery-examples/blob/main/notebooks/neural.py">Source</a>
        </body>
    </html>
    '''
    mock_client.fetch_url.return_value = (200, template_html, {})

    gm = GalleryManager(client=mock_client)
    info = gm.get_template_info("neural-thickets")
    assert info["slug"] == "neural-thickets"
    assert info["title"] == "Neural Thickets"
    assert "Interactive loss landscapes" in info["description"]
    assert info["github_url"] == "https://github.com/marimo-team/gallery-examples/blob/main/notebooks/neural.py"
    assert info["raw_download_url"] == "https://raw.githubusercontent.com/marimo-team/gallery-examples/main/notebooks/neural.py"


def test_gallery_download_template(tmp_path):
    mock_client = MagicMock()
    template_html = '''
    <html>
        <head><title>Neural Thickets</title></head>
        <body>
            <a href="https://github.com/marimo-team/gallery-examples/blob/main/notebooks/neural.py">Source</a>
        </body>
    </html>
    '''
    mock_client.fetch_url.return_value = (200, template_html, {})

    gm = GalleryManager(client=mock_client)
    out_file = str(tmp_path / "test_template.py")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"import marimo\napp = marimo.App()"

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        res_path = gm.download_template("neural-thickets", out_file)
        assert res_path == out_file

    with open(out_file, "r") as f:
        assert "import marimo" in f.read()
