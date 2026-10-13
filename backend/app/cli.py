"""运维命令行。

用法（容器内）：
    python -m app.cli create-admin admin@example.com "Admin" 's3cret-pw'
    python -m app.cli import-framework seeds/nist-csf-2.0.csv nist-csf-2.0 "NIST CSF 2.0" "NIST CSF 2.0" 2.0 nist.gov
    python -m app.cli purge-staging
    python -m app.cli rotate-secret <旧的 APP_SECRET_KEY>
"""

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  — 注册全部模型，跨模块外键才能解析
from app.db import session_factory
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def create_admin(
    email: str, name: str, password: str, *, session: AsyncSession | None = None
) -> None:
    """幂等：已存在则重置密码并提升为 admin。

    这是管理员把自己锁死之后的唯一退路，所以停用状态和过期时间也要一并清掉，
    只改密码是不够的。
    """

    if len(password) < 8:
        raise ValueError("密码至少八位")

    async def _run(db: AsyncSession) -> None:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(
                User(
                    email=email,
                    name=name,
                    role=Role.ADMIN,
                    password_hash=hash_password(password),
                )
            )
        else:
            user.role = Role.ADMIN
            user.is_active = True
            user.expires_at = None
            user.password_hash = hash_password(password)
            user.token_version += 1
        await db.flush()

    if session is not None:
        await _run(session)
        return

    async with session_factory() as db:
        await _run(db)
        await db.commit()


async def import_framework_cli(
    path: str, key: str, name_zh: str, name_en: str, version: str, source: str
) -> None:
    """种子与人工导入走同一条代码路径，不存在第二个实现。"""
    import csv
    from pathlib import Path

    from app.frameworks.importer import import_framework

    text = Path(path).read_text(encoding="utf-8-sig")
    reader = list(csv.reader(text.splitlines()))
    if not reader:
        raise SystemExit(f"{path} 是空文件")
    headers, rows = reader[0], reader[1:]

    async with session_factory() as session:
        actor = await session.scalar(
            select(User).where(User.role == Role.ADMIN).order_by(User.id).limit(1)
        )
        if actor is None:
            raise SystemExit("库中没有 admin 用户；请先执行 create-admin")
        framework = await import_framework(
            session,
            headers,
            rows,
            key=key,
            name_zh=name_zh,
            name_en=name_en,
            version=version,
            source=source,
            actor=actor,
        )
        await session.commit()
        print(f"已导入 {framework.key} v{framework.version}：{framework.item_count} 条")


def main() -> None:
    usage = (
        "用法:\n"
        "  python -m app.cli create-admin <email> <name> <password>\n"
        "  python -m app.cli import-framework <path> <key> <name_zh> <name_en> "
        "<version> <source>\n"
        "  python -m app.cli purge-staging\n"
        "  python -m app.cli rotate-secret <old APP_SECRET_KEY>"
    )
    match sys.argv[1:]:
        case ["create-admin", email, name, password]:
            try:
                asyncio.run(create_admin(email, name, password))
            except ValueError as exc:
                print(exc, file=sys.stderr)
                raise SystemExit(1) from exc
            print(f"管理员已就绪: {email}")
        case ["import-framework", path, key, name_zh, name_en, version, source]:
            asyncio.run(import_framework_cli(path, key, name_zh, name_en, version, source))
        case ["rotate-secret", old_key]:
            # 换主密钥：先把新值写进 .env / 环境变量并重启，再拿**旧**值跑这条，
            # 库里的密文才会跟着换过来。
            from app.secrets import rotate_cli

            print(asyncio.run(rotate_cli(old_key)))
        case ["purge-staging"]:
            # 平时由 worker 的 cron 跑；这里是「现在就跑一次」的入口。
            from app.ingest import staging

            print(asyncio.run(staging.purge_staging({})))
        case _:
            print(usage, file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
