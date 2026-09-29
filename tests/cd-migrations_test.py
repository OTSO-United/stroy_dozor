import asyncio
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "cd" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = load("cd_db", "migration-db.py")
runtime = load("cd_runtime", "runtime-bootstrap.py")
policy = json.loads((ROOT / "scripts/cd/migration-policy.json").read_text())


class MigrationTests(unittest.TestCase):
    def test_checks_cannot_be_disabled_by_python_optimization(self):
        for file in ("runtime-bootstrap.py", "migration-db.py"):
            tree = ast.parse((ROOT / "scripts/cd" / file).read_text())
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        with self.assertRaises(ValueError):
            db.require(False)
        with self.assertRaises(RuntimeError):
            runtime.require(False)

    def tree(self, root):
        previous, current = Path(root) / "old", Path(root) / "new"
        for directory in (previous, current):
            (directory / "versions").mkdir(parents=True)
        for file in (ROOT / "backend/migrations").rglob("*.py"):
            relative = file.relative_to(ROOT / "backend/migrations")
            (current / relative).parent.mkdir(parents=True, exist_ok=True)
            (current / relative).write_text(file.read_text(), encoding="utf-8")
            if relative.name != "004_detector_lab.py":
                (previous / relative).parent.mkdir(parents=True, exist_ok=True)
                (previous / relative).write_text(file.read_text(), encoding="utf-8")
        return previous, current

    def test_reviewed_tree(self):
        with tempfile.TemporaryDirectory() as root:
            previous, current = self.tree(root)
            self.assertTrue(db.validate_tree(previous, current, policy["migration004Sha256"])["immutableAppliedFiles"])

    def test_changed_old_migration(self):
        with tempfile.TemporaryDirectory() as root:
            previous, current = self.tree(root)
            path = next((current / "versions").glob("003*.py"))
            path.write_text(path.read_text() + "\n# changed\n")
            with self.assertRaisesRegex(ValueError, "Applied"):
                db.validate_tree(previous, current, policy["migration004Sha256"])

    def test_unreviewed_target_and_extra_migration(self):
        with tempfile.TemporaryDirectory() as root:
            previous, current = self.tree(root)
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                db.validate_tree(previous, current, "0" * 64)
            (current / "versions/005.py").write_text('revision="005"\ndown_revision="004"')
            with self.assertRaises(ValueError):
                db.validate_tree(previous, current, policy["migration004Sha256"])

    def test_data_preservation(self):
        before = {"revision": "003", "tables": {"projects": "digest"}, "sequences": []}
        db.assert_preserved(before, {**before, "revision": "004"})
        with self.assertRaises(ValueError):
            db.assert_preserved(before, {**before, "tables": {}})

    def test_smoke_requires_isolation(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            db.smoke()

    def call(self, client="127.0.0.1", method="GET", token=b"secret", state=None):
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        with patch.object(runtime, "marker", return_value=state):
            asyncio.run(runtime.Maintenance(app)({"type": "http", "client": (client, 1), "method": method,
                                                 "headers": [(b"x-stroy-cd-probe", token)]}, receive, send))
        return sent[0]["status"]

    def test_maintenance_blocks_external_and_local_writes(self):
        for client in ["127.0.0.1", "172.18.0.2"]:
            self.assertEqual(self.call(client=client, method="POST", state={"token": "secret"}), 503)
        self.assertEqual(self.call(client="172.18.0.2", state={"token": "secret"}), 503)
        self.assertEqual(self.call(token=b"wrong", state={"token": "secret"}), 503)

    def test_internal_read_probe_and_open_traffic(self):
        self.assertEqual(self.call(state={"token": "secret"}), 200)
        self.assertEqual(self.call(method="POST", state=None), 200)

    def test_invalid_marker_fails_closed(self):
        self.assertEqual(self.call(token=b"", state={"token": ""}), 503)


if __name__ == "__main__":
    unittest.main()
