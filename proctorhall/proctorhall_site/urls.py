from django.contrib import admin
from django.urls import path
from exams import views

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),
    path("student/exam/<int:exam_id>/start/", views.start_exam, name="start_exam"),
    path("student/attempt/<int:attempt_id>/", views.take_exam, name="take_exam"),
    path("student/attempt/<int:attempt_id>/save-code/", views.save_code, name="save_code"),
    path("student/attempt/<int:attempt_id>/run-code/", views.run_code, name="run_code"),
    path("student/attempt/<int:attempt_id>/submit/", views.submit_exam, name="submit_exam"),
    path("student/attempt/<int:attempt_id>/heartbeat/", views.heartbeat, name="heartbeat"),
    path("student/result/<int:attempt_id>/", views.result, name="result"),
    path("api/proctor-log/", views.proctor_log, name="proctor_log"),
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/exam/create/", views.create_exam, name="create_exam"),
    path("admin/exam/<int:exam_id>/", views.exam_detail, name="exam_detail"),
    path("admin/exam/<int:exam_id>/settings/", views.update_exam_settings, name="update_exam_settings"),
    path("admin/exam/<int:exam_id>/question/", views.add_question, name="add_question"),
    path("admin/exam/<int:exam_id>/publish/", views.toggle_publish, name="toggle_publish"),
    path("admin/exam/<int:exam_id>/results/", views.upload_results, name="upload_results"),
    path("admin/exam/<int:exam_id>/results/export/", views.export_results, name="export_results"),
    path("admin/programming-submissions/", views.programming_submissions, name="programming_submissions"),
    path("admin/programming-submissions/<int:answer_id>/", views.review_submission, name="review_submission"),
    path("admin/programming-submissions/<int:answer_id>/download-java/", views.download_java_submission, name="download_java_submission"),
    path("admin/attempt/<int:attempt_id>/audit/", views.attempt_audit, name="attempt_audit"),
]
