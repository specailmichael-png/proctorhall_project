from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import ActiveAttemptSession, Answer, Attempt, CodeVersion, Exam, ProctorLog, Question, QuestionSnapshot, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "student_id", "role", "is_active", "is_staff")
    fieldsets = ((None, {"fields": ("email", "password")}), ("Profile", {"fields": ("name", "student_id", "role", "legacy_password_hash")} ), ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")} ), ("Dates", {"fields": ("last_login", "date_joined")} ))
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "name", "student_id", "password1", "password2", "role")} ),)
    search_fields = ("email", "name", "student_id")


for model in (Exam, Question, Attempt, QuestionSnapshot, Answer, CodeVersion, ProctorLog, ActiveAttemptSession):
    admin.site.register(model)
