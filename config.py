# -*- coding: utf-8 -*-
"""
إعدادات التطبيق — تُقرأ من متغيرات البيئة (Environment Variables).
لا تضع أي قيم سرّية (مفاتيح، كلمات مرور) مباشرة في الكود؛ استخدم ملف .env محليًا
(انظر .env.example) أو لوحة تحكم منصة الاستضافة في الإنتاج.
"""
import os


def _normalize_db_url(url: str) -> str:
    """يحوّل postgres:// (صيغة قديمة يستخدمها Render/Heroku) إلى postgresql://
    التي يتطلبها SQLAlchemy 1.4+."""
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    # مفتاح الجلسة — إلزامي في الإنتاج، وإلا يرفض التطبيق العمل (انظر app.py)
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # قاعدة البيانات: PostgreSQL في الإنتاج عبر DATABASE_URL، وSQLite محليًا كافتراضي للتطوير السريع
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(os.environ.get("DATABASE_URL")) or (
        "sqlite:///" + os.path.join(os.path.abspath(os.path.dirname(__file__)), "mirbat.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping ضروري مع قواعد بيانات سحابية مُدارة (تقطع الاتصالات الخاملة)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    # الرفع
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8MB لكل طلب رفع
    UPLOAD_SUBDIR = "uploads"
    ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}

    # الجلسات وملفات تعريف الارتباط
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 ساعات

    # بيئة التشغيل
    ENV = os.environ.get("FLASK_ENV", "production")
    DEBUG = ENV == "development"
    # يُفعَّل تلقائيًا خلف HTTPS في الإنتاج (Render/أي منصة توفر SSL تلقائيًا)
    SESSION_COOKIE_SECURE = ENV != "development"

    # تحديد معدل الطلبات (حماية من هجمات تخمين كلمات المرور)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # بوابة الدفع لا تزال محاكاة (لا بوابة حقيقية مربوطة بعد) — هذا مفتاح تحكم صريح
    # حتى لا يُترك الدفع الوهمي يؤكد حجوزات حقيقية بالخطأ بعد ربط Moyasar/HyperPay.
    # اضبط SIMULATED_PAYMENTS_ENABLED=false بمجرد ربط بوابة دفع حقيقية.
    SIMULATED_PAYMENTS_ENABLED = os.environ.get("SIMULATED_PAYMENTS_ENABLED", "true").lower() == "true"
