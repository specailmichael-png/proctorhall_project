# ProctorHall Test Document

## 1. Overview

ProctorHall is a Django-based online examination platform that supports student examinations, administrative exam setup, programming question workflows, and proctoring/audit logging. The application includes user registration, student and admin roles, exam creation and publication, MCQ and programming question handling, autosave/versioning of code answers, lecturer review and marking, and browser-side monitoring features.

This document defines the project test strategy, scope, and execution checklist for validating core behavior and regression risk.

## 2. Scope

### In Scope
- Authentication and authorization
- Student registration, login, and role enforcement
- Exam creation and question management
- Draft vs published exam behavior
- Attempt lifecycle and exam timing
- MCQ answer submission and scoring
- Programming answer autosave, version tracking, and final submission
- Lecturer review and manual marking
- Proctoring logs and monitoring events
- Legacy import behavior and data integrity
- Security restrictions and validation rules

### Out of Scope
- Performance benchmarking under heavy student load
- Cross-browser certification beyond standard supported browsers
- External AI or face-recognition model training
- Production infrastructure deployment testing

## 3. Objectives

The testing objective is to verify that:
- only valid users can access the correct flows,
- exams behave correctly under publication and attempt rules,
- programming answers are saved and submitted accurately,
- grading and review workflows are consistent,
- monitoring events are recorded without exposing data to students,
- sensitive operations are protected against invalid or malicious input,
- existing automated Django tests continue to pass.

## 4. Test Environment

### Recommended local environment
- Python 3.12
- Django 6.1
- SQLite for local/test execution
- Browser for manual UI validation (Chrome/Edge recommended)

### Commands
```powershell
cd proctorhall
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py test
```

### Test database behavior
The project is configured to use Django's temporary SQLite test database for automated tests, regardless of the configured production database.

## 5. Test Approach

### 5.1 Automated testing
The project already contains Django test coverage focused on security and integrity. These automated tests should be treated as the minimum regression suite.

Key automated checks include:
- student registration always creates a student role
- admin can create an exam and add both MCQ and programming questions
- draft exams cannot be started
- changing exam duration does not alter active students' attempt duration
- code autosave creates version history and final source submission
- final exam submission updates attempt score and logs
- programming answers can be reviewed and manually marked
- Java submissions can be downloaded for local testing

### 5.2 Manual testing
Manual test validation is still recommended for UI, browser security, and proctoring flows that are difficult to assert in code alone.

## 6. Test Cases

| ID | Area | Scenario | Expected Result | Priority |
|---|---|---|---|---|
| TC-01 | Authentication | Register with a valid University of Lusaka ID (`BIT` + 8 digits) | ID is stored as the student account name, can be used to sign in, and registration redirects to login | High |
| TC-02 | Authentication | Register with malformed or duplicate student ID | Validation error is shown and no account is created | High |
| TC-03 | Authentication | Log in as admin and student with valid credentials | Correct dashboard is shown based on role | High |
| TC-04 | Authorization | Non-admin attempts to access admin-only route | Access is denied or redirected | High |
| TC-05 | Exam Management | Create an exam with title and duration | Exam is persisted and visible in admin dashboard | High |
| TC-06 | Exam Management | Add MCQ question to exam | Question is saved with correct fields and marks | High |
| TC-07 | Exam Management | Add programming question with language and editor settings | Programming configuration is stored correctly | High |
| TC-08 | Exam Lifecycle | Publish an exam | Published exam becomes available for student start | High |
| TC-09 | Exam Lifecycle | Publish an exam without an access password | Publishing is rejected | High |
| TC-10 | Exam Lifecycle | Start a published exam with the wrong password | No attempt is created and access is denied | High |
| TC-11 | Exam Lifecycle | Start a published exam with the correct password | Attempt is created and exam begins | High |
| TC-12 | Exam Lifecycle | Start a draft exam as a student | Request fails and exam is not accessible | High |
| TC-13 | Exam Lifecycle | Update exam duration after attempt starts | Existing active attempt keeps original duration; new attempts use new duration | Medium |
| TC-14 | Attempt Flow | Student starts exam | Attempt is created with unique session and correct status | High |
| TC-15 | MCQ Grading | Submit MCQ answers | Score and answer records are saved correctly | High |
| TC-16 | Code Autosave | Save programming code multiple times | New versions are created and final source is updated | High |
| TC-17 | Submission | Submit final programming answer | Code submission timestamp and final version are stored | High |
| TC-18 | Review | Lecturer reviews a submission | Submitted code, versions, and metadata are available for viewing | High |
| TC-19 | Marking | Award marks and feedback | Attempt score updates correctly and feedback is displayed | High |
| TC-20 | Monitoring | Trigger browser events such as tab switch, fullscreen exit, or copy attempt | Proctor log records event with correct type and severity | High |
| TC-21 | Lockdown | Trigger repeated warnings during exam | Student exam is submitted automatically after warning threshold | High |
| TC-22 | Data Integrity | Attempt to submit invalid code payload | Server rejects invalid source or mismatched language | High |
| TC-23 | Import Flow | Import legacy data from SQLite source | Users, exams, attempts, and logs are loaded without duplicate conflicts | Medium |
| TC-24 | Security | Validate source length and content sanitation | Oversized or malformed payloads are rejected | High |
| TC-25 | Regression | Run complete Django test suite | All tests pass without failures | Critical |

## 7. Detailed Functional Validation

### 7.1 Authentication and role enforcement
Validate the following:
- registration requires a `BIT` plus 8 digit student ID, valid email, and password length of at least 8 characters,
- duplicate emails are rejected,
- public registration always creates a student account even if a role is submitted,
- admin-only pages require lecturer/admin privileges,
- admin users can access exam creation and review workflows.
- existing student accounts without a valid student ID are blocked from sign-in until an administrator updates them.

### 7.2 Exam access password
Validate the following:
- admins must set an exam access password of at least 8 characters to create an exam,
- the password is stored as a password hash, not plaintext,
- an exam without a password cannot be published,
- a student must provide the correct password before the first attempt is created,
- a wrong password does not create an attempt,
- students can resume their already-created in-progress attempt without restarting it.

### 7.3 Exam authoring
Validate the following:
- an admin can create an exam and set a title and duration,
- exams can include both MCQ and programming questions,
- each programming question stores language, marks, editor requirement, and execution permission,
- draft exams are not visible to students as startable assessments,
- published exams can be started only by eligible students.

### 7.4 Attempt lifecycle and scoring
Validate the following:
- only one active attempt per exam/student is allowed,
- active attempts retain the original duration even after exam settings change,
- a submitted exam records submitted_at and final score,
- MCQ answers are scored based on correct_option,
- programming answers contribute marks only when awarded manually.

### 7.5 Programming answer workflow
Validate the following:
- saving code creates a new version record,
- repeated identical saves do not create unnecessary duplicates,
- final submission writes the final version and submission timestamp,
- submissions can be reviewed, downloaded, or inspected by lecturers,
- Java submissions are handled via download for local compile/run testing.

### 7.6 Proctoring and audit review
Validate the following:
- monitoring events are captured for common student actions,
- event types are normalized through allowed aliases,
- severity levels are recorded correctly,
- the student does not see the event details during the exam,
- lecturers can review the chronological log and summary by severity/type.

### 7.7 Legacy data import and integrity
Validate the following:
- imported records preserve the original IDs when required,
- imported user accounts retain legacy password hashes and migrate on first successful login,
- rerunning the importer does not create duplicates or corrupt data,
- imported exams/questions/attempts match expected structure.

## 8. Security and Integrity Validation

The project includes explicit integrity-focused tests and should be validated for the following risk areas:
- unauthorized role assignment
- exam access control
- monopolized or spoofed submissions
- invalid payload handling
- oversized code submissions
- mismatch between submitted language and configured language
- duplicate attempt or duplicate answer record prevention
- tampering with exam results or hidden answer data

Recommended checks:
- verify that invalid submissions return an error rather than a silent success,
- test for tampering attempts in API/JSON payloads,
- verify that only admin users can update marks and feedback,
- confirm that code is not executed on the server and that runtime behavior is browser-bound.

## 9. Exit Criteria

The testing cycle is considered successful when:
- all automated Django tests pass,
- all critical and high-priority manual scenarios pass,
- no unresolved defect remains in the test scope for release,
- monitoring and integrity flows behave as expected under realistic student/admin actions.

## 10. Recommended Regression Run

Before any release, perform:
```powershell
python manage.py check
python manage.py test
```

Then run a focused manual smoke pass covering:
1. registration and login,
2. exam creation and publication,
3. student attempt start and submit,
4. code autosave and submission,
5. admin review and grading,
6. monitoring log review.

## 11. Summary

ProctorHall has a strong foundation of security and integrity-focused Django tests. This document supplements the automated coverage with a structured QA plan covering the student, admin, proctoring, and submission workflows. The most important operational risk areas are user role integrity, exam lifecycle rules, code submission correctness, and browser-based monitoring validation.
