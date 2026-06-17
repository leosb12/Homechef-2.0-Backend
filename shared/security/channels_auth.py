from urllib.parse import parse_qs

from channels.db import database_sync_to_async


class SupabaseTokenAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        scope["user"] = await self._resolve_user(scope)
        return await self.inner(scope, receive, send)

    @database_sync_to_async
    def _resolve_user(self, scope):
        from shared.security.jwt_authentication import (
            SupabaseAuthUser,
            build_supabase_user_from_token,
            fetch_supabase_user,
            sync_user_profile,
        )

        raw_query = scope.get("query_string", b"").decode()
        params = parse_qs(raw_query)
        token = (params.get("token") or [""])[0].strip()
        if not token:
            return None
        try:
            try:
                user_data = build_supabase_user_from_token(token)
            except Exception:
                user_data = fetch_supabase_user(token)
            profile = sync_user_profile(user_data)
            if not profile.is_active:
                return None
            return SupabaseAuthUser(
                id=str(profile.supabase_user_id),
                supabase_user_id=str(profile.supabase_user_id),
                email=profile.email,
                role=profile.role,
                first_name=profile.first_name,
                last_name=profile.last_name,
                full_name=profile.full_name,
                avatar_url=profile.avatar_url,
                is_active=profile.is_active,
                profile=profile,
            )
        except Exception:
            return None
