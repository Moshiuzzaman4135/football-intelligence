import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]


@unittest.skipUnless(shutil.which("docker"), "Docker CLI is required")
class ComposeBootstrapContractTests(unittest.TestCase):
    def test_empty_postgres_is_migrated_before_backend_starts(self):
        environment = {
            **os.environ,
            "FOOTBALL_DATABASE_URL": "postgresql+psycopg://football_app:ephemeral@postgres/football",
            "FOOTBALL_POSTGRES_PASSWORD": "ephemeral-database-password",
            "FOOTBALL_MINIO_ROOT_USER": "ephemeral-root",
            "FOOTBALL_MINIO_ROOT_PASSWORD": "ephemeral-root-password",
            "FOOTBALL_S3_ACCESS_KEY": "ephemeral-app",
            "FOOTBALL_S3_SECRET_KEY": "ephemeral-app-password",
        }
        rendered = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        config = json.loads(rendered.stdout)
        services = config["services"]

        self.assertIn("postgres", services)
        self.assertIn("healthcheck", services["postgres"])
        self.assertTrue(
            any(
                mount["target"] == "/var/lib/postgresql/data"
                and mount["type"] == "volume"
                for mount in services["postgres"]["volumes"]
            )
        )
        self.assertEqual(services["migrate"]["command"], ["alembic", "upgrade", "head"])
        self.assertEqual(
            services["migrate"]["depends_on"]["postgres"]["condition"],
            "service_healthy",
        )
        self.assertEqual(
            services["backend"]["depends_on"]["migrate"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(
            services["backend"]["environment"]["FOOTBALL_DATABASE_URL"],
            services["migrate"]["environment"]["FOOTBALL_DATABASE_URL"],
        )


if __name__ == "__main__":
    unittest.main()
