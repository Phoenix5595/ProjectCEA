from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from shared.db import DatabasePoolOwner


class DatabasePoolOwnerTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.db_config = {
            "host": "localhost",
            "database": "test_db",
            "user": "test_user",
            "password": "test_password",
            "port": 5432,
        }
        self.logger = MagicMock()

    async def test_initialize_creates_and_records_pool(self) -> None:
        pool = MagicMock()
        with patch("shared.db.create_pool", new_callable=AsyncMock) as create_pool:
            create_pool.return_value = pool
            owner = DatabasePoolOwner(
                self.db_config,
                application_name="test_service",
                logger=self.logger,
            )

            self.assertTrue(await owner.initialize())

        create_pool.assert_awaited_once_with(
            self.db_config, application_name="test_service"
        )
        self.assertIs(owner._pool, pool)
        self.assertTrue(owner._db_connected)

    async def test_initialize_logs_failure_and_returns_false(self) -> None:
        with patch(
            "shared.db.create_pool",
            new_callable=AsyncMock,
            side_effect=OSError("database unavailable"),
        ):
            owner = DatabasePoolOwner(
                self.db_config,
                application_name="test_service",
                logger=self.logger,
            )

            self.assertFalse(await owner.initialize())

        self.logger.error.assert_called_once_with(
            "Failed to initialize database: database unavailable"
        )
        self.assertIsNone(owner._pool)
        self.assertFalse(owner._db_connected)

    async def test_get_pool_connects_lazily_and_close_updates_state(self) -> None:
        pool = MagicMock()
        pool.close = AsyncMock()
        with patch("shared.db.create_pool", new_callable=AsyncMock) as create_pool:
            create_pool.return_value = pool
            owner = DatabasePoolOwner(
                self.db_config,
                application_name="test_service",
                logger=self.logger,
            )

            self.assertIs(await owner._get_pool(), pool)
            await owner.close()

        create_pool.assert_awaited_once_with(
            self.db_config, application_name="test_service"
        )
        pool.close.assert_awaited_once_with()
        self.assertFalse(owner._db_connected)
        self.logger.info.assert_called_once_with("Database connection closed")
