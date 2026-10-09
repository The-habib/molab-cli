"""
MoLab Community Gallery & Template Subsystem.
Enables discovery, inspection, and retrieval of 110+ curated notebooks,
neural recipes, and data science templates directly from MoLab.
"""

import re
import urllib.request
from typing import Any, Dict, List, Optional

from molab_cli.client import MoLabClient, MOLAB_BASE
from molab_cli.exceptions import MoLabError


class GalleryError(MoLabError):
    """Raised when gallery operations fail."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="GALLERY_ERROR", details=details)


class GalleryManager:
    """Manages discovery and retrieval of MoLab gallery templates and notebooks."""

    def __init__(self, client: Optional[MoLabClient] = None):
        self.client = client or MoLabClient()
        self._cached_slugs: Optional[List[str]] = None

    def list_slugs(self) -> List[str]:
        """Fetch all unique template slugs available in the MoLab Gallery."""
        if self._cached_slugs:
            return self._cached_slugs

        status, html, _ = self.client.fetch_url(f"{MOLAB_BASE}/gallery")
        if status != 200:
            raise GalleryError(f"Failed to fetch MoLab gallery (HTTP {status})")

        items = re.findall(r'href="/gallery/l/([^"]+)"', html)
        unique = sorted(list(set(items)))
        self._cached_slugs = unique
        return unique

    def list_templates(self) -> List[Dict[str, str]]:
        """Return formatted list of all available gallery templates."""
        slugs = self.list_slugs()
        templates = []
        for slug in slugs:
            title = slug.replace("-", " ").title()
            templates.append({
                "slug": slug,
                "title": title,
                "url": f"{MOLAB_BASE}/gallery/l/{slug}",
            })
        return templates

    def search_templates(self, query: str) -> List[Dict[str, str]]:
        """Search gallery templates by keyword in slug or title."""
        q = query.lower().strip()
        all_templates = self.list_templates()
        return [
            t for t in all_templates
            if q in t["slug"].lower() or q in t["title"].lower()
        ]

    def get_template_info(self, slug: str) -> Dict[str, Any]:
        """Fetch metadata, description, and source code links for a gallery template."""
        clean_slug = slug.strip().lower().replace("/gallery/l/", "").strip("/")
        url = f"{MOLAB_BASE}/gallery/l/{clean_slug}"
        status, html, _ = self.client.fetch_url(url)
        if status != 200:
            raise GalleryError(f"Template '{clean_slug}' not found (HTTP {status})")

        # Extract title from <title> or <h1>
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
        raw_title = title_match.group(1).split("-")[0].strip() if title_match else clean_slug.replace("-", " ").title()

        # Extract github link
        gh_match = re.search(r'href="(https://github\.com/marimo-team/gallery-examples/blob/[^"]+\.py)"', html)
        github_url = gh_match.group(1) if gh_match else None

        raw_url = None
        if github_url:
            raw_url = github_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

        # Extract description meta
        desc_match = re.search(r'<meta name="description" content="([^"]+)"', html)
        desc = desc_match.group(1) if desc_match else "Community curated notebook template on MoLab."

        return {
            "slug": clean_slug,
            "title": raw_title,
            "description": desc,
            "gallery_url": url,
            "github_url": github_url,
            "raw_download_url": raw_url,
        }

    def download_template(self, slug: str, output_path: str) -> str:
        """Download raw Python code for a gallery template and save to file."""
        info = self.get_template_info(slug)
        raw_url = info.get("raw_download_url")
        if not raw_url:
            raise GalleryError(f"No direct download URL found for template '{slug}'")

        try:
            req = urllib.request.Request(raw_url, headers={"User-Agent": "MoLab-CLI/2.3"})
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                code = resp.read().decode("utf-8")
        except Exception as e:
            raise GalleryError(f"Failed to download template code from {raw_url}: {e}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(code)

        return output_path
