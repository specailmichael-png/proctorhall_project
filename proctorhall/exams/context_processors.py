from django.conf import settings

from .models import Exam


def exam_runner_settings(request):
    """Values the exam screen needs to run code in the student's browser."""
    return {"PYODIDE_VERSION": settings.PYODIDE_VERSION}


def admin_navigation(request):
    context = {"sidebar_exams": [], "active_page": None, "active_exam_id": None}
    if not request.user.is_authenticated or not request.user.is_admin:
        return context
    context["sidebar_exams"] = Exam.objects.filter(created_by=request.user).order_by("-created_at")
    route = getattr(request.resolver_match, "url_name", "")
    context["active_page"] = {"admin_dashboard": "dashboard", "create_exam": "create"}.get(route)
    context["active_exam_id"] = request.resolver_match.kwargs.get("exam_id")
    return context
