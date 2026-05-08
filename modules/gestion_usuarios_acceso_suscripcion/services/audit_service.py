from ..models import AISubscriptionAuditLog


class AISubscriptionAuditService:
    def log(self, *, chef_profile, action, description, subscription=None, metadata=None, request=None):
        ip_address = None
        user_agent = ""
        if request:
            forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            ip_address = forwarded_for.split(",")[0].strip() or request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")
        return AISubscriptionAuditLog.objects.create(
            chef_profile=chef_profile,
            subscription=subscription,
            action=action,
            description=description,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
