import hashlib
import os
import re
from pathlib import Path


def build_version():
    # Fingerprint the actual image source, even if Railway's commit variable is absent.
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    revision = os.environ.get('RAILWAY_GIT_COMMIT_SHA', '')
    commit = revision[:8] if re.fullmatch(r'[a-fA-F0-9]{7,64}', revision) else 'không có'
    return f"Code: {digest.hexdigest()[:12]} · Railway commit: {commit}"
