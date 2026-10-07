"""
High-level MoLab Web and Server Actions API Client.
"""

import gzip
import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from molab_cli.auth import get_cookie_header
from molab_cli.config import DEFAULT_USER_AGENT

MOLAB_BASE = "https://molab.marimo.io"

# Fallback known action IDs in case dynamic resolution fails
FALLBACK_ACTIONS = {
    "createNotebook": "7fec248cb86d8779a75078736a73768f147ee6595f",
    "duplicateNotebook": "40d513a53713263c7e6831a455182319cb97b280d7",
    "deleteNotebook": "401f2d3ad08b96b4eb774c8555a74430b316e09833",
    "shutdownNotebook": "6060c18435b4cf0d53a27bc340bec940326da3016b",
    "editTitle": "60050e636a864b289f1328e29bea3a4e66250a498f",
    "setGalleryVisibility": "7f088d66bde03b8ce73ed706e5426919f511b7c084",
}


class MoLabClient:
    """Client for molab.marimo.io web APIs, server actions, and cloud sandboxes."""

    def __init__(self):
        self._action_cache: Dict[str, str] = {}

    def fetch_url(
        self,
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Tuple[int, str, Dict[str, str]]:
        """Perform HTTP request with gzip decompression and auth cookies."""
        req_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Cookie": get_cookie_header(),
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                raw = resp.read()
                if resp_headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(raw).decode("utf-8", errors="ignore")
                else:
                    body = raw.decode("utf-8", errors="ignore")
                return status, body, resp_headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(raw).decode("utf-8", errors="ignore")
            else:
                body = raw.decode("utf-8", errors="ignore")
            return e.code, body, dict(e.headers)

    def resolve_action_id(self, action_name: str) -> str:
        """
        Dynamically discover the active Next.js Server Action ID for a given function name.
        """
        if action_name in self._action_cache:
            return self._action_cache[action_name]

        # Scan /new chunk scripts
        try:
            _, html, _ = self.fetch_url(f"{MOLAB_BASE}/new", timeout=10)
            chunks = list(set(re.findall(r"/_next/static/immutable/chunks/[a-zA-Z0-9_.-]+\.js", html)))
            for chunk_path in chunks:
                try:
                    _, js_content, _ = self.fetch_url(f"{MOLAB_BASE}{chunk_path}", timeout=5)
                    if action_name in js_content:
                        # Pattern: createServerReference("<hash>", ..., "<action_name>")
                        m = re.findall(
                            r'createServerReference\)?\([\"\']([a-f0-9]+)[\"\'][^)]*[\"\']'
                            + re.escape(action_name)
                            + r'[\"\']\)',
                            js_content,
                        )
                        if m:
                            self._action_cache[action_name] = m[0]
                            return m[0]
                except Exception:
                    continue
        except Exception:
            pass

        return FALLBACK_ACTIONS.get(action_name, "")

    def list_running_sandboxes(self) -> Dict[str, str]:
        """
        List all actively running notebooks mapped to their sandbox pod ID.
        """
        status, rsc, _ = self.fetch_url(f"{MOLAB_BASE}/notebooks", headers={"RSC": "1"})
        if status != 200:
            return {}
        running = {}
        for l in rsc.split("\n"):
            if "sandboxId" in l:
                m = re.search(r'"data":(\[\{.*?\}\])', l)
                if m:
                    try:
                        arr = json.loads(m.group(1))
                        for item in arr:
                            nb_id = item.get("id")
                            sb_id = item.get("sandboxId")
                            if nb_id and sb_id:
                                running[nb_id] = sb_id
                    except Exception:
                        pass
        return running

    def list_notebooks(self) -> List[Dict[str, Any]]:
        """
        List all cloud notebooks owned by user organization with running state and hardware specs.
        """
        status, rsc, _ = self.fetch_url(f"{MOLAB_BASE}/notebooks", headers={"RSC": "1"})
        if status != 200:
            raise RuntimeError(f"Failed to fetch notebooks page (HTTP {status})")

        running_map = self.list_running_sandboxes()
        notebooks = []

        m = re.search(r'"notebooks":(\[\{.*?\}\])', rsc)
        if m:
            try:
                items = json.loads(m.group(1))
                for item in items:
                    nb_id = item.get("id")
                    if not nb_id:
                        continue
                    notebooks.append({
                        "id": nb_id,
                        "title": item.get("title") or "Untitled Notebook",
                        "gpu": item.get("gpu") or "",
                        "cpu": item.get("cpu", 4),
                        "memory": item.get("memory", 32),
                        "gpu_count": item.get("gpuCount", 0),
                        "running": nb_id in running_map,
                        "sandbox_id": running_map.get(nb_id),
                        "url": f"{MOLAB_BASE}/notebooks/{nb_id}",
                    })
                return notebooks
            except Exception:
                pass

        # Fallback to HTML parsing if RSC structure varies
        status, html, _ = self.fetch_url(f"{MOLAB_BASE}/notebooks")
        seen = set()
        all_ids = re.findall(r"nb_[a-zA-Z0-9]+", html)
        for nb_id in all_ids:
            if nb_id in seen:
                continue
            seen.add(nb_id)
            idx = html.find(nb_id)
            snippet = html[idx : idx + 350]
            title_match = re.search(r'title[^\w]+([a-zA-Z0-9_\s-]+)', snippet)
            title = title_match.group(1).strip() if title_match else "Untitled Notebook"
            gpu = "rtxp6000" if "rtxp6000" in snippet else ""
            notebooks.append({
                "id": nb_id,
                "title": title,
                "gpu": gpu,
                "cpu": 4,
                "memory": 32,
                "gpu_count": 1 if gpu else 0,
                "running": nb_id in running_map,
                "sandbox_id": running_map.get(nb_id),
                "url": f"{MOLAB_BASE}/notebooks/{nb_id}",
            })
        return notebooks

    def create_notebook(
        self,
        code: str = "",
        gpu: str = "",
        cpu: int = 4,
        memory: int = 32,
        gpu_count: int = 0,
    ) -> str:
        """
        Create a new cloud notebook on MoLab.
        Supports Blackwell GPU ('rtxp6000' or 'blackwell').
        """
        action_id = FALLBACK_ACTIONS.get("createNotebook") or self.resolve_action_id("createNotebook")

        if not code.strip():
            code = "import marimo as mo\n"

        # Normalize GPU
        if gpu.lower() in ("blackwell", "rtx", "rtxp6000", "server"):
            gpu = "rtxp6000"
            gpu_count = max(1, gpu_count)

        payload = {
            "entryPoint": "molab_new",
            "code": code,
            "cpu": cpu,
            "memory": memory,
        }
        if gpu:
            payload["gpu"] = gpu
            payload["gpuCount"] = gpu_count

        body = json.dumps([payload]).encode("utf-8")
        status, resp_body, _ = self.fetch_url(
            f"{MOLAB_BASE}/new",
            method="POST",
            data=body,
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )

        m = re.search(r'"notebookId":"(nb_[a-zA-Z0-9]+)"', resp_body)
        if m:
            return m.group(1)

        # Check for error
        err_m = re.search(r'"error":"([^"]+)"', resp_body)
        if err_m:
            raise RuntimeError(f"Create Notebook Failed: {err_m.group(1)}")

        raise RuntimeError(f"Unexpected response when creating notebook: {resp_body[:300]}")

    def get_notebook_rsc(self, nb_id: str) -> str:
        """Fetch RSC stream for notebook page."""
        if not nb_id.startswith("nb_"):
            nb_id = f"nb_{nb_id}"

        status, rsc, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks/{nb_id}",
            headers={"RSC": "1"},
        )
        if status != 200:
            raise RuntimeError(f"Failed to fetch notebook {nb_id} (HTTP {status})")
        return rsc

    def inspect_notebook(self, nb_id: str) -> Dict[str, Any]:
        """
        Inspect notebook details, compute specifications, and sandbox session URL.
        """
        rsc = self.get_notebook_rsc(nb_id)

        title = "Untitled"
        cpu = 4
        memory = 32
        gpu = ""
        gpu_count = 0

        # Exact notebook object: \d+:{"id":"nb_...","title":...}
        m_nb = re.search(rf'\d+:(\{{\"id\":\"{nb_id}\".*?\}})\n', rsc)
        if m_nb:
            try:
                nb_obj = json.loads(m_nb.group(1))
                title = nb_obj.get("title") or title
                cpu = nb_obj.get("cpu", 4)
                memory = nb_obj.get("memory", 32)
                gpu = nb_obj.get("gpu") or ""
                gpu_count = nb_obj.get("gpuCount", 0)
            except Exception:
                pass
        else:
            title_m = re.search(r'"title":"([^"]+)"', rsc)
            title = title_m.group(1) if title_m else title

        # Sandbox session URL
        sb_m = re.search(
            r'https://(sb-[a-zA-Z0-9]+)-session\.sb\.molab\.run/session/([^"\'\s]+)',
            rsc,
        )
        sandbox_id = sb_m.group(1) if sb_m else None
        session_b64 = sb_m.group(2) if sb_m else None

        return {
            "id": nb_id,
            "title": title,
            "cpu": cpu,
            "memory": memory,
            "gpu": gpu,
            "gpu_count": gpu_count,
            "sandbox_id": sandbox_id,
            "session_token_b64": session_b64,
            "url": f"{MOLAB_BASE}/notebooks/{nb_id}",
        }

    def rename_notebook(self, nb_id: str, new_title: str) -> None:
        """
        Rename notebook via onTitleChange Server Action.
        """
        rsc = self.get_notebook_rsc(nb_id)

        # Find onTitleChange:"$hXX"
        ref_match = re.search(r'"onTitleChange":"\$h([a-z0-9]+)"', rsc)
        if not ref_match:
            raise RuntimeError(f"Could not locate onTitleChange action reference in {nb_id}")

        ref_id = ref_match.group(1)
        action_match = re.search(rf"\n{ref_id}:\{{\"id\":\"([a-f0-9]+)\",\"bound\":\"\$@([a-z0-9]+)\"\}}", rsc)
        if not action_match:
            raise RuntimeError(f"Could not locate bound action for onTitleChange {ref_id}")

        action_id = action_match.group(1)
        bound_var = action_match.group(2)

        arr_match = re.search(rf"\n{bound_var}:\[\"\$@([a-z0-9]+)\"\]", rsc)
        if not arr_match:
            raise RuntimeError("Could not resolve bound argument variable")

        val_var = arr_match.group(1)
        val_match = re.search(rf"\n{val_var}:\"([^\"]+)\"", rsc)
        if not val_match:
            raise RuntimeError("Could not resolve bound argument value")

        bound_arg = val_match.group(1)
        payload = [bound_arg, new_title]

        status, body, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks/{nb_id}",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )
        if status != 200:
            raise RuntimeError(f"Rename failed with HTTP {status}: {body[:200]}")

    def update_compute(
        self,
        nb_id: str,
        gpu: str = "rtxp6000",
        cpu: int = 4,
        memory: int = 32,
        gpu_count: int = 1,
    ) -> Dict[str, Any]:
        """
        Configure compute resources (e.g. NVIDIA Blackwell RTX Pro 6000) and restart notebook pod.
        """
        rsc = self.get_notebook_rsc(nb_id)

        # Find onSaveAndRestart:"$hXX"
        ref_match = re.search(r'"onSaveAndRestart":"\$h([a-z0-9]+)"', rsc)
        if not ref_match:
            raise RuntimeError(f"Could not locate onSaveAndRestart action in {nb_id}")

        ref_id = ref_match.group(1)
        action_match = re.search(rf"\n{ref_id}:\{{\"id\":\"([a-f0-9]+)\",\"bound\":\"\$@([a-z0-9]+)\"\}}", rsc)
        if not action_match:
            raise RuntimeError(f"Could not locate bound action for onSaveAndRestart {ref_id}")

        action_id = action_match.group(1)
        bound_var = action_match.group(2)

        arr_match = re.search(rf"\n{bound_var}:\[\"\$@([a-z0-9]+)\"\]", rsc)
        if not arr_match:
            raise RuntimeError("Could not resolve bound argument array")

        val_var = arr_match.group(1)
        val_match = re.search(rf"\n{val_var}:\"([^\"]+)\"", rsc)
        if not val_match:
            raise RuntimeError("Could not resolve bound argument string")

        bound_arg = val_match.group(1)

        # Normalize GPU
        if gpu.lower() in ("blackwell", "rtx", "rtxp6000", "server"):
            gpu = "rtxp6000"
            gpu_count = max(1, gpu_count)
        elif gpu.lower() in ("none", "", "cpu"):
            gpu = ""
            gpu_count = 0

        payload = [
            bound_arg,
            {
                "cpu": cpu,
                "memory": memory,
                "gpu": gpu,
                "gpuCount": gpu_count,
            },
        ]

        status, body, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks/{nb_id}",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )
        if status != 200:
            raise RuntimeError(f"Compute update failed with HTTP {status}: {body[:200]}")

        # Parse new sandbox URL from response
        sb_m = re.search(r"https://(sb-[a-zA-Z0-9]+)-session\.sb\.molab\.run", body)
        return {
            "success": True,
            "new_sandbox_id": sb_m.group(1) if sb_m else None,
            "gpu": gpu,
            "cpu": cpu,
            "memory": memory,
            "gpu_count": gpu_count,
        }

    def duplicate_notebook(self, nb_id: str) -> str:
        """
        Duplicate an existing notebook.
        """
        action_id = FALLBACK_ACTIONS.get("duplicateNotebook", "40d513a53713263c7e6831a455182319cb97b280d7")
        payload = [nb_id]
        status, body, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )
        if status != 200:
            raise RuntimeError(f"Duplicate failed with HTTP {status}: {body[:200]}")

        m = re.search(r'"notebooks":\[\{"id":"(nb_[a-zA-Z0-9]+)"', body)
        if m:
            return m.group(1)
        for cand in re.findall(r'nb_[a-zA-Z0-9]+', body):
            if cand != nb_id:
                return cand
        return nb_id

    def stop_notebook(self, nb_id: str, sandbox_id: Optional[str] = None) -> None:
        """
        Stop/Shutdown a running notebook sandbox pod.
        """
        if not sandbox_id:
            running = self.list_running_sandboxes()
            sandbox_id = running.get(nb_id)
            if not sandbox_id:
                try:
                    info = self.inspect_notebook(nb_id)
                    sandbox_id = info.get("sandbox_id")
                except Exception:
                    pass

        if not sandbox_id:
            raise RuntimeError(f"Notebook {nb_id} does not appear to have an active running sandbox.")

        action_id = FALLBACK_ACTIONS.get("shutdownNotebook", "6060c18435b4cf0d53a27bc340bec940326da3016b")
        payload = [nb_id, sandbox_id]
        status, body, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )
        if status != 200:
            raise RuntimeError(f"Shutdown failed (HTTP {status}): {body[:200]}")

    def delete_notebook(self, nb_id: str) -> None:
        """
        Permanently delete a notebook.
        """
        action_id = FALLBACK_ACTIONS.get("deleteNotebook", "401f2d3ad08b96b4eb774c8555a74430b316e09833")
        payload = [nb_id]
        status, body, _ = self.fetch_url(
            f"{MOLAB_BASE}/notebooks",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Next-Action": action_id,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "text/x-component",
            },
        )
        if status != 200:
            raise RuntimeError(f"Delete failed (HTTP {status}): {body[:200]}")
