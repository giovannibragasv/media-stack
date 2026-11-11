import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class EnvValueTest(unittest.TestCase):
    """scripts/lib/env.sh must resolve values the way docker compose interpolates them."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        shutil.copytree(REPO / "scripts", self.root / "scripts")
        for name in ("docker-compose.yml", ".env.example"):
            shutil.copy(REPO / name, self.root / name)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CONFIG_ROOT", "DATA_ROOT")}

    def run_bash(self, script, env=None):
        result = subprocess.run(
            ["bash", "-c", f'REPO_ROOT="$1"; source scripts/lib/env.sh; {script}', "_", self.root],
            cwd=self.root,
            env=env or self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def env_value(self, name, default="", env=None):
        return self.run_bash(f'env_value {name} "{default}"', env).rstrip("\n")

    def compose_source(self, service, target, env=None):
        config = json.loads(self.run_bash("compose config --format json", env))
        volumes = config["services"][service]["volumes"]
        return next(v["source"] for v in volumes if v["target"] == target)

    def test_matches_compose_on_quoted_hash_and_unquoted_hash(self):
        (self.root / ".env").write_text('CONFIG_ROOT="/Volumes/My Drive/cfg # x"\nDATA_ROOT=/a#b\n')
        self.assertEqual(self.env_value("CONFIG_ROOT"), "/Volumes/My Drive/cfg # x")
        self.assertEqual(self.env_value("DATA_ROOT"), "/a#b")
        self.assertEqual(
            self.compose_source("radarr", "/config"), "/Volumes/My Drive/cfg # x/radarr"
        )
        self.assertEqual(self.compose_source("radarr", "/data"), "/a#b")

    def test_strips_inline_comment_like_compose(self):
        (self.root / ".env").write_text("CONFIG_ROOT=./cfg      # per-service app state\n")
        self.assertEqual(self.env_value("CONFIG_ROOT"), "./cfg")

    def test_shell_env_wins_over_env_file(self):
        (self.root / ".env").write_text("DATA_ROOT=/from-file\n")
        self.assertEqual(
            self.env_value("DATA_ROOT", env={**self.env, "DATA_ROOT": "/shell"}), "/shell"
        )

    def test_default_when_unset_or_empty(self):
        (self.root / ".env").write_text("DATA_ROOT=\n")
        self.assertEqual(self.env_value("DATA_ROOT", "./data"), "./data")
        self.assertEqual(self.env_value("CONFIG_ROOT", "./config"), "./config")

    def test_bootstrap_twice_creates_only_the_layout(self):
        for _ in range(2):
            subprocess.run(
                ["bash", "scripts/bootstrap.sh"],
                cwd=self.root,
                env=self.env,
                capture_output=True,
                check=True,
            )
        made = sorted(
            str(p.relative_to(self.root))
            for top in ("config", "data")
            for p in [self.root / top, *(self.root / top).rglob("*")]
        )
        self.assertEqual(
            made,
            [
                "config",
                "data",
                "data/downloads",
                "data/downloads/incomplete",
                "data/movies",
                "data/music",
                "data/tv",
            ],
        )
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()),
            [".env", ".env.example", "config", "data", "docker-compose.yml", "scripts"],
        )


if __name__ == "__main__":
    unittest.main()
