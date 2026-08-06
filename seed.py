# -*- coding: utf-8 -*-
"""
بيانات تجريبية: مربط العز
- 3 ملّاك خيول خارجيين (إيواء): 3 + 3 + 4 = 10 خيول
- باقات أسعار مدروسة على السوق السعودي (راجع أسعار نوادي الرياض 2026)
- حساب مالك إسطبل تجريبي وحساب زائر تجريبي

تحذير: هذا السكربت يحذف كل بيانات هذه الجداول قبل إعادة تعبئتها — للتطوير المحلي فقط،
ولا يعمل إطلاقًا إلا إذا كان FLASK_ENV=development (يرفض العمل تلقائيًا في الإنتاج).
"""
import os
import sys

from dotenv import load_dotenv
load_dotenv()

if os.environ.get("FLASK_ENV") != "development":
    sys.exit(
        "رُفض التشغيل: seed.py يحذف بيانات حقيقية ويُستخدم للتطوير المحلي فقط.\n"
        "لتشغيله، اضبط أولًا: FLASK_ENV=development"
    )

from datetime import date, timedelta
from app import app
from models import db, Stable, User, Horse, Package, DailyLog, Booking, Review

with app.app_context():
    # يفترض هذا السكربت أن المخطط (schema) تم إنشاؤه مسبقًا عبر الترحيلات (migrations):
    #   flask db upgrade
    # هنا فقط نُفرغ الجداول ونعيد تعبئتها ببيانات تجريبية — لا نُنشئ أو نحذف الجداول نفسها.
    for model in [Review, Booking, DailyLog, Horse, Package, User, Stable]:
        db.session.query(model).delete()
    db.session.commit()

    stable = Stable(
        name_ar="مربط العز",
        slug="al-ezz",
        location="الرياض",
        description="مربط متخصص في إيواء ورعاية الخيول العربية الأصيلة، وتقديم خدمات التدريب والركوب.",
    )
    db.session.add(stable)
    db.session.commit()

    # مالك الإسطبل
    admin = User(name="سطام الغيداني", email="admin@alezz.sa", phone="0500000001",
                 role=User.ROLE_STABLE_OWNER, stable_id=stable.id)
    admin.set_password("admin123")
    db.session.add(admin)

    # ثلاثة ملّاك خيول خارجيين (خدمة إيواء)
    owners_data = [
        ("خالد المطيري", "khaled@example.com", 3),
        ("فهد العتيبي", "fahad@example.com", 3),
        ("ناصر القحطاني", "nasser@example.com", 4),
    ]
    horse_names = [
        "الأصيل", "غيمة", "الشهم", "لؤلؤة", "الفارس",
        "سهم", "ريحانة", "المجد", "زهرة الصحراء", "العنقاء",
    ]
    breeds = ["عربي أصيل", "عربي أصيل - كحيلان", "عربي أصيل - صقلاوي", "عربي أصيل - عبيّان"]
    colors = ["أشهب", "كميت", "أدهم", "أشقر"]

    name_idx = 0
    for owner_name, owner_email, count in owners_data:
        owner = User(name=owner_name, email=owner_email, phone="0501234567",
                     role=User.ROLE_HORSE_OWNER, stable_id=stable.id)
        owner.set_password("owner123")
        db.session.add(owner)
        db.session.commit()

        for i in range(count):
            horse = Horse(
                stable_id=stable.id,
                owner_id=owner.id,
                name=horse_names[name_idx % len(horse_names)],
                breed=breeds[name_idx % len(breeds)],
                color=colors[name_idx % len(colors)],
                birth_year=2018 + (name_idx % 6),
                service_type="boarding",
            )
            db.session.add(horse)
            db.session.commit()

            # سجلات يومية تجريبية لآخر 3 أيام
            for d in range(3):
                log = DailyLog(
                    horse_id=horse.id,
                    log_date=date.today() - timedelta(days=d),
                    feeding="شعير 3 كغم صباحًا، برسيم 2 كغم مساءً، ماء نظيف متجدد",
                    care="تنظيف المسكن، فرش جديد، تمشيط وتنظيف الحوافر",
                    training="30 دقيقة مشي هادئ في الساحة الرملية" if d != 1 else "راحة",
                    medication="لا يوجد" if d != 0 else "جرعة فيتامينات وقائية",
                    created_by=admin.id,
                )
                db.session.add(log)
            name_idx += 1

    # حصانان مخصصان لحصص التدريب/الركوب العامة (ملك الإسطبل)
    for tname, tbreed in [("سيف المربط", "عربي أصيل - كحيلان"), ("نجم الصحراء", "عربي أصيل - صقلاوي")]:
        th = Horse(stable_id=stable.id, owner_id=None, name=tname, breed=tbreed,
                   color="أشهب", birth_year=2019, service_type="training",
                   notes="حصان هادئ مناسب للمبتدئين، مدرّب على حصص الركوب التعليمية.")
        db.session.add(th)

    db.session.commit()

    # باقات مدروسة على أسعار السوق السعودي (نوادي الرياض، 2026)
    packages = [
        dict(name_ar="حصة تجريبية فردية", kind="riding", session_count=1, price=150,
             duration_label="حصة واحدة (45 دقيقة)",
             description="مناسبة للمبتدئين|إشراف مدرب مرخّص|تشمل معدات الأمان"),
        dict(name_ar="باقة 4 حصص", kind="riding", session_count=4, price=550,
             duration_label="صالحة لمدة شهر",
             description="حصة أسبوعية|تقييم مستوى مبدئي|إشراف مدرب مرخّص"),
        dict(name_ar="باقة 8 حصص", kind="riding", session_count=8, price=1000,
             duration_label="صالحة لمدة شهرين",
             description="حصتان أسبوعيًا|متابعة تطور الأداء|أولوية في الحجز"),
        dict(name_ar="باقة 12 حصة", kind="riding", session_count=12, price=1400,
             duration_label="صالحة لمدة 3 أشهر",
             description="الأنسب للمتقدمين|تدريب على المهارات المتقدمة|شهادة إتمام مستوى"),
        dict(name_ar="باقة قفز الحواجز", kind="jumping", session_count=10, price=1500,
             duration_label="صالحة لمدة شهرين",
             description="لمتدربي المستوى المتوسط فأعلى|معدات قفز متخصصة|مدرب مختص بالقفز"),
        dict(name_ar="اشتراك سنوي كامل", kind="annual", session_count=52, price=5000,
             duration_label="حصة أسبوعية لمدة عام كامل",
             description="أفضل قيمة مقابل السعر|أولوية الحجز والمواعيد|تقييم دوري كل شهر|دعوة لفعاليات المربط"),
    ]
    for p in packages:
        db.session.add(Package(stable_id=stable.id, **p))

    # زائر تجريبي
    visitor = User(name="عبدالله الشمري", email="visitor@example.com", phone="0559876543",
                    role=User.ROLE_VISITOR, stable_id=stable.id)
    visitor.set_password("visitor123")
    db.session.add(visitor)

    db.session.commit()

    print("تم إنشاء البيانات التجريبية بنجاح ✅")
    print("— مالك الإسطبل : admin@alezz.sa / admin123")
    print("— مالك خيل (مثال) : khaled@example.com / owner123")
    print("— زائر تجريبي : visitor@example.com / visitor123")
