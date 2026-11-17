import os
import subprocess
import sys
import tempfile
from pathlib import Path

AOS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AOS / "scripts"))
sys.path.insert(0, str(AOS))


def make_repo(files: dict[str, str], name: str = "proj", remote: str | None = None) -> Path:
    d = Path(tempfile.mkdtemp()) / name
    d.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=d, check=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True, env=env)
    return d


SAMPLE = {
    "src/auth/token_refresh.py": "def refresh_token(session):\n    return session\n\n" + "\n".join(f"def filler_{i}():\n    return {i}\n" for i in range(60)) + "\ndef revoke_token(t):\n    return None\n",
    "src/billing/invoice.py": "class InvoiceBuilder:\n    def build(self):\n        return 1\n",
    "tests/test_token_refresh.py": "def test_refresh():\n    assert True\n",
    "package.json": "{}\n",
    "README.md": "# demo\n",
}
