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
    # جلسة طويلة الأمد: تسجيل الدخول اليومي username/password فقط، بدون طلب OTP إلا وقت التسجيل
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30  # 30 يومًا

    # مزود إرسال OTP — راجع sms.py. القيمة الافتراضية "console" (تسجيل بدل إرسال فعلي) لا يجوز
    # تركها في الإنتاج؛ يُرفض بدء التطبيق إن كانت DEBUG=False وSMS_PROVIDER لا يزال console (انظر app.py)
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "console").lower()
    OTP_LENGTH = 6
    OTP_TTL_SECONDS = 5 * 60          # صلاحية الكود: 5 دقائق
    OTP_MAX_ATTEMPTS = 5              # حد محاولات إدخال الكود قبل رفضه
    OTP_RESEND_COOLDOWN_SECONDS = 60  # حد أدنى بين كل طلب كود وآخر لنفس الرقم

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
