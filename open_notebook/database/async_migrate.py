"""
Async migration system for SurrealDB using the official Python client.
Based on patterns from sblpy migration system.
"""

from pathlib import Path
from typing import List

from loguru import logger

from .repository import db_connection, repo_query


class AsyncMigration:
    """
    Handles individual migration operations with async support.
    """

    def __init__(self, sql: str) -> None:
        """Initialize migration with SQL content."""
        self.sql = sql

    @classmethod
    def from_file(cls, file_path: str) -> "AsyncMigration":
        """Create migration from SQL file."""
        # ``utf-8-sig`` accepts both normal UTF-8 and legacy migration files
        # saved by Windows editors with a BOM. SurrealDB does not accept the
        # BOM token at the beginning of a query.
        with open(file_path, "r", encoding="utf-8-sig") as file:
            raw_content = file.read()
            # Clean up SQL content
            lines = []
            for line in raw_content.split("\n"):
                line = line.strip()
                if line and not line.startswith("--"):
                    lines.append(line)
            sql = " ".join(lines)
            return cls(sql)

    async def run(self, bump: bool = True) -> None:
        """Run the migration."""
        try:
            async with db_connection() as connection:
                await connection.query(self.sql)

            if bump:
                await bump_version()
            else:
                await lower_version()

        except Exception as e:
            logger.error(f"Migration failed: {str(e)}")
            raise


class OptionalFeatureRemovalMigration(AsyncMigration):
    """Remove retired Podcast/Transformation data only after a strict baseline check."""

    _expected_transformations = {
        "Simple Summary", "Reflections", "Dense Summary", "Key Insights",
        "Table of Contents", "Analyze Paper",
    }
    _expected_episode_profiles = {"solo_expert", "tech_discussion", "business_analysis"}
    _expected_speaker_profiles = {"business_panel", "solo_expert", "tech_experts"}

    async def run(self, bump: bool = True) -> None:
        try:
            transformations = await repo_query("SELECT VALUE name FROM transformation;")
            episode_profiles = await repo_query("SELECT VALUE name FROM episode_profile;")
            speaker_profiles = await repo_query("SELECT VALUE name FROM speaker_profile;")
            episodes = await repo_query("SELECT id FROM episode LIMIT 1;")
            podcast_config = await repo_query("SELECT id FROM podcast_config LIMIT 1;")
            media_roots = [Path("data/podcasts"), Path("data/podcast"), Path("data/episodes")]
            has_media = any(path.exists() and any(path.rglob("*")) for path in media_roots)

            observed = {
                "transformations": {str(item) for item in transformations or []},
                "episode_profiles": {str(item) for item in episode_profiles or []},
                "speaker_profiles": {str(item) for item in speaker_profiles or []},
            }
            empty = not any(observed.values()) and not episodes and not podcast_config and not has_media
            baseline_matches = (
                observed["transformations"] == self._expected_transformations
                and observed["episode_profiles"] == self._expected_episode_profiles
                and observed["speaker_profiles"] == self._expected_speaker_profiles
                and not episodes and not podcast_config and not has_media
            )
            if not (empty or baseline_matches):
                raise RuntimeError(
                    "Retired feature migration stopped: Podcast/Transformation data differs from the approved baseline. "
                    "No records or files were deleted."
                )
            if baseline_matches:
                async with db_connection() as connection:
                    await connection.query(
                        "DELETE transformation; DELETE episode_profile; DELETE speaker_profile; "
                        "REMOVE TABLE IF EXISTS transformation; REMOVE TABLE IF EXISTS episode; "
                        "REMOVE TABLE IF EXISTS episode_profile; REMOVE TABLE IF EXISTS speaker_profile; "
                        "REMOVE TABLE IF EXISTS podcast_config;"
                    )
            if bump:
                await bump_version()
            else:
                await lower_version()
        except Exception as exc:
            logger.error(f"Retired feature migration failed safely: {exc}")
            raise


class AsyncMigrationRunner:
    """
    Handles running multiple migrations in sequence.
    """

    def __init__(
        self,
        up_migrations: List[AsyncMigration],
        down_migrations: List[AsyncMigration],
    ) -> None:
        """Initialize runner with migration lists."""
        self.up_migrations = up_migrations
        self.down_migrations = down_migrations

    async def run_all(self) -> None:
        """Run all pending up migrations."""
        current_version = await get_latest_version()

        for i in range(current_version, len(self.up_migrations)):
            logger.info(f"Running migration {i + 1}")
            await self.up_migrations[i].run(bump=True)

    async def run_one_up(self) -> None:
        """Run one up migration."""
        current_version = await get_latest_version()

        if current_version < len(self.up_migrations):
            logger.info(f"Running migration {current_version + 1}")
            await self.up_migrations[current_version].run(bump=True)

    async def run_one_down(self) -> None:
        """Run one down migration."""
        current_version = await get_latest_version()

        if current_version > 0:
            logger.info(f"Rolling back migration {current_version}")
            await self.down_migrations[current_version - 1].run(bump=False)


class AsyncMigrationManager:
    """
    Main migration manager with async support.
    """

    def __init__(self):
        """Initialize migration manager."""
        self.up_migrations = [
            AsyncMigration.from_file("open_notebook/database/migrations/1.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/2.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/3.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/4.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/5.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/6.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/7.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/8.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/9.surrealql"),
            AsyncMigration.from_file("open_notebook/database/migrations/10.surrealql"),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/11.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/12.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/13.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/14.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/15.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/16.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/17.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/18.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/19.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/20.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/21.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/22.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/23.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/24.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/25.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/26.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/27.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/28.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/29.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/30.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/31.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/32.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/33.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/34.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/35.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/36.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/37.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/38.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/39.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/40.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/41.surrealql"
            ),
            OptionalFeatureRemovalMigration(""),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/43.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/44.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/45.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/46.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/47.surrealql"
            ),
            # Migration 48 is intentionally reserved for the opt-in chat
            # memory release.  This file is logical migration 48 while the
            # filename remains 49 to avoid applying the reserved migration.
            AsyncMigration.from_file(
                "open_notebook/database/migrations/49.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/50.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/51.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/52.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/53.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/54.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/55.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/56.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/57.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/58.surrealql"
            ),
        ]
        self.down_migrations = [
            AsyncMigration.from_file(
                "open_notebook/database/migrations/1_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/2_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/3_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/4_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/5_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/6_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/7_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/8_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/9_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/10_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/11_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/12_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/13_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/14_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/15_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/16_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/17_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/18_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/19_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/20_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/21_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/22_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/23_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/24_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/25_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/26_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/27_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/28_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/29_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/30_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/31_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/32_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/33_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/34_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/35_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/36_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/37_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/38_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/39_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/40_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/41_down.surrealql"
            ),
            AsyncMigration(""),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/43_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/44_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/45_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/46_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/47_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/49_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/50_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/51_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/52_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/53_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/54_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/55_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/56_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/57_down.surrealql"
            ),
            AsyncMigration.from_file(
                "open_notebook/database/migrations/58_down.surrealql"
            ),
        ]
        self.runner = AsyncMigrationRunner(
            up_migrations=self.up_migrations,
            down_migrations=self.down_migrations,
        )

    async def get_current_version(self) -> int:
        """Get current database version."""
        return await get_latest_version()

    async def needs_migration(self) -> bool:
        """Check if migration is needed."""
        current_version = await self.get_current_version()
        return current_version < len(self.up_migrations)

    async def run_migration_up(self):
        """Run all pending migrations."""
        await repo_query("DEFINE TABLE IF NOT EXISTS open_notebook SCHEMALESS;")
        await repo_query("DEFINE TABLE IF NOT EXISTS model SCHEMALESS;")
        current_version = await self.get_current_version()
        logger.info(f"Current version before migration: {current_version}")

        if await self.needs_migration():
            try:
                await self.runner.run_all()
                new_version = await self.get_current_version()
                logger.info(f"Migration successful. New version: {new_version}")
            except Exception as e:
                logger.error(f"Migration failed: {str(e)}")
                raise
        else:
            logger.info("Database is already at the latest version")


# Database version management functions
async def get_latest_version() -> int:
    """Get the latest version from the migrations table."""
    try:
        versions = await get_all_versions()
        if not versions:
            return 0
        return max(version["version"] for version in versions)
    except Exception:
        # If migrations table doesn't exist, we're at version 0
        return 0


async def get_all_versions() -> List[dict]:
    """Get all versions from the migrations table."""
    try:
        await repo_query("DEFINE TABLE IF NOT EXISTS _sbl_migrations SCHEMALESS;")
        result = await repo_query("SELECT * FROM _sbl_migrations ORDER BY version;")
        return result
    except Exception:
        # If table doesn't exist, return empty list
        return []


async def bump_version() -> None:
    """Bump the version by adding a new entry to migrations table."""
    current_version = await get_latest_version()
    new_version = current_version + 1

    await repo_query(
        "CREATE _sbl_migrations SET version = $version, applied_at = time::now();",
        {"version": new_version},
    )


async def lower_version() -> None:
    """Lower the version by removing the latest entry from migrations table."""
    current_version = await get_latest_version()
    if current_version > 0:
        await repo_query(
            "DELETE FROM _sbl_migrations WHERE version = $version;",
            {"version": current_version},
        )
