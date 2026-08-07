"""Authentication: signup, login, refresh rotation with reuse detection, resets."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import ConflictError, UnauthorizedError
from app.core.logging import log
from app.models.user import RefreshToken, User

logger = log("auth")


async def signup(session: AsyncSession, *, email: str, name: str, password: str) -> User:
    email = email.strip().lower()
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An account with this email already exists")
    user = User(email=email, name=name.strip(), password_hash=security.hash_password(password))
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None and user.password_hash is None:
        # Social-login-only account: no password can ever match, and the generic
        # message would steer the user to password reset instead of the button
        # that works.
        raise UnauthorizedError(
            "This account uses social login — continue with Google or GitHub, "
            "or set a password via password reset"
        )
    if user is None or not security.verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")
    user.last_seen_at = utcnow()
    return user


async def issue_refresh_token(
    session: AsyncSession, user: User, *, user_agent: str | None = None, ip: str | None = None
) -> str:
    settings = get_settings()
    token, jti = security.create_refresh_token(user.id)
    session.add(
        RefreshToken(
            id=jti,
            user_id=user.id,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )
    )
    await session.flush()
    return token


async def rotate_refresh_token(
    session: AsyncSession, raw_token: str, *, user_agent: str | None = None, ip: str | None = None
) -> tuple[User, str]:
    """Validate + rotate. A rotated-then-reused token revokes the whole family."""
    payload = security.decode_token(raw_token, "refresh")
    record = await session.get(RefreshToken, payload["jti"])
    now = utcnow()
    settings = get_settings()
    if record is None:
        raise UnauthorizedError("Unknown refresh token")
    if record.revoked_at is not None or record.replaced_by is not None:
        # A token consumed *by rotation* moments ago is almost always a benign
        # race — a second tab, a Set-Cookie response lost to a reload, a
        # back/forward-cache replay — not theft. Nuking the whole family here is
        # what logged users out of every tab.
        #
        # Within the grace window we advance the ONE existing chain from its live
        # head rather than minting an independent sibling. That is the crucial
        # difference: a sibling would fork a second chain that never shares a
        # consumed token with the first, so a genuinely stolen token replayed
        # here would live forever, invisible to reuse detection. By rotating the
        # head instead, there is always a single chain — a stolen token replayed
        # in grace still consumes the head, and the legitimate holder's next
        # refresh (minutes later, well outside grace) presents that now-consumed
        # token and trips the family wipe. Tokens revoked *without* a successor
        # (logout, password change, admin revoke) never get grace.
        grace = timedelta(seconds=settings.refresh_rotation_grace_seconds)
        rotated_recently = (
            record.replaced_by is not None
            and record.revoked_at is not None
            and now - record.revoked_at <= grace
        )
        head = await _live_chain_head(session, record) if rotated_recently else None
        if head is None:
            # Reuse of a consumed token outside grace (or a chain whose head was
            # explicitly revoked) → assume theft, revoke everything for the user.
            # Commit immediately: the request will end 401 and roll back otherwise.
            logger.warning("refresh token reuse detected for user %s", record.user_id)
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await session.commit()
            raise UnauthorizedError("Refresh token reuse detected; please log in again")
        # Rotate the current head, not the replayed token — keeps one chain.
        record = head
    if record.expires_at <= now:
        raise UnauthorizedError("Refresh token expired")

    user = await session.get(User, record.user_id)
    if user is None:
        raise UnauthorizedError("Unknown user")

    new_token, new_jti = security.create_refresh_token(user.id)
    session.add(
        RefreshToken(
            id=new_jti,
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )
    )
    record.replaced_by = new_jti
    record.revoked_at = now
    await session.flush()
    return user, new_token


async def _live_chain_head(session: AsyncSession, record: RefreshToken) -> RefreshToken | None:
    """Follow ``replaced_by`` to the chain's current head. Returns None when the
    chain was terminated (a link revoked without a successor — logout / reset /
    admin revoke), which must NOT get grace. Bounded walk with a cycle guard."""
    head = record
    seen: set[str] = {head.id}
    for _ in range(64):  # chains are short; this is a safety bound
        if head.replaced_by is None:
            # A head that was revoked without being rotated = terminated session.
            return None if head.revoked_at is not None else head
        nxt = await session.get(RefreshToken, head.replaced_by)
        if nxt is None or nxt.id in seen:
            return None
        seen.add(nxt.id)
        head = nxt
    return None


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    try:
        payload = security.decode_token(raw_token, "refresh")
    except UnauthorizedError:
        return  # logout is idempotent
    record = await session.get(RefreshToken, payload["jti"])
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow()


async def request_password_reset(session: AsyncSession, email: str) -> str | None:
    """Returns the reset token if the account exists (caller emails it)."""
    user = (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()
    if user is None:
        return None  # do not reveal account existence
    return security.create_password_reset_token(user.id)


async def reset_password(session: AsyncSession, *, token: str, new_password: str) -> User:
    payload = security.decode_token(token, "password_reset")
    user = await session.get(User, payload["sub"])
    if user is None:
        raise UnauthorizedError("Unknown user")
    user.password_hash = security.hash_password(new_password)
    # Password change invalidates every session.
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return user
