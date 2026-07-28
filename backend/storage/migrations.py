from typing import Final, List, Tuple

Migration = Tuple[int, str]

MIGRATIONS: Final[List[Migration]] = [
    (
        1,
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT NOT NULL UNIQUE,
            github_username TEXT NOT NULL,
            github_email TEXT NOT NULL,
            github_password BLOB NOT NULL,
            github_created_at TEXT NOT NULL,
            opencode_provider_name TEXT NOT NULL UNIQUE,
            opencode_workspace_id TEXT NOT NULL,
            opencode_api_key BLOB NOT NULL,
            opencode_user_id TEXT,
            email_provider TEXT NOT NULL,
            temp_email TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('active', 'exhausted', 'invalid', 'pending_payment', 'cancelled')
            ),
            quota_total INTEGER,
            quota_used INTEGER,
            quota_updated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE pool_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_account_id TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (current_account_id) REFERENCES accounts(uuid) ON DELETE SET NULL
        );

        CREATE TABLE operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT,
            level TEXT NOT NULL CHECK (level IN ('info', 'warning', 'error')),
            step TEXT NOT NULL,
            message TEXT NOT NULL,
            screenshot_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(uuid) ON DELETE CASCADE
        );

        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX idx_accounts_status ON accounts(status);
        CREATE INDEX idx_operation_logs_account_id ON operation_logs(account_id);
        """,
    ),
    (
        2,
        """
        CREATE TABLE account_cleanup_operations (
            account_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('requested', 'remote_deleted')),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES accounts(uuid) ON DELETE CASCADE
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE pending_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT NOT NULL UNIQUE,
            github_username TEXT NOT NULL UNIQUE,
            github_email TEXT NOT NULL,
            github_password BLOB NOT NULL,
            github_created_at TEXT NOT NULL,
            email_provider TEXT NOT NULL,
            temp_email TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending_setup', 'pending_payment', 'cancelled', 'invalid')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE pending_account_cleanup_operations (
            account_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('requested', 'remote_deleted')),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES pending_accounts(uuid) ON DELETE CASCADE
        );

        CREATE INDEX idx_pending_accounts_status ON pending_accounts(status);
        """,
    ),
    (
        4,
        """
        ALTER TABLE accounts ADD COLUMN quota_checked_at TEXT;
        ALTER TABLE accounts ADD COLUMN quota_invalid_reason TEXT;

        UPDATE accounts
        SET quota_checked_at = updated_at, quota_invalid_reason = 'unknown'
        WHERE status = 'invalid';
        """,
    ),
    (
        5,
        """
        ALTER TABLE accounts ADD COLUMN opencode_configured INTEGER NOT NULL DEFAULT 1
            CHECK (opencode_configured IN (0, 1));
        ALTER TABLE accounts ADD COLUMN omo_configured INTEGER NOT NULL DEFAULT 1
            CHECK (omo_configured IN (0, 1));
        """,
    ),
    (
        6,
        """
        ALTER TABLE accounts ADD COLUMN github_auth_state BLOB;
        ALTER TABLE accounts ADD COLUMN opencode_auth_state BLOB;
        ALTER TABLE pending_accounts ADD COLUMN github_auth_state BLOB;
        ALTER TABLE pending_accounts ADD COLUMN opencode_auth_state BLOB;
        """,
    ),
]
