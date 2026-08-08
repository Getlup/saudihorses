# -*- coding: utf-8 -*-
"""
اختبارات آلية لمنصّة المِرباط.
تشغيل: pytest -v
تستخدم قاعدة بيانات SQLite في الذاكرة، منفصلة تمامًا عن بيانات التطوير/الإنتاج.
"""
import io
import os
import re
import pytest
from PIL import Image

os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["FLASK_ENV"] = "development"  # لتجاوز اشتراط SECRET_KEY في الإنتاج فقط أثناء الاستيراد
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app as flask_app  # noqa: E402
from models import db, Stable, User, Horse, Package, Achievement, DailyTask, GalleryPhoto, Booking, Review  # noqa: E402


def make_test_image(name="test.png"):
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), color="blue").save(buf, format="PNG")
    buf.seek(0)
    return (buf, name)


@pytest.fixture()
def app():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    from app import limiter
    limiter.reset()
    with flask_app.app_context():
        db.create_all()
        stable = Stable(name_ar="مربط الاختبار", slug="test-stable")
        db.session.add(stable)
        db.session.commit()

        owner = User(name="مالك تجريبي", email="owner@test.com", stable_id=stable.id,
                     role=User.ROLE_STABLE_OWNER)
        owner.set_password("password123")
        db.session.add(owner)

        visitor = User(name="زائر تجريبي", email="visitor@test.com", stable_id=stable.id,
                       role=User.ROLE_VISITOR)
        visitor.set_password("password123")
        db.session.add(visitor)

        horse = Horse(stable_id=stable.id, name="الأصيل", service_type="training")
        db.session.add(horse)

        pkg = Package(stable_id=stable.id, name_ar="حصة فردية", session_count=1, price=150)
        db.session.add(pkg)
        db.session.commit()

    # لا نُبقي app_context مفتوحًا أثناء طلبات test_client — كل طلب يفتح سياقه الخاص،
    # وإبقاء سياق خارجي مفتوحًا يتعارض مع تخزين Flask-WTF لرمز CSRF داخل الجلسة.
    yield flask_app

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def get_csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf token not found in form"
    return m.group(1)


def login(client, identifier, password):
    r = client.get("/login")
    csrf = get_csrf(r.get_data(as_text=True))
    return client.post("/login", data={"username": identifier, "password": password, "csrf_token": csrf},
                        follow_redirects=True)


def logout(client):
    r = client.get("/login")
    csrf = get_csrf(r.get_data(as_text=True))
    return client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)


# ---------------------------------------------------------------- الصفحات العامة
def test_public_pages_load(client):
    for path in ["/", "/login", "/s/test-stable", "/s/test-stable/register", "/s/test-stable/book", "/healthz"]:
        assert client.get(path).status_code == 200


def test_404_for_unknown_page(client):
    assert client.get("/this-page-does-not-exist").status_code == 404


def test_security_headers_present_on_every_response(client):
    r = client.get("/")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    csp = r.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


# ---------------------------------------------------------------- المصادقة
def test_login_wrong_password_fails(client):
    r = login(client, "owner@test.com", "wrong-password")
    assert "اسم المستخدم أو كلمة المرور غير صحيحة" in r.get_data(as_text=True)


def test_login_correct_password_succeeds(client):
    r = login(client, "owner@test.com", "password123")
    assert r.status_code == 200
    assert "أهلًا بك" in r.get_data(as_text=True)


def test_post_without_csrf_token_rejected(client):
    r = client.post("/login", data={"username": "owner@test.com", "password": "password123"})
    assert r.status_code == 400


def test_register_rejects_short_password(client):
    r = client.get("/s/test-stable/register")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/register", data={
        "name": "شخص جديد", "email": "new@test.com", "phone": "0555000111",
        "password": "short", "terms_accepted": "on", "csrf_token": csrf
    })
    assert "8 أحرف" in r.get_data(as_text=True)


def test_register_creates_account_and_logs_in(client):
    from models import User
    r = client.get("/s/test-stable/register")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/register", data={
        "name": "شخص جديد", "email": "newvisitor@test.com", "phone": "0555000222",
        "password": "longenough123", "terms_accepted": "on", "csrf_token": csrf
    }, follow_redirects=True)
    assert r.status_code == 200

    with client.application.app_context():
        user = User.query.filter_by(email="newvisitor@test.com").first()
        assert user is not None
        assert user.phone == "+966555000222"


def test_register_blank_phone_does_not_collide(client):
    """حقلا جوال فارغَين لمستخدمَين مختلفَين لا يجب أن يتعارضا مع قيد UNIQUE — ينطبق على الحسابات
    اللي تُنشأ من لوحة الإدارة (owners/staff) لا على تسجيل الزوار، حيث الجوال أصبح إلزاميًا."""
    from models import User
    user = User(name="بدون جوال 1", email="blankphone-admin@test.com", phone=None,
                stable_id=1, role=User.ROLE_HORSE_OWNER)
    user.set_password("longenough123")
    user2 = User(name="بدون جوال 2", email="blankphone-admin2@test.com", phone=None,
                 stable_id=1, role=User.ROLE_HORSE_OWNER)
    user2.set_password("longenough123")
    with client.application.app_context():
        db.session.add_all([user, user2])
        db.session.commit()
        assert User.query.filter(
            User.email.in_(["blankphone-admin@test.com", "blankphone-admin2@test.com"])
        ).count() == 2


def test_register_rejects_missing_phone(client):
    r = client.get("/s/test-stable/register")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/register", data={
        "name": "شخص جديد", "email": "nophone@test.com", "phone": "", "password": "longenough123",
        "csrf_token": csrf
    })
    assert "تعبئة جميع الحقول" in r.get_data(as_text=True)


def test_register_rejects_without_terms_acceptance(client):
    r = client.get("/s/test-stable/register")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/register", data={
        "name": "شخص جديد", "email": "noterms@test.com", "phone": "0555000444",
        "password": "longenough123", "csrf_token": csrf
    })
    assert "الموافقة على الشروط" in r.get_data(as_text=True)


def test_register_rejects_invalid_phone_format(client):
    r = client.get("/s/test-stable/register")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/register", data={
        "name": "شخص جديد", "email": "badphone@test.com", "phone": "123", "password": "longenough123",
        "terms_accepted": "on", "csrf_token": csrf
    })
    assert "رقم الجوال غير صحيح" in r.get_data(as_text=True)


# ---------------------------------------------------------------- الصلاحيات (Authorization)
def test_visitor_cannot_access_admin_dashboard(client):
    login(client, "visitor@test.com", "password123")
    r = client.get("/admin")
    assert r.status_code == 403


def test_anonymous_redirected_to_login_for_admin(client):
    r = client.get("/admin", follow_redirects=True)
    assert "تسجيل الدخول" in r.get_data(as_text=True)


def test_visitor_cannot_view_another_visitors_booking(app, client):
    with app.app_context():
        from models import Stable, Booking, User
        stable = Stable.query.first()
        other_visitor = User(name="آخر", email="other@test.com", stable_id=stable.id,
                             role=User.ROLE_VISITOR)
        other_visitor.set_password("password123")
        db.session.add(other_visitor)
        db.session.commit()

        pkg = Package.query.first()
        visitor = User.query.filter_by(email="visitor@test.com").first()
        booking = Booking(stable_id=stable.id, visitor_id=visitor.id, package_id=pkg.id,
                          amount=pkg.price, sessions_remaining=1)
        db.session.add(booking)
        db.session.commit()
        bid = booking.id

    login(client, "other@test.com", "password123")
    r = client.get(f"/checkout/{bid}")
    assert r.status_code == 403


# ---------------------------------------------------------------- تدفق الحجز الكامل
def test_full_booking_flow(app, client):
    login(client, "visitor@test.com", "password123")
    with app.app_context():
        pkg = Package.query.first()
        pid = pkg.id

    r = client.get("/s/test-stable/book")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/book", data={"package_id": pid, "session_date": "2026-09-01",
                                    "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 302
    checkout_url = r.headers["Location"]

    r = client.get(checkout_url)
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(checkout_url, data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 302
    success_url = r.headers["Location"]

    r = client.get(success_url)
    assert r.status_code == 200
    assert "تم تأكيد حجزك" in r.get_data(as_text=True)


def test_booking_rejects_invalid_package(client):
    login(client, "visitor@test.com", "password123")
    r = client.get("/s/test-stable/book")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/book", data={"package_id": 99999, "csrf_token": csrf}, follow_redirects=True)
    assert "الباقة المختارة غير متاحة" in r.get_data(as_text=True)


# ---------------------------------------------------------------- عزل بيانات مالكي الخيول
def test_horse_owner_only_sees_own_horses(app, client):
    with app.app_context():
        stable = Stable.query.first()
        owner_a = User(name="مالك أ", email="owner_a@test.com", stable_id=stable.id,
                       role=User.ROLE_HORSE_OWNER)
        owner_a.set_password("password123")
        owner_b = User(name="مالك ب", email="owner_b@test.com", stable_id=stable.id,
                       role=User.ROLE_HORSE_OWNER)
        owner_b.set_password("password123")
        db.session.add_all([owner_a, owner_b])
        db.session.commit()

        horse_a = Horse(stable_id=stable.id, owner_id=owner_a.id, name="حصان أ")
        db.session.add(horse_a)
        db.session.commit()
        horse_a_id = horse_a.id

    login(client, "owner_b@test.com", "password123")
    r = client.get(f"/owner/horse/{horse_a_id}")
    assert r.status_code == 404  # first_or_404 على استعلام مُقيَّد بـ owner_id


# ---------------------------------------------------------------- إنشاء حسابات مالكي الخيول
def test_stable_owner_can_create_horse_owner_account(client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/owners/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/owners/new", data={
        "name": "مالك جديد", "email": "newowner@test.com", "password": "password123",
        "csrf_token": csrf
    }, follow_redirects=True)
    assert "تم إنشاء حساب" in r.get_data(as_text=True)

    # الحساب الجديد يقدر يسجّل دخوله فعليًا
    logout(client)
    r = login(client, "newowner@test.com", "password123")
    assert "أهلًا بك" in r.get_data(as_text=True)


def test_creating_horse_owner_rejects_duplicate_email(client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/owners/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/owners/new", data={
        "name": "تكرار", "email": "visitor@test.com", "password": "password123",
        "csrf_token": csrf
    })
    assert "مسجّل مسبقًا" in r.get_data(as_text=True)


def test_visitor_cannot_create_horse_owner_accounts(client):
    login(client, "visitor@test.com", "password123")
    r = client.get("/admin/owners/new")
    assert r.status_code == 403


# ---------------------------------------------------------------- منصّة متعددة المرابط
def test_stable_owner_cannot_access_platform_dashboard(client):
    login(client, "owner@test.com", "password123")
    r = client.get("/platform")
    assert r.status_code == 403


def test_super_admin_can_create_new_stable(app, client):
    with app.app_context():
        owner = User.query.filter_by(email="owner@test.com").first()
        owner.role = User.ROLE_SUPER_ADMIN
        db.session.commit()

    login(client, "owner@test.com", "password123")
    r = client.get("/platform/stables/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/platform/stables/new", data={
        "stable_name": "مربط جديد", "slug": "new-stable",
        "owner_name": "مالك ثانٍ", "owner_email": "owner2@test.com",
        "owner_password": "password123", "csrf_token": csrf,
    }, follow_redirects=True)
    assert "تم إنشاء مربط" in r.get_data(as_text=True)

    r = client.get("/")
    assert "مربط جديد" in r.get_data(as_text=True)

    # الحساب الجديد فعليًا يقدر يسجّل دخوله ويدير مربطه
    logout(client)
    r = login(client, "owner2@test.com", "password123")
    assert "أهلًا بك" in r.get_data(as_text=True)
    r = client.get("/admin")
    assert r.status_code == 200


def test_creating_stable_rejects_duplicate_slug(app, client):
    with app.app_context():
        owner = User.query.filter_by(email="owner@test.com").first()
        owner.role = User.ROLE_SUPER_ADMIN
        db.session.commit()

    login(client, "owner@test.com", "password123")
    r = client.get("/platform/stables/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/platform/stables/new", data={
        "stable_name": "تكرار", "slug": "test-stable",  # نفس سلج الإسطبل الموجود أصلًا
        "owner_name": "x", "owner_email": "dup@test.com",
        "owner_password": "password123", "csrf_token": csrf,
    })
    assert "مستخدم لمربط آخر" in r.get_data(as_text=True)


def test_visitor_cannot_book_at_a_different_stable(app, client):
    """اختبار أمان جوهري: زائر مسجّل في مربط لا يقدر يحجز في مربط آخر."""
    with app.app_context():
        other_stable = Stable(name_ar="مربط آخر", slug="other-stable")
        db.session.add(other_stable)
        db.session.commit()
        other_pkg = Package(stable_id=other_stable.id, name_ar="باقة", session_count=1, price=100)
        db.session.add(other_pkg)
        db.session.commit()
        other_pkg_id = other_pkg.id

    login(client, "visitor@test.com", "password123")  # هذا الزائر تابع لـ test-stable
    r = client.get("/s/other-stable/book")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/other-stable/book", data={
        "package_id": other_pkg_id, "csrf_token": csrf
    }, follow_redirects=True)
    assert "مربط مختلف" in r.get_data(as_text=True)


# ---------------------------------------------------------------- Lounge (إنجازات الخيول)
def test_public_lounge_and_moderation_pages_load(client):
    assert client.get("/lounge").status_code == 200


def test_stable_owner_can_submit_achievement(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/achievements/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/admin/horse/{hid}/achievements/new", data={
        "title": "المركز الأول - بطولة القدرة والتحمل", "csrf_token": csrf
    }, follow_redirects=True)
    assert "بانتظار موافقة" in r.get_data(as_text=True)

    with app.app_context():
        a = Achievement.query.filter_by(horse_id=hid).first()
        assert a.status == Achievement.STATUS_PENDING


def test_pending_achievement_not_shown_in_public_lounge(app, client):
    with app.app_context():
        stable = Stable.query.first()
        horse = Horse.query.first()
        owner = User.query.filter_by(email="owner@test.com").first()
        a = Achievement(horse_id=horse.id, stable_id=stable.id, submitted_by=owner.id,
                        title="إنجاز بانتظار المراجعة")
        db.session.add(a)
        db.session.commit()

    r = client.get("/lounge")
    assert "إنجاز بانتظار المراجعة" not in r.get_data(as_text=True)


def test_approved_achievement_shown_in_public_lounge(app, client):
    with app.app_context():
        stable = Stable.query.first()
        horse = Horse.query.first()
        owner = User.query.filter_by(email="owner@test.com").first()
        a = Achievement(horse_id=horse.id, stable_id=stable.id, submitted_by=owner.id,
                        title="إنجاز معتمد", status=Achievement.STATUS_APPROVED)
        db.session.add(a)
        db.session.commit()

    r = client.get("/lounge")
    assert "إنجاز معتمد" in r.get_data(as_text=True)


def test_stable_owner_cannot_access_lounge_moderation(client):
    login(client, "owner@test.com", "password123")
    r = client.get("/platform/lounge")
    assert r.status_code == 403


def test_super_admin_can_approve_achievement(app, client):
    with app.app_context():
        owner = User.query.filter_by(email="owner@test.com").first()
        owner.role = User.ROLE_SUPER_ADMIN
        db.session.commit()
        owner_id = owner.id

        stable = Stable.query.first()
        horse = Horse.query.first()
        a = Achievement(horse_id=horse.id, stable_id=stable.id, submitted_by=owner.id,
                        title="إنجاز قيد المراجعة")
        db.session.add(a)
        db.session.commit()
        aid = a.id

    login(client, "owner@test.com", "password123")
    r = client.get("/platform/lounge")
    assert "إنجاز قيد المراجعة" in r.get_data(as_text=True)
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/platform/lounge/{aid}/approve", data={"csrf_token": csrf}, follow_redirects=True)
    assert "تم اعتماد الإنجاز" in r.get_data(as_text=True)

    with app.app_context():
        a = db.session.get(Achievement, aid)
        assert a.status == Achievement.STATUS_APPROVED
        assert a.reviewed_by == owner_id


def test_horse_owner_cannot_submit_achievement_for_horse_not_theirs(app, client):
    with app.app_context():
        stable = Stable.query.first()
        owner_a = User(name="مالك أ", email="owner_a2@test.com", stable_id=stable.id,
                       role=User.ROLE_HORSE_OWNER)
        owner_a.set_password("password123")
        owner_b = User(name="مالك ب", email="owner_b2@test.com", stable_id=stable.id,
                       role=User.ROLE_HORSE_OWNER)
        owner_b.set_password("password123")
        db.session.add_all([owner_a, owner_b])
        db.session.commit()

        horse_a = Horse(stable_id=stable.id, owner_id=owner_a.id, name="حصان أ2")
        db.session.add(horse_a)
        db.session.commit()
        horse_a_id = horse_a.id

    login(client, "owner_b2@test.com", "password123")
    r = client.get(f"/owner/horse/{horse_a_id}/achievements/new")
    assert r.status_code == 404


# ---------------------------------------------------------------- نظام المهام اليومية
def test_visiting_tasks_page_auto_creates_all_12_tasks(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/tasks")
    assert r.status_code == 200

    with app.app_context():
        count = DailyTask.query.filter_by(horse_id=hid).count()
        assert count == len(DailyTask.TASK_TYPES)

    html = r.get_data(as_text=True)
    for _, label in DailyTask.TASK_TYPES:
        assert label in html


def test_visiting_tasks_page_twice_does_not_duplicate(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    client.get(f"/admin/horse/{hid}/tasks")
    client.get(f"/admin/horse/{hid}/tasks")

    with app.app_context():
        count = DailyTask.query.filter_by(horse_id=hid).count()
        assert count == len(DailyTask.TASK_TYPES)


def test_marking_task_done_updates_status_and_completion(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/tasks")
    csrf = get_csrf(r.get_data(as_text=True))

    with app.app_context():
        task = DailyTask.query.filter_by(horse_id=hid, task_type="morning_feeding").first()
        tid = task.id

    r = client.post(f"/admin/tasks/{tid}/update", data={
        "status": "done", "notes": "3 كغم شعير", "csrf_token": csrf
    }, follow_redirects=True)
    assert "1 / 12" in r.get_data(as_text=True)

    with app.app_context():
        t = db.session.get(DailyTask, tid)
        assert t.status == DailyTask.STATUS_DONE
        assert t.completed_by is not None
        assert t.completed_at is not None
        assert t.notes == "3 كغم شعير"


def test_marking_task_skipped_clears_completed_fields(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/tasks")
    csrf = get_csrf(r.get_data(as_text=True))

    with app.app_context():
        task = DailyTask.query.filter_by(horse_id=hid, task_type="grooming").first()
        tid = task.id

    client.post(f"/admin/tasks/{tid}/update", data={
        "status": "skipped", "notes": "غير مطلوب", "csrf_token": csrf
    })

    with app.app_context():
        t = db.session.get(DailyTask, tid)
        assert t.status == DailyTask.STATUS_SKIPPED
        assert t.completed_by is None
        assert t.completed_at is None


def test_dashboard_shows_task_completion_kpi(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    client.get(f"/admin/horse/{hid}/tasks")  # يضمن وجود المهام

    r = client.get("/admin")
    assert "إنجاز مهام اليوم" in r.get_data(as_text=True)


def test_horse_owner_can_view_but_not_update_tasks(app, client):
    with app.app_context():
        stable = Stable.query.first()
        owner = User(name="مالك حصان", email="taskowner@test.com", stable_id=stable.id,
                     role=User.ROLE_HORSE_OWNER)
        owner.set_password("password123")
        db.session.add(owner)
        db.session.commit()

        horse = Horse(stable_id=stable.id, owner_id=owner.id, name="حصان المهام")
        db.session.add(horse)
        db.session.commit()
        hid = horse.id

    login(client, "taskowner@test.com", "password123")
    r = client.get(f"/owner/horse/{hid}/tasks")
    assert r.status_code == 200
    # لا يوجد نموذج تعديل حالة في صفحة القراءة فقط
    assert 'name="status"' not in r.get_data(as_text=True)

    # ومباشرة عبر مسار الإدارة (POST) يُمنع تمامًا لأنه ليس ضمن الأدوار المسموحة
    with app.app_context():
        task = DailyTask.query.filter_by(horse_id=hid).first()
        tid = task.id
    r2 = client.get("/login")  # للحصول على رمز CSRF صالح ضمن نفس الجلسة
    csrf = get_csrf(r2.get_data(as_text=True))
    r = client.post(f"/admin/tasks/{tid}/update", data={"status": "done", "csrf_token": csrf})
    assert r.status_code == 403


def test_stable_owner_cannot_update_task_of_another_stable(app, client):
    with app.app_context():
        other_stable = Stable(name_ar="مربط آخر للمهام", slug="tasks-other-stable")
        db.session.add(other_stable)
        db.session.commit()
        other_horse = Horse(stable_id=other_stable.id, name="حصان مربط آخر")
        db.session.add(other_horse)
        db.session.commit()
        other_horse_id = other_horse.id

    login(client, "owner@test.com", "password123")  # مالك مربط test-stable
    r = client.get(f"/admin/horse/{other_horse_id}/tasks")
    assert r.status_code == 404  # الحصان ليس ضمن مربطه أصلًا فلن يُعثر عليه


# ---------------------------------------------------------------- حسابات الموظفين
def test_stable_owner_can_create_staff_account(client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/staff/new")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/staff/new", data={
        "name": "خالد الموظف", "email": "khaled.staff2@test.com", "password": "password123",
        "specialty": "مدرب", "csrf_token": csrf,
    }, follow_redirects=True)
    assert "تم إنشاء حساب" in r.get_data(as_text=True)

    r = client.get("/admin/staff")
    assert "خالد الموظف" in r.get_data(as_text=True)
    assert "مدرب" in r.get_data(as_text=True)


def test_staff_account_can_login_and_reach_dashboard(app, client):
    with app.app_context():
        stable = Stable.query.first()
        staff = User(name="موظف تجريبي", email="staff3@test.com", stable_id=stable.id,
                     role=User.ROLE_STAFF, specialty="موظف عام")
        staff.set_password("password123")
        db.session.add(staff)
        db.session.commit()

    r = login(client, "staff3@test.com", "password123")
    assert r.status_code == 200
    r = client.get("/admin")
    assert r.status_code == 200


def test_staff_dashboard_hides_management_actions(app, client):
    with app.app_context():
        stable = Stable.query.first()
        staff = User(name="موظف مقيّد", email="staff4@test.com", stable_id=stable.id,
                     role=User.ROLE_STAFF)
        staff.set_password("password123")
        db.session.add(staff)
        db.session.commit()

    login(client, "staff4@test.com", "password123")
    r = client.get("/admin")
    html = r.get_data(as_text=True)
    assert "مالكو الخيول" not in html
    assert "حجوزات بانتظار التأكيد" not in html


def test_staff_cannot_create_horses_or_owners_or_other_staff(app, client):
    with app.app_context():
        stable = Stable.query.first()
        staff = User(name="موظف مقيّد2", email="staff5@test.com", stable_id=stable.id,
                     role=User.ROLE_STAFF)
        staff.set_password("password123")
        db.session.add(staff)
        db.session.commit()

    login(client, "staff5@test.com", "password123")
    assert client.get("/admin/horse/new").status_code == 403
    assert client.get("/admin/owners/new").status_code == 403
    assert client.get("/admin/staff/new").status_code == 403
    assert client.get("/admin/staff").status_code == 403


def test_staff_can_view_and_update_tasks_and_get_assigned(app, client):
    with app.app_context():
        stable = Stable.query.first()
        horse = Horse.query.first()
        staff = User(name="موظف منفّذ", email="staff6@test.com", stable_id=stable.id,
                     role=User.ROLE_STAFF, specialty="مدرب")
        staff.set_password("password123")
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
        hid = horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/tasks")
    assert "موظف منفّذ" in r.get_data(as_text=True)  # يظهر في قائمة الموظفين المنسدلة
    csrf = get_csrf(r.get_data(as_text=True))
    with app.app_context():
        task = DailyTask.query.filter_by(horse_id=hid, task_type="grooming").first()
        tid = task.id
    client.post(f"/admin/tasks/{tid}/update", data={
        "status": "done", "assigned_to_id": str(staff_id), "csrf_token": csrf
    })
    logout(client)

    login(client, "staff6@test.com", "password123")
    r = client.get(f"/admin/horse/{hid}/tasks")
    assert r.status_code == 200
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/admin/tasks/{tid}/update", data={
        "status": "skipped", "notes": "تم تأجيلها", "csrf_token": csrf
    }, follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        t = db.session.get(DailyTask, tid)
        assert t.status == DailyTask.STATUS_SKIPPED
        assert t.assignee_name == "موظف منفّذ"


# ---------------------------------------------------------------- مدينة وإحداثيات المربط
def test_stable_owner_can_update_city_and_coordinates(app, client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/stable/edit")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/stable/edit", data={
        "stable_name": "مربط الاختبار", "description": "وصف تجريبي",
        "city": "الرياض", "location": "حي الملقا",
        "latitude": "24.7136", "longitude": "46.6753", "csrf_token": csrf,
    }, follow_redirects=True)
    assert "تم تحديث بيانات المربط" in r.get_data(as_text=True)

    with app.app_context():
        stable = Stable.query.first()
        assert stable.city == "الرياض"
        assert stable.latitude == 24.7136
        assert stable.longitude == 46.6753


def test_invalid_city_and_coordinates_are_ignored_not_crashed(app, client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/stable/edit")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/stable/edit", data={
        "stable_name": "مربط الاختبار", "city": "مدينة وهمية غير موجودة",
        "latitude": "not-a-number", "longitude": "also-not-a-number", "csrf_token": csrf,
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        stable = Stable.query.first()
        assert stable.city is None
        assert stable.latitude is None


def test_platform_home_filters_stables_by_city(app, client):
    with app.app_context():
        s1 = Stable.query.first()
        s1.city = "الرياض"
        s1_name = s1.name_ar
        s2 = Stable(name_ar="مربط جدة", slug="jeddah-stable", city="جدة")
        db.session.add(s2)
        db.session.commit()

    r = client.get("/?city=الرياض")
    html = r.get_data(as_text=True)
    assert s1_name in html
    assert "مربط جدة" not in html

    r = client.get("/?city=جدة")
    html = r.get_data(as_text=True)
    assert "مربط جدة" in html
    assert s1_name not in html


def test_map_link_shown_only_when_coordinates_set(app, client):
    with app.app_context():
        stable = Stable.query.first()
        slug = stable.slug

    r = client.get(f"/s/{slug}")
    assert "google.com/maps" not in r.get_data(as_text=True)

    login(client, "owner@test.com", "password123")
    r = client.get("/admin/stable/edit")
    csrf = get_csrf(r.get_data(as_text=True))
    client.post("/admin/stable/edit", data={
        "stable_name": "مربط الاختبار", "latitude": "24.7136", "longitude": "46.6753",
        "csrf_token": csrf,
    })
    logout(client)

    r = client.get(f"/s/{slug}")
    assert "google.com/maps" in r.get_data(as_text=True)


# ---------------------------------------------------------------- معرض صور المربط (Gallery)
def test_stable_owner_can_upload_and_view_gallery_photo(app, client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/gallery", data={
        "photo": make_test_image(), "caption": "ساحة التدريب", "csrf_token": csrf,
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "تمت إضافة الصورة" in r.get_data(as_text=True)

    with app.app_context():
        photo = GalleryPhoto.query.first()
        assert photo is not None
        assert photo.caption == "ساحة التدريب"
        slug = Stable.query.first().slug

    r = client.get(f"/s/{slug}/gallery")
    assert "ساحة التدريب" in r.get_data(as_text=True)


def test_gallery_rejects_non_image_upload(app, client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    fake_file = (io.BytesIO(b"not a real image"), "evil.png")
    r = client.post("/admin/gallery", data={
        "photo": fake_file, "csrf_token": csrf,
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "صورة صالحة" in r.get_data(as_text=True)
    with app.app_context():
        assert GalleryPhoto.query.count() == 0


def test_uploaded_photo_strips_exif_metadata(app, client):
    """يتحقق من أن save_photo يعيد ترميز الصورة فعليًا ويحذف بيانات EXIF (قد تحتوي إحداثيات GPS)
    بدل حفظ الملف الخام كما هو."""
    from PIL import Image as PILImage
    buf = io.BytesIO()
    img = PILImage.new("RGB", (60, 60), color="red")
    exif = img.getexif()
    exif[0x0110] = "Test Camera Model"  # tag شائع (Model) — يكفي للتحقق من الحذف
    img.save(buf, format="JPEG", exif=exif)
    buf.seek(0)

    login(client, "owner@test.com", "password123")
    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/gallery", data={
        "photo": (buf, "with_exif.jpg"), "caption": "فحص EXIF", "csrf_token": csrf,
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "تمت إضافة الصورة" in r.get_data(as_text=True)

    with app.app_context():
        photo = GalleryPhoto.query.filter_by(caption="فحص EXIF").first()
        assert photo is not None
        # المسار المخزَّن نسبي لمجلد static/
        full_path = os.path.join(app.root_path, "static", photo.photo_path)
        assert os.path.exists(full_path)
        reopened = PILImage.open(full_path)
        assert dict(reopened.getexif()) == {}  # لا توجد أي بيانات EXIF متبقية بعد إعادة الترميز
        assert full_path.endswith(".jpg")  # الامتداد طابق الصيغة الفعلية المكتشفة (JPEG)


def test_gallery_enforces_max_photo_limit(app, client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "MAX_GALLERY_PHOTOS", 1)

    login(client, "owner@test.com", "password123")
    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    client.post("/admin/gallery", data={"photo": make_test_image(), "csrf_token": csrf},
                content_type="multipart/form-data")

    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/admin/gallery", data={"photo": make_test_image("second.png"), "csrf_token": csrf},
                     content_type="multipart/form-data", follow_redirects=True)
    assert "الحد الأقصى" in r.get_data(as_text=True)
    with app.app_context():
        assert GalleryPhoto.query.count() == 1


def test_stable_owner_cannot_delete_another_stables_gallery_photo(app, client):
    with app.app_context():
        stable = Stable.query.first()
        other_stable = Stable(name_ar="مربط آخر للمعرض", slug="gallery-other-stable")
        db.session.add(other_stable)
        db.session.commit()
        owner2 = User(name="مالك آخر", email="galleryowner2@test.com", stable_id=other_stable.id,
                     role=User.ROLE_STABLE_OWNER)
        owner2.set_password("password123")
        db.session.add(owner2)
        photo = GalleryPhoto(stable_id=stable.id, photo_path="uploads/gallery/1/fake.png",
                             uploaded_by=User.query.filter_by(email="owner@test.com").first().id)
        db.session.add(photo)
        db.session.commit()
        photo_id = photo.id

    login(client, "galleryowner2@test.com", "password123")
    r = client.get("/admin/gallery")
    assert "لا توجد صور بعد" in r.get_data(as_text=True)
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/admin/gallery/{photo_id}/delete", data={"csrf_token": csrf})
    assert r.status_code == 404

    with app.app_context():
        assert db.session.get(GalleryPhoto, photo_id) is not None


def test_stable_owner_can_delete_own_gallery_photo(app, client):
    login(client, "owner@test.com", "password123")
    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    client.post("/admin/gallery", data={"photo": make_test_image(), "csrf_token": csrf},
                content_type="multipart/form-data")

    with app.app_context():
        photo_id = GalleryPhoto.query.first().id

    r = client.get("/admin/gallery")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/admin/gallery/{photo_id}/delete", data={"csrf_token": csrf}, follow_redirects=True)
    assert "تم حذف الصورة" in r.get_data(as_text=True)
    with app.app_context():
        assert GalleryPhoto.query.count() == 0


def test_visitor_cannot_manage_gallery(client):
    login(client, "visitor@test.com", "password123")
    assert client.get("/admin/gallery").status_code == 403


# ---------------------------------------------------------------- تصدير PDF (التقرير اليومي والفاتورة)
def test_stable_owner_can_download_daily_report_pdf(app, client):
    with app.app_context():
        horse = Horse.query.first()
        hid = horse.id

    login(client, "owner@test.com", "password123")
    client.get(f"/admin/horse/{hid}/tasks")  # يضمن وجود المهام أولًا
    r = client.get(f"/admin/horse/{hid}/tasks/report")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.data[:4] == b"%PDF"
    assert len(r.data) > 1000


def test_staff_can_download_daily_report_pdf(app, client):
    with app.app_context():
        stable = Stable.query.first()
        horse = Horse.query.first()
        staff = User(name="موظف التقارير", email="reportstaff@test.com", stable_id=stable.id,
                     role=User.ROLE_STAFF)
        staff.set_password("password123")
        db.session.add(staff)
        db.session.commit()
        hid = horse.id

    login(client, "reportstaff@test.com", "password123")
    client.get(f"/admin/horse/{hid}/tasks")
    r = client.get(f"/admin/horse/{hid}/tasks/report")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_horse_owner_cannot_access_report_export_route(app, client):
    with app.app_context():
        stable = Stable.query.first()
        horse = Horse.query.first()
        owner2 = User(name="مالك حصان", email="reportowner2@test.com", stable_id=stable.id,
                      role=User.ROLE_HORSE_OWNER)
        owner2.set_password("password123")
        db.session.add(owner2)
        db.session.commit()
        hid = horse.id

    login(client, "reportowner2@test.com", "password123")
    assert client.get(f"/admin/horse/{hid}/tasks/report").status_code == 403


def test_report_export_blocked_for_horse_of_another_stable(app, client):
    with app.app_context():
        other_stable = Stable(name_ar="مربط آخر للتقارير", slug="report-other-stable")
        db.session.add(other_stable)
        db.session.commit()
        other_horse = Horse(stable_id=other_stable.id, name="حصان مربط آخر")
        db.session.add(other_horse)
        db.session.commit()
        other_horse_id = other_horse.id

    login(client, "owner@test.com", "password123")
    r = client.get(f"/admin/horse/{other_horse_id}/tasks/report")
    assert r.status_code == 404


def _create_paid_booking(app):
    with app.app_context():
        stable = Stable.query.first()
        pkg = Package.query.first()
        visitor = User.query.filter_by(email="visitor@test.com").first()
        booking = Booking(stable_id=stable.id, visitor_id=visitor.id, package_id=pkg.id,
                          amount=pkg.price, sessions_remaining=1,
                          status=Booking.STATUS_CONFIRMED, payment_status="paid")
        db.session.add(booking)
        db.session.commit()
        return booking.id


def test_visitor_can_download_own_invoice(app, client):
    bid = _create_paid_booking(app)
    login(client, "visitor@test.com", "password123")
    r = client.get(f"/booking/{bid}/invoice")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.data[:4] == b"%PDF"


def test_stable_owner_can_download_any_of_their_bookings_invoice(app, client):
    bid = _create_paid_booking(app)
    login(client, "owner@test.com", "password123")
    r = client.get(f"/booking/{bid}/invoice")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_unrelated_visitor_cannot_download_someone_elses_invoice(app, client):
    bid = _create_paid_booking(app)
    with app.app_context():
        stable = Stable.query.first()
        stranger = User(name="غريب", email="strangerinvoice@test.com", stable_id=stable.id,
                        role=User.ROLE_VISITOR)
        stranger.set_password("password123")
        db.session.add(stranger)
        db.session.commit()

    login(client, "strangerinvoice@test.com", "password123")
    r = client.get(f"/booking/{bid}/invoice")
    assert r.status_code == 403


def test_anonymous_cannot_download_invoice(app, client):
    bid = _create_paid_booking(app)
    r = client.get(f"/booking/{bid}/invoice")
    assert r.status_code == 403


def test_booking_success_page_shows_invoice_button(app, client):
    with app.app_context():
        pkg = Package.query.first()
        pid = pkg.id

    login(client, "visitor@test.com", "password123")
    r = client.get("/s/test-stable/book")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/book", data={"package_id": pid, "csrf_token": csrf}, follow_redirects=False)
    checkout_url = r.headers["Location"]
    r = client.get(checkout_url)
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(checkout_url, data={"csrf_token": csrf}, follow_redirects=True)
    assert "طباعة الفاتورة" in r.get_data(as_text=True)


# ---------------------------------------------------------------- إصلاحات أمنية (مراجعة الكود)
def _create_book_and_go_to_checkout(client, pkg_id):
    r = client.get("/s/test-stable/book")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/s/test-stable/book", data={"package_id": pkg_id, "csrf_token": csrf},
                     follow_redirects=False)
    return r.headers["Location"]


def test_checkout_disabled_returns_503_when_simulation_off(app, client, monkeypatch):
    with app.app_context():
        pkg = Package.query.first()
        pid = pkg.id

    login(client, "visitor@test.com", "password123")
    checkout_url = _create_book_and_go_to_checkout(client, pid)
    r = client.get(checkout_url)
    csrf = get_csrf(r.get_data(as_text=True))

    monkeypatch.setitem(app.config, "SIMULATED_PAYMENTS_ENABLED", False)
    r = client.get(checkout_url)
    assert r.status_code == 503
    r = client.post(checkout_url, data={"csrf_token": csrf})
    assert r.status_code == 503


def test_checkout_still_works_when_simulation_enabled_by_default(app, client):
    with app.app_context():
        pkg = Package.query.first()
        pid = pkg.id

    login(client, "visitor@test.com", "password123")
    checkout_url = _create_book_and_go_to_checkout(client, pid)
    r = client.get(checkout_url)
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(checkout_url, data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 302  # ينجح ويحوّل لصفحة النجاح كالمعتاد


def test_cannot_submit_review_twice_for_same_booking(app, client):
    bid = _create_paid_booking(app)
    login(client, "visitor@test.com", "password123")
    r = client.get(f"/review/{bid}")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/review/{bid}", data={"horse_rating": 5, "experience_rating": 5, "csrf_token": csrf},
                     follow_redirects=True)
    assert "شكرًا لتقييمك" in r.get_data(as_text=True)

    r = client.get(f"/review/{bid}", follow_redirects=True)
    assert "سبق أن أرسلت تقييمًا" in r.get_data(as_text=True)

    with app.app_context():
        assert Review.query.filter_by(booking_id=bid).count() == 1


def test_invalid_rating_rejected_not_defaulted_to_five(app, client):
    bid = _create_paid_booking(app)
    login(client, "visitor@test.com", "password123")
    r = client.get(f"/review/{bid}")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post(f"/review/{bid}", data={
        "horse_rating": "not-a-number", "experience_rating": "5", "csrf_token": csrf
    }, follow_redirects=True)
    assert "تقييم صحيح" in r.get_data(as_text=True)
    with app.app_context():
        assert Review.query.filter_by(booking_id=bid).count() == 0


def test_review_requires_paid_booking(app, client):
    with app.app_context():
        stable = Stable.query.first()
        pkg = Package.query.first()
        visitor = User.query.filter_by(email="visitor@test.com").first()
        booking = Booking(stable_id=stable.id, visitor_id=visitor.id, package_id=pkg.id,
                          amount=pkg.price, sessions_remaining=1,
                          status=Booking.STATUS_PENDING, payment_status="unpaid")
        db.session.add(booking)
        db.session.commit()
        bid = booking.id

    login(client, "visitor@test.com", "password123")
    r = client.get(f"/review/{bid}", follow_redirects=True)
    assert "لم يُدفع بعد" in r.get_data(as_text=True)


def test_logout_requires_post(client):
    login(client, "owner@test.com", "password123")
    assert client.get("/logout").status_code == 405


def test_logout_via_post_works_and_requires_csrf(client):
    login(client, "owner@test.com", "password123")
    r = client.post("/logout", data={})  # بدون رمز CSRF
    assert r.status_code == 400

    r = client.get("/login")
    csrf = get_csrf(r.get_data(as_text=True))
    r = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=True)
    assert "تم تسجيل الخروج" in r.get_data(as_text=True)
