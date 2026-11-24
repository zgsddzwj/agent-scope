from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import aiosqlite
from agentscope.tool import FunctionTool, Toolkit
from dotenv import load_dotenv

from auth import hash_password

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

Priority = Literal["low", "normal", "high", "urgent"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    configured = Path(os.getenv("DATABASE_PATH", ".runtime/business.sqlite3"))
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


@asynccontextmanager
async def database() -> AsyncIterator[aiosqlite.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    try:
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
    finally:
        await connection.close()


async def init_db() -> None:
    async with database() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tenant_memberships (
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                order_no TEXT NOT NULL UNIQUE,
                product TEXT NOT NULL,
                amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
                status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT REFERENCES tenants(id),
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
            """,
        )
        await db.execute("PRAGMA journal_mode = WAL")

    await ensure_bootstrap_user("admin", "BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_PASSWORD")
    await ensure_bootstrap_user("user", "DEMO_USERNAME", "DEMO_USER_PASSWORD")
    await migrate_and_backfill_tenants()
    if os.getenv("SEED_DEMO_DATA", "true").lower() == "true":
        await seed_demo_data()


async def migrate_and_backfill_tenants() -> None:
    async with database() as db:
        for table in ("orders", "tickets", "sessions", "audit_logs"):
            columns = {row["name"] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
            if "tenant_id" not in columns:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT REFERENCES tenants(id)",
                )

        users = await (await db.execute("SELECT id, username, role FROM users")).fetchall()
        for user in users:
            await ensure_default_membership(
                db,
                user["id"],
                user["role"],
                f"{user['username']}-space",
                "管理租户" if user["role"] == "admin" else "演示租户",
            )

        for table in ("orders", "tickets", "sessions", "audit_logs"):
            rows = await (
                await db.execute(f"SELECT id, user_id FROM {table} WHERE tenant_id IS NULL")
            ).fetchall()
            for row in rows:
                membership = await (
                    await db.execute(
                        """
                        SELECT tenant_id FROM tenant_memberships
                        WHERE user_id = ? ORDER BY is_default DESC, created_at LIMIT 1
                        """,
                        (row["user_id"],),
                    )
                ).fetchone()
                if membership is not None:
                    await db.execute(
                        f"UPDATE {table} SET tenant_id = ? WHERE id = ?",
                        (membership["tenant_id"], row["id"]),
                    )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_tenant_user ON orders(tenant_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_tenant_user ON tickets(tenant_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_tenant_user ON sessions(tenant_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id)")
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_default_membership "
            "ON tenant_memberships(user_id) WHERE is_default = 1",
        )


async def ensure_default_membership(
    db: aiosqlite.Connection,
    user_id: str,
    role: str,
    slug: str,
    tenant_name: str,
) -> str:
    now = utc_now()
    tenant_id = secrets.token_hex(16)
    await db.execute(
        "INSERT OR IGNORE INTO tenants (id, slug, name, created_at) VALUES (?, ?, ?, ?)",
        (tenant_id, slug, tenant_name, now),
    )
    tenant = await (await db.execute("SELECT id FROM tenants WHERE slug = ?", (slug,))).fetchone()
    await db.execute(
        "UPDATE tenant_memberships SET is_default = 0 WHERE user_id = ?",
        (user_id,),
    )
    await db.execute(
        """
        INSERT INTO tenant_memberships (tenant_id, user_id, role, is_default, created_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(tenant_id, user_id) DO UPDATE SET
            role = excluded.role,
            is_default = 1
        """,
        (tenant["id"], user_id, role, now),
    )
    return tenant["id"]


async def ensure_bootstrap_user(
    role: str,
    username_key: str,
    password_key: str,
) -> None:
    username = os.getenv(username_key, "").strip()
    password = os.getenv(password_key, "")
    if not username or not password:
        raise RuntimeError(f"请配置 {username_key} 和 {password_key}。")
    if len(password) < 12:
        raise RuntimeError(f"{password_key} 至少需要 12 个字符。")
    async with database() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users (id, username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (secrets.token_hex(16), username, hash_password(password), role, utc_now()),
        )
        user = await (
            await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        ).fetchone()
        await ensure_default_membership(
            db,
            user["id"],
            role,
            f"{username}-space",
            "管理租户" if role == "admin" else "演示租户",
        )


async def seed_demo_data() -> None:
    username = os.getenv("DEMO_USERNAME", "demo")
    async with database() as db:
        user = await (
            await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        ).fetchone()
        if user is None:
            return
        user_id = user["id"]
        membership = await (
            await db.execute(
                "SELECT tenant_id FROM tenant_memberships WHERE user_id = ? AND is_default = 1",
                (user_id,),
            )
        ).fetchone()
        tenant_id = membership["tenant_id"]
        async with db.execute(
            "SELECT COUNT(*) AS count FROM orders WHERE tenant_id = ? AND user_id = ?",
            (tenant_id, user_id),
        ) as cursor:
            order_count = (await cursor.fetchone())["count"]
        if order_count == 0:
            now = utc_now()
            await db.executemany(
                """
                INSERT INTO orders
                    (id, tenant_id, user_id, order_no, product, amount_cents, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (secrets.token_hex(16), tenant_id, user_id, "SO-DEMO-1001", "机械键盘", 49900, "paid", now, now),
                    (secrets.token_hex(16), tenant_id, user_id, "SO-DEMO-1002", "显示器", 129900, "shipped", now, now),
                    (secrets.token_hex(16), tenant_id, user_id, "SO-DEMO-1003", "USB-C 扩展坞", 29900, "pending", now, now),
                ],
            )
        async with db.execute(
            "SELECT COUNT(*) AS count FROM tickets WHERE tenant_id = ? AND user_id = ?",
            (tenant_id, user_id),
        ) as cursor:
            ticket_count = (await cursor.fetchone())["count"]
        if ticket_count == 0:
            now = utc_now()
            await db.execute(
                """
                INSERT INTO tickets
                    (id, tenant_id, user_id, subject, description, priority, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    secrets.token_hex(16),
                    tenant_id,
                    user_id,
                    "确认显示器发货时间",
                    "希望确认订单 SO-DEMO-1002 的预计发货时间。",
                    "normal",
                    "open",
                    now,
                    now,
                ),
            )

        other_tenant_id = secrets.token_hex(16)
        now = utc_now()
        await db.execute(
            "INSERT OR IGNORE INTO tenants (id, slug, name, created_at) VALUES (?, ?, ?, ?)",
            (other_tenant_id, "other-demo-space", "第二演示租户", now),
        )
        other_tenant = await (
            await db.execute("SELECT id FROM tenants WHERE slug = 'other-demo-space'")
        ).fetchone()
        await db.execute(
            """
            INSERT OR IGNORE INTO tenant_memberships
                (tenant_id, user_id, role, is_default, created_at)
            VALUES (?, ?, 'user', 0, ?)
            """,
            (other_tenant["id"], user_id, now),
        )
        async with db.execute(
            "SELECT COUNT(*) AS count FROM orders WHERE tenant_id = ? AND user_id = ?",
            (other_tenant["id"], user_id),
        ) as cursor:
            other_order_count = (await cursor.fetchone())["count"]
        if other_order_count == 0:
            await db.execute(
                """
                INSERT INTO orders
                    (id, tenant_id, user_id, order_no, product, amount_cents, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    secrets.token_hex(16),
                    other_tenant["id"],
                    user_id,
                    "SO-DEMO-OTHER-1001",
                    "第二租户示例商品",
                    9900,
                    "paid",
                    now,
                    now,
                ),
            )


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def list_user_tenants(user_id: str) -> list[dict[str, Any]]:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT t.id, t.slug, t.name, m.role, m.is_default
            FROM tenant_memberships m JOIN tenants t ON t.id = m.tenant_id
            WHERE m.user_id = ? ORDER BY m.is_default DESC, t.created_at
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_tenant_membership(user_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT t.id, t.slug, t.name, m.role, m.is_default
            FROM tenant_memberships m JOIN tenants t ON t.id = m.tenant_id
            WHERE m.user_id = ? AND m.tenant_id = ?
            """,
            (user_id, tenant_id),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def create_session(
    tenant_id: str,
    user_id: str,
    title: str,
    state_json: str,
) -> dict[str, Any]:
    session_id = secrets.token_urlsafe(18)
    now = utc_now()
    async with database() as db:
        await db.execute(
            """
            INSERT INTO sessions
                (id, tenant_id, user_id, title, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, tenant_id, user_id, title[:80], state_json, now, now),
        )
    return {"id": session_id, "title": title[:80], "created_at": now, "updated_at": now}


async def get_session(
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, title, state_json, created_at, updated_at
            FROM sessions WHERE id = ? AND tenant_id = ? AND user_id = ?
            """,
            (session_id, tenant_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    result = dict(row)
    result.pop("user_id")
    return result


async def list_sessions(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, created_at, updated_at FROM sessions
            WHERE tenant_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 100
            """,
            (tenant_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def save_session(
    tenant_id: str,
    user_id: str,
    session_id: str,
    title: str,
    state_json: str,
) -> None:
    async with database() as db:
        cursor = await db.execute(
            """
            UPDATE sessions SET title = ?, state_json = ?, updated_at = ?
            WHERE id = ? AND tenant_id = ? AND user_id = ?
            """,
            (title[:80], state_json, utc_now(), session_id, tenant_id, user_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("会话不存在或不属于当前租户和用户。")


async def delete_session(tenant_id: str, user_id: str, session_id: str) -> bool:
    async with database() as db:
        cursor = await db.execute(
            "DELETE FROM sessions WHERE id = ? AND tenant_id = ? AND user_id = ?",
            (session_id, tenant_id, user_id),
        )
        return cursor.rowcount == 1


async def list_orders(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, order_no, product, amount_cents, status, created_at, updated_at
            FROM orders WHERE tenant_id = ? AND user_id = ?
            ORDER BY created_at DESC LIMIT 50
            """,
            (tenant_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_order(
    tenant_id: str,
    user_id: str,
    order_no: str,
) -> dict[str, Any] | None:
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, order_no, product, amount_cents, status, created_at, updated_at
            FROM orders WHERE tenant_id = ? AND user_id = ? AND order_no = ?
            """,
            (tenant_id, user_id, order_no.strip()),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def create_ticket(
    tenant_id: str,
    user_id: str,
    subject: str,
    description: str,
    priority: Priority,
) -> dict[str, Any]:
    subject = subject.strip()
    description = description.strip()
    if not 1 <= len(subject) <= 120:
        raise ValueError("工单主题长度必须为 1 到 120 个字符。")
    if not 1 <= len(description) <= 2000:
        raise ValueError("工单描述长度必须为 1 到 2000 个字符。")
    if priority not in {"low", "normal", "high", "urgent"}:
        raise ValueError("无效的工单优先级。")
    ticket_id = secrets.token_hex(16)
    now = utc_now()
    async with database() as db:
        await db.execute(
            """
            INSERT INTO tickets
                (id, tenant_id, user_id, subject, description, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (ticket_id, tenant_id, user_id, subject, description, priority, now, now),
        )
        await record_audit(
            db,
            tenant_id,
            user_id,
            "create_ticket",
            "ticket",
            ticket_id,
            {"subject": subject, "priority": priority},
        )
    return {
        "ticket_id": ticket_id,
        "subject": subject,
        "priority": priority,
        "status": "open",
    }


async def list_tickets(
    tenant_id: str,
    user_id: str,
    status: Literal["all", "open", "closed"] = "all",
) -> list[dict[str, Any]]:
    if status not in {"all", "open", "closed"}:
        raise ValueError("status 只能是 all、open 或 closed。")
    sql = """
        SELECT id, subject, description, priority, status, created_at, updated_at
        FROM tickets WHERE tenant_id = ? AND user_id = ?
    """
    params: list[Any] = [tenant_id, user_id]
    if status != "all":
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT 50"
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def list_all_tickets(
    tenant_id: str,
    status: Literal["all", "open", "closed"] = "all",
) -> list[dict[str, Any]]:
    if status not in {"all", "open", "closed"}:
        raise ValueError("status 只能是 all、open 或 closed。")
    sql = """
        SELECT t.id, t.subject, t.description, t.priority, t.status,
               t.created_at, t.updated_at, u.username
        FROM tickets t JOIN users u ON u.id = t.user_id
        WHERE t.tenant_id = ?
    """
    params: list[Any] = [tenant_id]
    if status != "all":
        sql += " WHERE t.status = ?"
        params.append(status)
    sql += " ORDER BY t.created_at DESC LIMIT 100"
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def close_ticket(
    tenant_id: str,
    user_id: str,
    ticket_id: str,
) -> dict[str, Any]:
    async with database() as db:
        cursor = await db.execute(
            """
            UPDATE tickets SET status = 'closed', updated_at = ?
            WHERE id = ? AND tenant_id = ? AND user_id = ? AND status = 'open'
            """,
            (utc_now(), ticket_id, tenant_id, user_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("工单不存在、已关闭或不属于当前租户和用户。")
        await record_audit(
            db,
            tenant_id,
            user_id,
            "close_ticket",
            "ticket",
            ticket_id,
            {"status": "closed"},
        )
    return {"ticket_id": ticket_id, "status": "closed"}


async def record_audit(
    db: aiosqlite.Connection,
    tenant_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    await db.execute(
        """
        INSERT INTO audit_logs
            (tenant_id, user_id, action, entity_type, entity_id, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            user_id,
            action,
            entity_type,
            entity_id,
            json.dumps(details, ensure_ascii=False),
            utc_now(),
        ),
    )


async def audit_tool_confirmation(
    tenant_id: str,
    user_id: str,
    session_id: str,
    decisions: list[dict[str, Any]],
) -> None:
    async with database() as db:
        await record_audit(
            db,
            tenant_id,
            user_id,
            "confirm_tools",
            "session",
            session_id,
            {"decisions": decisions},
        )


async def list_audit_logs(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    async with database() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT a.id, a.action, a.entity_type, a.entity_id, a.details_json,
                   a.created_at, u.username
            FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.tenant_id = ? ORDER BY a.id DESC LIMIT ?
            """,
            (tenant_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        result.append(item)
    return result


def make_business_toolkit(user: dict[str, Any]) -> Toolkit:
    tenant_id = user["tenant_id"]
    user_id = user["id"]

    async def list_my_orders() -> str:
        """查询当前登录用户在当前租户中的全部订单，不需要参数。"""
        return json.dumps(await list_orders(tenant_id, user_id), ensure_ascii=False)

    async def get_order_status(order_no: str) -> str:
        """按订单号查询当前租户中属于当前用户的订单状态。"""
        order = await get_order(tenant_id, user_id, order_no)
        return json.dumps(order if order is not None else {"error": "订单不存在"}, ensure_ascii=False)

    async def list_my_tickets(status: Literal["all", "open", "closed"] = "all") -> str:
        """查询当前登录用户在当前租户中的工单。"""
        return json.dumps(await list_tickets(tenant_id, user_id, status), ensure_ascii=False)

    async def create_support_ticket(
        subject: str,
        description: str,
        priority: Priority = "normal",
    ) -> str:
        """为当前登录用户创建客服工单。这是写操作，必须经过用户确认。"""
        ticket = await create_ticket(tenant_id, user_id, subject, description, priority)
        return json.dumps(ticket, ensure_ascii=False)

    async def close_my_ticket(ticket_id: str) -> str:
        """关闭当前租户中属于当前登录用户的未关闭工单，需要用户确认。"""
        try:
            result = await close_ticket(tenant_id, user_id, ticket_id)
        except LookupError as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    tools = [
        FunctionTool(list_my_orders, is_read_only=True),
        FunctionTool(get_order_status, is_read_only=True),
        FunctionTool(list_my_tickets, is_read_only=True),
        FunctionTool(create_support_ticket, is_read_only=False),
        FunctionTool(close_my_ticket, is_read_only=False),
    ]
    if user["role"] == "admin":
        async def list_all_tickets(status: Literal["all", "open", "closed"] = "all") -> str:
            """管理员查询当前租户内所有用户的工单。"""
            return json.dumps(await list_all_tickets(tenant_id, status), ensure_ascii=False)

        tools.append(FunctionTool(list_all_tickets, is_read_only=True))
    return Toolkit(tools=tools)
