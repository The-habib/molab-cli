"""
MoLab In-Notebook Vault Subsystem.
Enables 100% on-MoLab persistent storage without local phone storage or external cloud accounts.
Encodes workspace files into self-extracting Marimo notebook cells stored directly on MoLab.
"""

import base64
import json
import os
import shlex
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from molab_cli.exceptions import MoLabError
from molab_cli.sandbox import SandboxSession


class VaultError(MoLabError):
    """Raised when in-notebook vault operations fail."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="VAULT_ERROR", details=details)


class MoLabVault:
    """Manages 100% on-MoLab workspace persistence stored inside notebook metadata."""

    def __init__(self, target: Union[str, SandboxSession]):
        if isinstance(target, SandboxSession):
            self.session = target
            self.notebook_id = target.notebook_id
        else:
            self.notebook_id = target if target.startswith("nb_") else f"nb_{target}"
            self.session = SandboxSession(self.notebook_id)

    def pack_workspace(
        self,
        source_dir: str = "/workspace",
        max_size_mb: float = 25.0,
    ) -> Dict[str, Any]:
        """
        Compress pod workspace files directly into a self-extracting cell inside /marimo/notebook.py.
        100% on MoLab servers with zero local phone storage and zero third-party cloud.
        """
        self.session.resolve()

        source_dir_json = json.dumps(source_dir)
        max_bytes = int(max_size_mb * 1024 * 1024)

        # 1. Archive workspace into base64 payload on the pod
        pack_script = f"""
import tarfile, io, base64, os, sys, uuid, warnings
warnings.filterwarnings('ignore')

source_dir = {source_dir_json}
if not os.path.exists(source_dir):
    print("NO_SOURCE")
    sys.exit(0)

buf = io.BytesIO()
count = 0
total_uncompressed = 0
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for root, dirs, files in os.walk(source_dir):
        # Exclude common large caches
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".cache")]
        for file in files:
            if file.endswith((".pyc", ".tar.gz", ".tmp")):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, source_dir)
            try:
                sz = os.path.getsize(full_path)
                total_uncompressed += sz
                tar.add(full_path, arcname=rel_path)
                count += 1
            except Exception:
                pass

compressed_bytes = buf.getvalue()
compressed_size = len(compressed_bytes)

if compressed_size > {max_bytes}:
    print(f"TOO_LARGE:{{compressed_size}}")
    sys.exit(0)

b64_str = base64.b64encode(compressed_bytes).decode("ascii")

# Check /marimo/notebook.py
nb_path = "/marimo/notebook.py"
if not os.path.exists(nb_path):
    print("NO_NOTEBOOK")
    sys.exit(0)

with open(nb_path, "r", encoding="utf-8") as f:
    content = f.read()

marker_start = "# === MOLAB_VAULT_START ==="
marker_end = "# === MOLAB_VAULT_END ==="

vault_cell = f'''
{{marker_start}}
@app.cell
def _():
    # MoLab Permanent In-Notebook Vault (Zero External Storage)
    import base64, io, os, tarfile, warnings
    warnings.filterwarnings("ignore")
    dest = os.path.realpath({source_dir_json})
    os.makedirs(dest, exist_ok=True)
    b64_data = \"\"\"{{b64_str}}\"\"\"
    try:
        raw = base64.b64decode(b64_data)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
            for member in t.getmembers():
                target_p = os.path.realpath(os.path.abspath(os.path.join(dest, member.name)))
                if not target_p.startswith(dest + os.sep) and target_p != dest:
                    continue
                if member.issym() or member.islnk():
                    link_p = os.path.realpath(os.path.abspath(os.path.join(os.path.dirname(target_p), member.linkname)))
                    if not link_p.startswith(dest + os.sep) and link_p != dest:
                        continue
                t.extract(member, dest)
    except Exception:
        pass
    return
{{marker_end}}
'''

if marker_start in content and marker_end in content:
    s_idx = content.find(marker_start)
    e_idx = content.find(marker_end) + len(marker_end)
    new_content = content[:s_idx].rstrip() + "\\n" + vault_cell.strip() + "\\n" + content[e_idx:].lstrip()
else:
    # Append before if __name__ == '__main__': if present, or at the end
    if "if __name__ == \\"__main__\\":" in content:
        parts = content.split("if __name__ == \\"__main__\\":")
        new_content = parts[0].rstrip() + "\\n" + vault_cell.strip() + "\\n\\nif __name__ == \\"__main__\\":" + parts[1]
    else:
        new_content = content.rstrip() + "\\n" + vault_cell.strip() + "\\n"

tmp_nb_path = f"/marimo/notebook.py.tmp_{{uuid.uuid4().hex}}"
with open(tmp_nb_path, "w", encoding="utf-8") as f:
    f.write(new_content)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp_nb_path, nb_path)

print(f"SUCCESS:{{count}}:{{compressed_size}}:{{total_uncompressed}}")
"""
        cmd = f"/tmp/uv-venv/bin/python -c {shlex.quote(pack_script)}"
        raw_out = self.session.execute_command(cmd, timeout=60.0).strip()

        # Find the line starting with SUCCESS or error
        success_line = None
        for line in raw_out.splitlines():
            line_s = line.strip()
            if line_s.startswith("SUCCESS:"):
                success_line = line_s
                break
            elif line_s.startswith("TOO_LARGE:"):
                raise VaultError(f"Workspace exceeds in-notebook vault limit of {max_size_mb}MB: {line_s}")
            elif line_s == "NO_SOURCE":
                raise VaultError(f"Source directory {source_dir} does not exist.")
            elif line_s == "NO_NOTEBOOK":
                raise VaultError("No /marimo/notebook.py found on pod.")

        if not success_line:
            raise VaultError(f"Failed to pack workspace into notebook vault: {raw_out}")

        parts = success_line.split(":")
        files_count = int(parts[1])
        comp_size = int(parts[2])
        uncomp_size = int(parts[3])

        return {
            "notebook_id": self.notebook_id,
            "status": "packed_in_notebook",
            "files_packed": files_count,
            "compressed_bytes": comp_size,
            "uncompressed_bytes": uncomp_size,
            "storage_location": "MoLab Cloud Database (100% on MoLab)",
        }

    def unpack_workspace(
        self,
        target_dir: str = "/workspace",
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        """
        Extract files from the in-notebook vault directly into /workspace on the pod.
        Safely validates archive members against path traversal and symlink escapes.
        Provides conflict detection and non-destructive restore option.
        """
        self.session.resolve()
        target_dir_json = json.dumps(target_dir)
        overwrite_val = "True" if overwrite else "False"
        unpack_script = f"""
import base64, io, os, tarfile, sys, warnings, json
warnings.filterwarnings('ignore')

nb_path = "/marimo/notebook.py"
if not os.path.exists(nb_path):
    print("NO_NOTEBOOK")
    sys.exit(0)

with open(nb_path, "r", encoding="utf-8") as f:
    content = f.read()

marker_start = "# === MOLAB_VAULT_START ==="
marker_end = "# === MOLAB_VAULT_END ==="

if marker_start not in content or marker_end not in content:
    print("NO_VAULT_FOUND")
    sys.exit(0)

s_idx = content.find('b64_data = \"\"\"') + len('b64_data = \"\"\"')
e_idx = content.find('\"\"\"', s_idx)
b64_data = content[s_idx:e_idx].strip()

try:
    raw = base64.b64decode(b64_data)
except Exception as e:
    print(f"CORRUPT_VAULT:{{e}}")
    sys.exit(0)

dest = os.path.realpath({target_dir_json})
os.makedirs(dest, exist_ok=True)
overwrite = {overwrite_val}

count = 0
conflicts = []
skipped = 0

with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
    for member in t.getmembers():
        target_p = os.path.realpath(os.path.abspath(os.path.join(dest, member.name)))
        if not target_p.startswith(dest + os.sep) and target_p != dest:
            # Traversal attempt blocked
            continue
        if member.issym() or member.islnk():
            link_p = os.path.realpath(os.path.abspath(os.path.join(os.path.dirname(target_p), member.linkname)))
            if not link_p.startswith(dest + os.sep) and link_p != dest:
                # Symlink escape blocked
                continue
        if os.path.exists(target_p) and not member.isdir():
            if not overwrite:
                conflicts.append(member.name)
                skipped += 1
                continue
        t.extract(member, dest)
        count += 1

res = {{
    "status": "SUCCESS",
    "files_unpacked": count,
    "archive_bytes": len(raw),
    "conflicts": conflicts[:50],
    "conflicts_count": len(conflicts),
    "files_skipped": skipped
}}
print("RESULT_JSON:" + json.dumps(res))
"""
        cmd = f"/tmp/uv-venv/bin/python -c {shlex.quote(unpack_script)}"
        raw_out = self.session.execute_command(cmd, timeout=45.0).strip()

        for line in raw_out.splitlines():
            line_s = line.strip()
            if line_s.startswith("RESULT_JSON:"):
                payload = json.loads(line_s[len("RESULT_JSON:"):])
                return {
                    "notebook_id": self.notebook_id,
                    "status": "unpacked",
                    "files_unpacked": payload.get("files_unpacked", 0),
                    "archive_bytes": payload.get("archive_bytes", 0),
                    "target_dir": target_dir,
                    "conflicts": payload.get("conflicts", []),
                    "conflicts_count": payload.get("conflicts_count", 0),
                    "files_skipped": payload.get("files_skipped", 0),
                }
            elif line_s.startswith("SUCCESS:"):
                parts = line_s.split(":")
                files_unpacked = int(parts[1])
                archive_size = int(parts[2])
                return {
                    "notebook_id": self.notebook_id,
                    "status": "unpacked",
                    "files_unpacked": files_unpacked,
                    "archive_bytes": archive_size,
                    "target_dir": target_dir,
                    "conflicts": [],
                    "conflicts_count": 0,
                    "files_skipped": 0,
                }
            elif line_s == "NO_VAULT_FOUND":
                raise VaultError("No vault found inside this notebook.")
            elif line_s == "NO_NOTEBOOK":
                raise VaultError("No /marimo/notebook.py found on pod.")
            elif line_s.startswith("CORRUPT_VAULT:"):
                raise VaultError(f"Vault payload in notebook is corrupted: {line_s}")

        raise VaultError(f"Failed to unpack vault: {raw_out}")

    def inspect_vault(self) -> Dict[str, Any]:
        """
        Check if the notebook currently contains a permanent in-notebook vault.
        """
        self.session.resolve()
        inspect_script = """
import os, sys, warnings
warnings.filterwarnings('ignore')

nb_path = '/marimo/notebook.py'
if not os.path.exists(nb_path):
    print('NONE')
    sys.exit(0)

with open(nb_path, 'r', encoding='utf-8') as f:
    c = f.read()

if '# === MOLAB_VAULT_START ===' in c:
    s_idx = c.find('b64_data =')
    if s_idx != -1:
        s_quote = c.find('\"\"\"', s_idx)
        e_quote = c.find('\"\"\"', s_quote + 3) if s_quote != -1 else -1
        size = (e_quote - s_quote - 3) if e_quote != -1 and s_quote != -1 else 0
        print(f'EXISTS:{size}')
    else:
        print('EXISTS:0')
else:
    print('NONE')
"""
        cmd = f"python3 -c {shlex.quote(inspect_script)}"
        raw_out = self.session.execute_command(cmd, timeout=15.0).strip()

        for line in raw_out.splitlines():
            line_s = line.strip()
            if line_s.startswith("EXISTS:"):
                size_b64 = int(line_s.split(":")[1])
                return {
                    "has_vault": True,
                    "exists": True,
                    "approx_size_bytes": int(size_b64 * 3 / 4),
                    "storage_location": "MoLab Cloud Database (100% on MoLab)",
                }
            elif line_s == "NONE":
                return {"has_vault": False, "exists": False}

        return {"has_vault": False, "exists": False}
