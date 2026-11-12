from unittest import TestCase
from unittest.mock import patch

from ponsbot.diagnostics import build_version


class DiagnosticsTests(TestCase):
    def test_build_version_reports_deployed_commit_and_source_hash(self):
        with patch.dict('os.environ', {'RAILWAY_GIT_COMMIT_SHA': 'a' * 40}):
            result = build_version()
        self.assertRegex(result, r'Code: [0-9a-f]{12}')
        self.assertIn('Railway commit: aaaaaaaa', result)

    def test_arbitrary_env_content_is_not_echoed_to_users(self):
        with patch.dict('os.environ', {'RAILWAY_GIT_COMMIT_SHA': 'private-token-not-a-commit'}):
            result = build_version()
        self.assertNotIn('private-token', result)
