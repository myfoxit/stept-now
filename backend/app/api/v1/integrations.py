"""Workspace integrations API: catalog listing, OAuth connect, app credentials.

All six routes require ``integrations:manage``. Connect/reconnect only mint the
authorize URL — the browser drives the round trip and the public callback in
``app.api.oauth_public`` finishes it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.integrations import (
    ConnectIn,
    ConnectOut,
    CredentialIn,
    CredentialOut,
    IntegrationsOut,
)
from app.services import integrations as integrations_service

router = APIRouter()

_MANAGE = Depends(require_perm(Perm.INTEGRATIONS_MANAGE))


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get("/integrations", response_model=IntegrationsOut, dependencies=[_MANAGE])
async def list_integrations(principal: Member, session: Db) -> IntegrationsOut:
    return await integrations_service.list_providers(session, principal.workspace.id)


@router.post("/integrations/{provider}/connect", response_model=ConnectOut, dependencies=[_MANAGE])
async def connect_integration(
    provider: str, principal: Member, session: Db, body: ConnectIn | None = None
) -> ConnectOut:
    url = await integrations_service.mint_connect_url(
        session,
        principal.workspace.id,
        provider,
        actor=_actor(principal),
        return_to=(body or ConnectIn()).return_to,
    )
    return ConnectOut(authorize_url=url)


@router.post(
    "/integrations/connections/{connection_id}/reconnect",
    response_model=ConnectOut,
    dependencies=[_MANAGE],
)
async def reconnect_integration(
    connection_id: str, principal: Member, session: Db, body: ConnectIn | None = None
) -> ConnectOut:
    url = await integrations_service.mint_reconnect_url(
        session,
        principal.workspace.id,
        connection_id,
        actor=_actor(principal),
        return_to=(body or ConnectIn()).return_to,
    )
    return ConnectOut(authorize_url=url)


@router.delete(
    "/integrations/connections/{connection_id}", response_model=Msg, dependencies=[_MANAGE]
)
async def disconnect_integration(connection_id: str, principal: Member, session: Db) -> Msg:
    await integrations_service.disconnect(
        session, principal.workspace.id, connection_id, actor=_actor(principal)
    )
    return Msg(message="Integration disconnected")


@router.put(
    "/integrations/{provider}/credentials", response_model=CredentialOut, dependencies=[_MANAGE]
)
async def put_credentials(
    provider: str, body: CredentialIn, principal: Member, session: Db
) -> CredentialOut:
    return await integrations_service.upsert_credential(
        session,
        principal.workspace.id,
        provider,
        actor=_actor(principal),
        client_id=body.client_id,
        client_secret=body.client_secret,
        extra=body.extra,
    )


@router.delete("/integrations/{provider}/credentials", response_model=Msg, dependencies=[_MANAGE])
async def delete_credentials(provider: str, principal: Member, session: Db) -> Msg:
    await integrations_service.delete_credential(
        session, principal.workspace.id, provider, actor=_actor(principal)
    )
    return Msg(message="Credential removed")
