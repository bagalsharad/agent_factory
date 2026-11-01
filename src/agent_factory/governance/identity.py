"""Authentication: local mode, API keys and OIDC (JWT via JWKS). Users and workspaces bootstrap."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import AuthMode, Settings
from ..core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from ..core.ids import utcnow
from ..storage.db import Database
from ..storage.models import ApiKey, Membership, User, Workspace
from .rbac import Principal, Role, make_principal

DEFAULT_WORKSPACE_SLUG = "default"
LOCAL_SUBJECT = "local-admin"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class IdentityService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._jwks: jwt.PyJWKClient | None = None

    # ------------------------------------------------------------------ bootstrap
    def ensure_default_workspace(self, session: Session) -> Workspace:
        ws = session.scalars(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG)).first()
        if not ws:
            ws = Workspace(slug=DEFAULT_WORKSPACE_SLUG, name="Default workspace")
            session.add(ws)
            session.flush()
        return ws

    def ensure_user(
        self,
        session: Session,
        subject: str,
        *,
        email: str | None = None,
        display_name: str = "",
        role: str = Role.VIEWER,
        workspace: Workspace | None = None,
    ) -> User:
        user = session.scalars(select(User).where(User.subject == subject)).first()
        if not user:
            user = User(subject=subject, email=email, display_name=display_name or subject)
            session.add(user)
            session.flush()
        ws = workspace or self.ensure_default_workspace(session)
        membership = session.scalars(
            select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
        ).first()
        if not membership:
            session.add(Membership(user_id=user.id, workspace_id=ws.id, role=str(role)))
            session.flush()
        return user

    def bootstrap(self) -> None:
        with self.db.transaction() as session:
            ws = self.ensure_default_workspace(session)
            if self.settings.auth_mode == AuthMode.LOCAL:
                self.ensure_user(session, LOCAL_SUBJECT, display_name="Local developer", role=Role.ADMIN, workspace=ws)

    # ------------------------------------------------------------------ API keys
    def create_api_key(self, user_id: str, name: str, expires_at: datetime | None = None) -> tuple[str, ApiKey]:
        prefix = secrets.token_hex(4)
        raw = f"af_{prefix}_{secrets.token_urlsafe(32)}"
        with self.db.transaction() as session:
            record = ApiKey(user_id=user_id, name=name, prefix=prefix, key_hash=hash_key(raw), expires_at=expires_at)
            session.add(record)
        return raw, record

    def revoke_api_key(self, key_id: str) -> None:
        with self.db.transaction() as session:
            record = session.get(ApiKey, key_id)
            if not record:
                raise NotFoundError("API key not found")
            record.revoked_at = utcnow()

    def _from_api_key(self, session: Session, raw: str) -> User:
        parts = raw.split("_", 2)
        if len(parts) != 3 or parts[0] != "af":
            raise AuthenticationError("Malformed API key")
        record = session.scalars(select(ApiKey).where(ApiKey.prefix == parts[1])).first()
        if not record or not hmac.compare_digest(record.key_hash, hash_key(raw)):
            raise AuthenticationError("Invalid API key")
        if record.revoked_at or (record.expires_at and record.expires_at < utcnow()):
            raise AuthenticationError("API key revoked or expired")
        record.last_used_at = utcnow()
        user = session.get(User, record.user_id)
        if not user or not user.is_active:
            raise AuthenticationError("User is inactive")
        return user

    # ------------------------------------------------------------------ OIDC
    def _decode_jwt(self, token: str) -> dict[str, Any]:
        s = self.settings
        if not (s.oidc_issuer and s.oidc_jwks_url):
            raise AuthenticationError("OIDC is not configured")
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(s.oidc_jwks_url, cache_keys=True)
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            options: Any = {"require": ["exp", "iat", "sub"], "verify_aud": bool(s.oidc_audience)}
            return jwt.decode(
                token,
                key.key,
                algorithms=["RS256", "ES256", "PS256"],
                issuer=s.oidc_issuer,
                audience=s.oidc_audience,
                options=options,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"Invalid identity token: {exc.__class__.__name__}") from exc

    def _from_oidc(self, session: Session, token: str) -> tuple[User, str | None]:
        claims = self._decode_jwt(token)
        roles = claims.get(self.settings.oidc_roles_claim) or []
        if isinstance(roles, str):
            roles = [roles]
        valid = [r for r in roles if r in {x.value for x in Role}]
        order = [Role.ADMIN, Role.DEVELOPER, Role.OPERATOR, Role.EVALUATOR, Role.VIEWER]
        role = next((r for r in order if r.value in valid), None)
        user = self.ensure_user(
            session,
            f"oidc:{claims['sub']}",
            email=claims.get("email"),
            display_name=claims.get("name", claims.get("email", claims["sub"])),
            role=role or self.settings.oidc_default_role,
        )
        return user, role.value if role else None

    # ------------------------------------------------------------------ resolution
    def authenticate(self, authorization: str | None, workspace_ref: str | None) -> Principal:
        mode = self.settings.auth_mode
        with self.db.transaction() as session:
            role_override: str | None = None
            if mode == AuthMode.LOCAL:
                user = session.scalars(select(User).where(User.subject == LOCAL_SUBJECT)).first()
                if user is None:
                    user = self.ensure_user(session, LOCAL_SUBJECT, display_name="Local developer", role=Role.ADMIN)
                method = "local"
            else:
                if not authorization or not authorization.lower().startswith("bearer "):
                    raise AuthenticationError("Missing bearer credentials")
                token = authorization[7:].strip()
                if token.startswith("af_"):
                    user, method = self._from_api_key(session, token), "api_key"
                elif self.settings.oidc_issuer:
                    (user, role_override), method = self._from_oidc(session, token), "oidc"
                else:
                    raise AuthenticationError("Unsupported credential type")
            return self._principal(session, user, workspace_ref, method, role_override)

    def _principal(
        self, session: Session, user: User, workspace_ref: str | None, method: str, role_override: str | None
    ) -> Principal:
        query = (
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == user.id)
        )
        rows = session.execute(query).all()
        if not rows:
            raise PermissionDeniedError("User has no workspace membership")
        if workspace_ref:
            match = [(m, w) for m, w in rows if workspace_ref in (w.id, w.slug)]
            if not match:
                # do not reveal whether the workspace exists
                raise PermissionDeniedError("No access to the requested workspace")
            membership, ws = match[0]
        else:
            preferred = [(m, w) for m, w in rows if w.slug == DEFAULT_WORKSPACE_SLUG]
            membership, ws = (preferred or rows)[0]
        role = membership.role
        if role_override and ws.slug == DEFAULT_WORKSPACE_SLUG and role_override != role:
            membership.role = role = role_override  # IdP is the source of truth for default-workspace roles
        return make_principal(user.id, user.subject, user.display_name, ws.id, role, method)

    def principal_for_user(self, user_id: str | None, workspace_id: str) -> Principal | None:
        """Re-resolve a requester's *current* role (revoked access stops queued/resumed executions)."""
        if not user_id:
            return None
        with self.db.session() as session:
            user = session.get(User, user_id)
            membership = session.scalars(
                select(Membership).where(Membership.user_id == user_id, Membership.workspace_id == workspace_id)
            ).first()
            if not user or not user.is_active or not membership:
                return None
            return make_principal(user.id, user.subject, user.display_name, workspace_id, membership.role, "delegated")

    def system_principal(self, workspace_id: str) -> Principal:
        """Used by background workers acting on behalf of an execution's original requester."""
        return make_principal("system", "system", "Agent Factory worker", workspace_id, Role.ADMIN, "system")
