# -*- coding: utf-8 -*-
import logging
import os
import re
import secrets
import sys
import uuid
from datetime import date, datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                    session, flash, abort, send_from_directory, jsonify, send_file)
from dotenv import load_dotenv
load_dotenv()

from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_

from config import Config
from models import (db, Stable, User, Horse, DailyLog, DailyTask, Package, Booking, Review,
                     Achievement, GalleryPhoto, PhoneOtp, Vaccination, Medication, DailyReport)
from pdf_reports import build_daily_report_pdf, build_invoice_pdf
from sms import send_otp_sms, SmsSendError
from translations import translate

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")

app = Flask(__name__)
app.config.from_object(Config)

# التطبيق يعمل خلف Proxy (Render/Nginx) — ضروري كي يعرف Flask أن الطلب أصلًا HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# رفض بدء التطبيق في الإنتاج بدون SECRET_KEY حقيقي — خطأ شائع وخطير أن يُنسى
if not app.config["DEBUG"] and not app.config["SECRET_KEY"]:
    sys.exit("خطأ إعداد حرج: يجب ضبط متغير البيئة SECRET_KEY قبل تشغيل التطبيق في الإنتاج.")
if app.config["DEBUG"] and not app.config["SECRET_KEY"]:
    app.config["SECRET_KEY"] = "dev-only-not-secure-change-me"

# تنبيه (وليس رفضًا) لو مزود SMS لا يزال "console" بالإنتاج — التسجيل الحالي بريد/كلمة مرور فلا يعتمد
# على SMS فعليًا، لكن هذا يبقى تذكيرًا مفيدًا وقت إعادة تفعيل تدفق OTP بالجوال لاحقًا.
if not app.config["DEBUG"] and app.config["SMS_PROVIDER"] == "console":
    logging.warning("SMS_PROVIDER=console بالإنتاج — مسار OTP بالجوال معطَّل حاليًا فهذا غير مؤثر الآن.")

db.init_app(app)
migrate = Migrate(app, db)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, storage_uri=app.config["RATELIMIT_STORAGE_URI"],
                   default_limits=["200 per hour"])

# ---------------------------------------------------------------- السجلات (Logging)
logging.basicConfig(
    level=logging.INFO if not app.config["DEBUG"] else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
app.logger.setLevel(logging.INFO)


# ---------------------------------------------------------------- أدوات مساعدة للرفع الآمن
def allowed_ext(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_IMAGE_EXT"]


def save_photo(file_storage, subfolder):
    """يحفظ صورة مرفوعة بأمان عبر إعادة ترميزها الكاملة (وليس فقط التحقق منها وحفظ الملف الخام):
    - تتحقق من صحة الصورة فعليًا (وليس فقط الامتداد المُرسَل)
    - تحدد الامتداد الحقيقي من محتوى الصورة نفسه (يمنع تعارض امتداد مزيّف عن المحتوى الفعلي)
    - تُعيد ترميز الصورة من الصفر (تحذف تلقائيًا أي بيانات EXIF أو بيانات دخيلة/مخفية ملحقة بالملف
      الأصلي — لا يُمرَّر exif= عند الحفظ فتُحذف بشكل افتراضي، وهي أيضًا حماية ضد ملفات "polyglot"
      المصمَّمة لتُقرأ كصورة صالحة من جهة وكشيء آخر ضار من جهة أخرى)
    - تحدد أبعادًا قصوى (تصغير تلقائي) لمنع استنزاف الذاكرة/التخزين بصور ضخمة الأبعاد
    - اسم ملف عشوائي آمن لمنع تخمين المسارات أو تعارض الأسماء
    """
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_ext(file_storage.filename):
        flash("صيغة الصورة غير مدعومة (يُسمح فقط بـ JPG, PNG, WEBP)", "error")
        return None

    FORMAT_TO_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
    MAX_DIMENSION = 3000  # بكسل لكل ضلع كحد أقصى — إعادة تحجيم تلقائي بدون تشويه النسبة

    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        img.verify()  # تحقق بنيوي أولي — يُبطل الكائن، لذا يلزم إعادة فتحه بعده
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        img.load()  # يفشل هنا لو كانت بيانات البكسل نفسها تالفة، وليس فقط الترويسة (header)
    except Image.DecompressionBombError:
        flash("أبعاد الصورة كبيرة جدًا وغير مسموحة", "error")
        return None
    except (UnidentifiedImageError, OSError, ValueError):
        flash("الملف المرفوع ليس صورة صالحة", "error")
        return None

    actual_format = img.format
    if actual_format not in FORMAT_TO_EXT:
        flash("صيغة الصورة غير مدعومة (يُسمح فقط بـ JPG, PNG, WEBP)", "error")
        return None
    ext = FORMAT_TO_EXT[actual_format]

    # JPEG لا يدعم قناة الشفافية — تحويل ضروري لتفادي فشل الحفظ أو ألوان مشوَّهة
    if actual_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    folder = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(folder, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.{ext}"
    save_kwargs = {"quality": 88, "optimize": True} if actual_format in ("JPEG", "WEBP") else {"optimize": True}
    # ملاحظة: عدم تمرير exif= هنا مقصود — هذا ما يضمن حذف بيانات EXIF (قد تحتوي إحداثيات GPS
    # أو معلومات الجهاز) من الصورة المحفوظة نهائيًا، بغض النظر عمّا كان بالملف الأصلي.
    img.save(os.path.join(folder, fname), format=actual_format, **save_kwargs)
    return f"uploads/{subfolder}/{fname}"


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


PHONE_RE = re.compile(r"^\+9665\d{8}$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{4,30}$")


def normalize_saudi_phone(raw):
    """يحوّل مدخلات المستخدم المختلفة لرقم جوال سعودي إلى صيغة موحّدة +9665XXXXXXXX،
    أو يرجّع None إن كان الرقم غير صالح."""
    digits = re.sub(r"[^\d]", "", raw or "")
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("966"):
        digits = "+" + digits
    elif digits.startswith("05") and len(digits) == 10:
        digits = "+966" + digits[1:]
    elif digits.startswith("5") and len(digits) == 9:
        digits = "+966" + digits
    else:
        digits = "+" + digits if not digits.startswith("+") else digits
    return digits if PHONE_RE.match(digits) else None


def create_and_send_otp(phone, purpose=PhoneOtp.PURPOSE_REGISTER):
    """ينشئ كود OTP جديد لرقم الجوال ويرسله فعليًا، مع فرض فترة تهدئة بين الطلبات المتتالية
    لنفس الرقم لمنع استنزاف رصيد الرسائل. يرجّع (True, None) عند النجاح أو (False, رسالة الخطأ)."""
    cooldown = app.config["OTP_RESEND_COOLDOWN_SECONDS"]
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=cooldown)
    recent = (PhoneOtp.query.filter_by(phone=phone, purpose=purpose)
              .filter(PhoneOtp.created_at >= cutoff).first())
    if recent:
        return False, "الرجاء الانتظار قليلًا قبل طلب كود جديد"

    code = "".join(secrets.choice("0123456789") for _ in range(app.config["OTP_LENGTH"]))
    otp = PhoneOtp(
        phone=phone,
        purpose=purpose,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                   + timedelta(seconds=app.config["OTP_TTL_SECONDS"]),
        max_attempts=app.config["OTP_MAX_ATTEMPTS"],
    )
    otp.set_code(code)
    db.session.add(otp)
    db.session.commit()

    try:
        send_otp_sms(phone, code)
    except SmsSendError:
        app.logger.exception("فشل إرسال OTP إلى %s", phone)
        return False, "تعذّر إرسال الرسالة النصية حاليًا، حاول لاحقًا"
    return True, None


def verify_otp(phone, code, purpose=PhoneOtp.PURPOSE_REGISTER):
    """يتحقق من كود OTP لرقم معيّن. يرجّع (True, None) عند النجاح، أو (False, رسالة خطأ)."""
    otp = (PhoneOtp.query.filter_by(phone=phone, purpose=purpose, consumed_at=None)
           .order_by(PhoneOtp.created_at.desc()).first())
    if not otp or not otp.is_usable:
        return False, "الكود غير صالح أو منتهي الصلاحية، اطلب كودًا جديدًا"

    if not otp.check_code(code):
        otp.attempts += 1
        db.session.commit()
        return False, "الكود غير صحيح"

    otp.consumed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    return True, None


def normalize_optional_phone(raw):
    """للحقول الاختيارية (تنشئها الإدارة، غير مبنية على تحقق OTP): يحوّل النص الفارغ إلى
    None صراحة (لا يُخزَّن كنص فارغ "" أبدًا) حتى لا يتعارض حقلان فارغان مع قيد UNIQUE على phone.
    إن أُدخل رقم، يحاول توحيد صيغته؛ فإن كان غير صالح يُرجع (None, تحذير) بدل رفض الحفظ بالكامل."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    normalized = normalize_saudi_phone(raw)
    if not normalized:
        return None, "تنبيه: رقم الجوال المُدخل غير صالح فتم تجاهله — يمكن إضافته لاحقًا من صفحة التعديل"
    return normalized, None


def login_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash("الرجاء تسجيل الدخول للمتابعة", "error")
                return redirect(url_for("login"))
            if roles and user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def asset_url(filename):
    """رابط ملف ثابت (CSS/JS) مع رقم إصدار مبني على وقت تعديل الملف نفسه — يضمن أن أي طبقة
    تخزين مؤقت (كاش المتصفح أو CDN الاستضافة) تتجاهل النسخة القديمة تلقائيًا بعد كل نشر جديد،
    بدل الاعتماد على تحكم المستخدم اليدوي بمسح الكاش (Hard Refresh قد لا يكفي مع بعض شبكات CDN)."""
    full_path = os.path.join(app.static_folder, filename)
    try:
        version = str(int(os.path.getmtime(full_path)))
    except OSError:
        version = "1"
    return url_for("static", filename=filename) + f"?v={version}"


@app.context_processor
def inject_globals():
    nav_stable = None
    if request.view_args and "slug" in request.view_args:
        nav_stable = Stable.query.filter_by(slug=request.view_args["slug"]).first()
    lang = session.get("lang", "ar")
    return {
        "current_user": current_user(), "today": date.today(), "nav_stable": nav_stable,
        "lang": lang, "t": lambda key: translate(key, lang), "asset_url": asset_url,
    }


@app.route("/lang/<code>")
def set_language(code):
    """تبديل لغة الواجهة (ar/en) — يُخزَّن الاختيار بالجلسة ويُعاد توجيه المستخدم لنفس الصفحة."""
    if code in ("ar", "en"):
        session["lang"] = code
        session.permanent = True
    return redirect(request.referrer or url_for("home"))


# ---------------------------------------------------------------- ترويسات أمنية على كل استجابة
CSP_POLICY = (
    "default-src 'self'; "
    "img-src 'self'; "
    "font-src 'self' https://fonts.gstatic.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "  # 'unsafe-inline' ضروري لأن القوالب تستخدم style="" مباشرة على العناصر
    "script-src 'self'; "  # لا يوجد أي <script> مضمّن أو خارجي بالمشروع حاليًا
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = CSP_POLICY
    if not app.config["DEBUG"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename, max_age=86400)


@app.route("/healthz")
def healthz():
    """فحص صحة بسيط تستخدمه منصة الاستضافة للتأكد أن التطبيق وقاعدة البيانات يعملان."""
    try:
        db.session.execute(db.select(1))
        return jsonify(status="ok"), 200
    except Exception:
        app.logger.exception("فشل فحص الصحة")
        return jsonify(status="error"), 503


# ---------------------------------------------------------------- معالجات الأخطاء
@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(413)
def too_large(e):
    flash("حجم الملف كبير جدًا (الحد الأقصى 8 ميغابايت)", "error")
    return redirect(request.referrer or url_for("home")), 413


@app.errorhandler(500)
def server_error(e):
    app.logger.exception("خطأ داخلي غير متوقع")
    return render_template("errors/500.html"), 500


# ---------------------------------------------------------------- الصفحة الرئيسية للمنصة
@app.route("/")
def home():
    city_filter = request.args.get("city", "").strip()
    query = Stable.query.filter_by(status=Stable.STATUS_APPROVED)
    if city_filter and city_filter in Stable.CITIES:
        query = query.filter_by(city=city_filter)
    stables = query.order_by(Stable.name_ar).all()

    cities_in_use = sorted({s.city for s in Stable.query.filter(
        Stable.city.isnot(None), Stable.status == Stable.STATUS_APPROVED).all()})
    return render_template("platform_home.html", stables=stables,
                            cities_in_use=cities_in_use, selected_city=city_filter)


def _stable_publicly_visible_or_404(stable):
    """يمنع الوصول العام لمربط بانتظار المراجعة أو مرفوض — يبقى مرئيًا فقط لمالكه/موظفيه/مشرف المنصة."""
    if stable.status == Stable.STATUS_APPROVED:
        return
    user = current_user()
    if user and (user.role == User.ROLE_SUPER_ADMIN or user.stable_id == stable.id):
        return
    abort(404)


# ---------------------------------------------------------------- الصفحة الرئيسية لمربط محدد
@app.route("/s/<slug>")
def stable_home(slug):
    stable = Stable.query.filter_by(slug=slug).first_or_404()
    _stable_publicly_visible_or_404(stable)
    packages = Package.query.filter_by(stable_id=stable.id, is_active=True).all()
    featured_horses = Horse.query.filter_by(stable_id=stable.id, is_public=True).limit(4).all()
    gallery_preview = GalleryPhoto.query.filter_by(stable_id=stable.id).order_by(
        GalleryPhoto.created_at.desc()).limit(4).all()
    return render_template("home.html", stable=stable, packages=packages, horses=featured_horses,
                            gallery_preview=gallery_preview)


# ---------------------------------------------------------------- تسجيل الدخول / الخروج
def _redirect_after_login(user):
    if user.role == User.ROLE_SUPER_ADMIN:
        return redirect(url_for("platform_dashboard"))
    elif user.role == User.ROLE_STABLE_OWNER or user.role == User.ROLE_STAFF:
        return redirect(url_for("admin_dashboard"))
    elif user.role == User.ROLE_HORSE_OWNER:
        return redirect(url_for("owner_dashboard"))
    elif user.stable_id:
        stable = db.session.get(Stable, user.stable_id)
        return redirect(url_for("stable_home", slug=stable.slug) if stable else url_for("home"))
    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    """تسجيل الدخول اليومي: اسم مستخدم + كلمة مرور فقط، بدون أي رسالة SMS.
    الجلسة تدوم 30 يومًا (PERMANENT_SESSION_LIFETIME) — التحقق عبر OTP يحدث مرة واحدة فقط وقت التسجيل."""
    if request.method == "POST":
        identifier = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        # يقبل اسم المستخدم (حسابات مسجَّلة برقم الجوال) أو البريد الإلكتروني (حسابات
        # مالك/موظف/مشرف أُنشئت من لوحة الإدارة أو seed-admin ولا تملك اسم مستخدم بعد)
        user = User.query.filter(or_(User.username == identifier, User.email == identifier)).first()
        # رسالة خطأ موحّدة سواء كان الحساب غير موجود أو كلمة المرور خاطئة — لمنع تخمين الحسابات
        if user and user.check_password(password):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            flash(f"أهلًا بك، {user.name}", "success")
            return _redirect_after_login(user)
        flash("اسم المستخدم أو كلمة المرور غير صحيحة", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("تم تسجيل الخروج بنجاح", "success")
    return redirect(url_for("home"))


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/register-stable", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register_stable():
    """تسجيل ذاتي عام لمالك مربط جديد — ينشئ المربط وحسابه بحالة "بانتظار الموافقة"، ولا يظهران
    للعموم إطلاقًا (لا بالقائمة العامة ولا بالرابط المباشر) حتى يعتمدهما مشرف المنصة."""
    if request.method == "POST":
        import re as _re

        stable_name = request.form.get("stable_name", "").strip()
        slug = request.form.get("slug", "").strip().lower()
        owner_name = request.form.get("owner_name", "").strip()
        owner_email = request.form.get("owner_email", "").strip().lower()
        owner_password = request.form.get("owner_password", "")
        terms_accepted = request.form.get("terms_accepted") == "on"

        if not all([stable_name, slug, owner_name, owner_email, owner_password]):
            flash("الرجاء تعبئة جميع الحقول المطلوبة", "error")
            return render_template("register_stable.html")
        if not terms_accepted:
            flash("الرجاء الموافقة على الشروط والأحكام للمتابعة", "error")
            return render_template("register_stable.html")
        if len(owner_password) < 8:
            flash("يجب ألا تقل كلمة المرور عن 8 أحرف", "error")
            return render_template("register_stable.html")
        if "@" not in owner_email or "." not in owner_email.split("@")[-1]:
            flash("الرجاء إدخال بريد إلكتروني صحيح", "error")
            return render_template("register_stable.html")
        if not _re.fullmatch(r"[a-z0-9-]+", slug):
            flash("الرابط المختصر يجب أن يحتوي أحرفًا إنجليزية صغيرة وأرقامًا وشرطات (-) فقط", "error")
            return render_template("register_stable.html")
        if Stable.query.filter_by(slug=slug).first():
            flash("هذا الرابط المختصر مستخدم لمربط آخر بالفعل، اختر رابطًا مختلفًا", "error")
            return render_template("register_stable.html")
        if User.query.filter_by(email=owner_email).first():
            flash("هذا البريد الإلكتروني مسجّل مسبقًا لحساب آخر", "error")
            return render_template("register_stable.html")

        stable = Stable(name_ar=stable_name, slug=slug, status=Stable.STATUS_PENDING)
        db.session.add(stable)
        db.session.commit()

        owner = User(name=owner_name, email=owner_email, stable_id=stable.id,
                     role=User.ROLE_STABLE_OWNER,
                     terms_accepted_at=datetime.now(timezone.utc).replace(tzinfo=None))
        owner.set_password(owner_password)
        db.session.add(owner)
        db.session.commit()
        app.logger.info("stable_self_registered id=%s slug=%s owner_id=%s", stable.id, stable.slug, owner.id)

        session.clear()
        session["user_id"] = owner.id
        session.permanent = True
        flash("تم إرسال طلبك بنجاح — بانتظار موافقة مشرف المنصة قبل تفعيل مربطك", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("register_stable.html")


# ---------------------------------------------------------------- التسجيل: بريد إلكتروني + كلمة مرور
# ملاحظة: التسجيل عبر OTP بالجوال مُعطَّل مؤقتًا (يحتاج مزود SMS مربوط بسجل تجاري مطابق للنشاط).
# البنية التحتية (PhoneOtp، sms.py، normalize_saudi_phone) باقية بالكود جاهزة لإعادة التفعيل لاحقًا
# بدون إعادة بناء — فقط يُعاد ربط هذا المسار بخطوتَي stable_register_verify عند توفر مزود SMS حقيقي.
@app.route("/s/<slug>/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def stable_register(slug):
    """تسجيل زائر جديد يريد حجز حصص ركوب/تدريب في مربط محدد."""
    stable = Stable.query.filter_by(slug=slug).first_or_404()
    _stable_publicly_visible_or_404(stable)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        raw_phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        terms_accepted = request.form.get("terms_accepted") == "on"

        if not all([name, email, raw_phone, password]):
            flash("الرجاء تعبئة جميع الحقول المطلوبة", "error")
            return render_template("register.html", stable=stable)
        if not terms_accepted:
            flash("الرجاء الموافقة على الشروط والأحكام للمتابعة", "error")
            return render_template("register.html", stable=stable)
        phone = normalize_saudi_phone(raw_phone)
        if not phone:
            flash("رقم الجوال غير صحيح — تأكد من كتابته بصيغة سعودية صالحة (05XXXXXXXX)", "error")
            return render_template("register.html", stable=stable)
        if len(password) < 8:
            flash("يجب ألا تقل كلمة المرور عن 8 أحرف", "error")
            return render_template("register.html", stable=stable)
        if "@" not in email or "." not in email.split("@")[-1]:
            flash("الرجاء إدخال بريد إلكتروني صحيح", "error")
            return render_template("register.html", stable=stable)
        if User.query.filter_by(email=email).first():
            flash("هذا البريد الإلكتروني مسجّل مسبقًا", "error")
            return render_template("register.html", stable=stable)
        if User.query.filter_by(phone=phone).first():
            flash("رقم الجوال هذا مسجّل مسبقًا لحساب آخر", "error")
            return render_template("register.html", stable=stable)

        user = User(name=name, email=email, phone=phone,
                    role=User.ROLE_VISITOR, stable_id=stable.id,
                    terms_accepted_at=datetime.now(timezone.utc).replace(tzinfo=None))
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session.clear()
        session["user_id"] = user.id
        session.permanent = True
        flash("تم إنشاء حسابك بنجاح، يمكنك الآن حجز حصتك", "success")
        return redirect(url_for("stable_book", slug=stable.slug))

    return render_template("register.html", stable=stable)


# ---------------------------------------------------------------- لوحة مالك الإسطبل
@app.route("/admin")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_dashboard():
    user = current_user()
    stable = db.session.get(Stable, user.stable_id) if user.stable_id else None
    if user.role != User.ROLE_SUPER_ADMIN and stable and stable.status != Stable.STATUS_APPROVED:
        return render_template("admin_pending_approval.html", stable=stable)

    horses = Horse.query.filter_by(stable_id=user.stable_id).order_by(Horse.name).all()
    pending_bookings = (Booking.query.filter_by(stable_id=user.stable_id, status="pending")
                         .order_by(Booking.created_at.desc()).limit(50).all())

    today = date.today()
    horse_ids = [h.id for h in horses]
    reports_today = DailyReport.query.filter(
        DailyReport.horse_id.in_(horse_ids), DailyReport.report_date == today
    ).all() if horse_ids else []
    reported_horse_ids = {r.horse_id for r in reports_today}
    reports_done = len(reported_horse_ids)
    reports_total = len(horses)
    completion_pct = round(reports_done / reports_total * 100) if reports_total else 0
    abnormal_today = sum(1 for r in reports_today if r.has_abnormal)

    return render_template("admin_dashboard.html", horses=horses, bookings=pending_bookings,
                            done_tasks=reports_done, total_tasks=reports_total, completion_pct=completion_pct,
                            horses_count=len(horses), pending_bookings_count=len(pending_bookings),
                            abnormal_today=abnormal_today)


@app.route("/admin/stable/edit", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_stable_edit():
    user = current_user()
    stable = db.get_or_404(Stable, user.stable_id)

    if request.method == "POST":
        name = request.form.get("stable_name", "").strip()
        if not name:
            flash("اسم المربط مطلوب", "error")
            return render_template("admin_stable_edit.html", stable=stable, cities=Stable.CITIES)

        stable.name_ar = name
        stable.description = request.form.get("description", "").strip()[:2000]
        city, location, latitude, longitude = parse_stable_location_fields(request.form)
        stable.city = city
        stable.location = location
        stable.latitude = latitude
        stable.longitude = longitude
        db.session.commit()
        flash("تم تحديث بيانات المربط", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_stable_edit.html", stable=stable, cities=Stable.CITIES)


def get_or_create_tasks_for_day(horse, day):
    """يضمن وجود صف مهمة لكل نوع من الأنواع الاثنتي عشرة الثابتة لهذا الحصان في هذا اليوم،
    وينشئ الناقص منها تلقائيًا (بحالة 'بانتظار التنفيذ'). يُرجع القائمة مرتبة بترتيب اليوم الثابت."""
    existing = {t.task_type: t for t in DailyTask.query.filter_by(horse_id=horse.id, task_date=day).all()}
    created = False
    for task_type, _ in DailyTask.TASK_TYPES:
        if task_type not in existing:
            t = DailyTask(horse_id=horse.id, task_date=day, task_type=task_type)
            db.session.add(t)
            existing[task_type] = t
            created = True
    if created:
        db.session.commit()
    return [existing[task_type] for task_type, _ in DailyTask.TASK_TYPES]


def submit_achievement(horse, user):
    """يتحقق من مدخلات نموذج الإنجاز وينشئه بحالة 'بانتظار المراجعة'.
    يُرجع (achievement_or_None, error_message_or_None)."""
    title = request.form.get("title", "").strip()
    if not title:
        return None, "عنوان الإنجاز مطلوب"

    competition_name = request.form.get("competition_name", "").strip()[:200]
    description = request.form.get("description", "").strip()[:2000]

    date_raw = request.form.get("achievement_date")
    achievement_date = None
    if date_raw:
        try:
            achievement_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            achievement_date = None

    photo_path = save_photo(request.files.get("photo"), f"achievements/{horse.id}")

    achievement = Achievement(
        horse_id=horse.id,
        stable_id=horse.stable_id,
        submitted_by=user.id,
        title=title[:200],
        competition_name=competition_name,
        achievement_date=achievement_date,
        description=description,
        photo_path=photo_path,
        status=Achievement.STATUS_PENDING,
    )
    db.session.add(achievement)
    db.session.commit()
    app.logger.info("achievement_submitted id=%s horse_id=%s by user_id=%s",
                     achievement.id, horse.id, user.id)
    return achievement, None


@app.route("/admin/horse/<int:horse_id>")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_horse_detail(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()
    return render_template("admin_horse_detail.html", horse=horse)


@app.route("/admin/horse/<int:horse_id>/toggle-visibility", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_horse_toggle_visibility(horse_id):
    """يتحكم بها مالك الإسطبل فقط (وليس الموظفين) — إظهار/إخفاء حصان عن الزوار والأعضاء."""
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()
    horse.is_public = not horse.is_public
    db.session.commit()
    flash(f"الحصان {horse.name} الآن {'ظاهر للزوار' if horse.is_public else 'مخفي عن الزوار'}", "success")
    return redirect(url_for("admin_horse_detail", horse_id=horse.id))


@app.route("/admin/horse/<int:horse_id>/achievements/new", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_achievement_new(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    if request.method == "POST":
        achievement, error = submit_achievement(horse, user)
        if error:
            flash(error, "error")
            return render_template("achievement_new.html", horse=horse, back_url=url_for("admin_horse_detail", horse_id=horse.id))
        flash("تم إرسال الإنجاز — بانتظار موافقة مشرف المنصة قبل ظهوره في منصة الفخر", "success")
        return redirect(url_for("admin_horse_detail", horse_id=horse.id))

    return render_template("achievement_new.html", horse=horse, back_url=url_for("admin_horse_detail", horse_id=horse.id))


@app.route("/admin/horse/<int:horse_id>/daily-report", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_horse_daily_report(horse_id):
    """التقرير اليومي المبسّط (طبيعي/غير طبيعي) — الواجهة الأساسية الجديدة لإدخال بيانات
    الحصان اليومية، تحل محل نظام الاثنتي عشرة مهمة كخطوة عمل الموظف الفعلية."""
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    date_raw = request.args.get("date")
    try:
        day = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else date.today()
    except ValueError:
        day = date.today()

    report = DailyReport.query.filter_by(horse_id=horse.id, report_date=day).first()

    if request.method == "POST":
        if not report:
            report = DailyReport(horse_id=horse.id, report_date=day)
            db.session.add(report)

        for key, _ in DailyReport.STATUS_FIELDS:
            status = request.form.get(f"{key}_status", DailyReport.STATUS_NORMAL)
            if status not in DailyReport.STATUSES:
                status = DailyReport.STATUS_NORMAL
            setattr(report, f"{key}_status", status)
            detail = request.form.get(f"{key}_detail", "").strip()[:500] if status == DailyReport.STATUS_ABNORMAL else None
            setattr(report, f"{key}_detail", detail)

        report.training_activity = request.form.get("training_activity", "").strip()[:2000] or None
        report.medication_given = request.form.get("medication_given", "").strip()[:2000] or None
        report.note = request.form.get("note", "").strip()[:1000] or None

        photo_path = save_photo(request.files.get("photo"), f"reports/{horse.id}")
        if photo_path:
            report.photo_path = photo_path

        report.completed_by = user.id
        db.session.commit()
        app.logger.info("daily_report_saved horse_id=%s date=%s by user_id=%s", horse.id, day, user.id)
        flash("تم حفظ التقرير اليومي", "success")
        return redirect(url_for("admin_horse_daily_report", horse_id=horse.id, date=day.isoformat()))

    return render_template("admin_horse_daily_report.html", horse=horse, report=report, day=day,
                            status_fields=DailyReport.STATUS_FIELDS)


@app.route("/admin/horse/<int:horse_id>/tasks")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_horse_tasks(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    date_raw = request.args.get("date")
    try:
        day = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else date.today()
    except ValueError:
        day = date.today()

    tasks = get_or_create_tasks_for_day(horse, day)
    done_count = sum(1 for t in tasks if t.status == DailyTask.STATUS_DONE)
    staff_members = User.query.filter_by(stable_id=user.stable_id, role=User.ROLE_STAFF).order_by(User.name).all()
    return render_template("admin_horse_tasks.html", horse=horse, tasks=tasks, day=day,
                            done_count=done_count, total_count=len(tasks),
                            prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
                            today=date.today(), staff_members=staff_members)


@app.route("/admin/horse/<int:horse_id>/tasks/report")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_horse_report(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    date_raw = request.args.get("date")
    try:
        day = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else date.today()
    except ValueError:
        day = date.today()

    tasks = get_or_create_tasks_for_day(horse, day)
    stable = db.session.get(Stable, user.stable_id)
    pdf_buf = build_daily_report_pdf(stable, horse, day, tasks)
    filename = f"تقرير-{horse.name}-{day.isoformat()}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/admin/tasks/<int:task_id>/update", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_task_update(task_id):
    user = current_user()
    task = db.get_or_404(DailyTask, task_id)
    if task.horse.stable_id != user.stable_id:
        abort(403)

    new_status = request.form.get("status")
    if new_status not in (DailyTask.STATUS_PENDING, DailyTask.STATUS_DONE, DailyTask.STATUS_SKIPPED):
        new_status = task.status
    task.status = new_status

    # التكليف الآن عبر حساب موظف فعلي بدل النص الحر — نتحقق أن الموظف المختار فعلًا من نفس المربط
    assigned_to_id_raw = request.form.get("assigned_to_id")
    if assigned_to_id_raw is not None:
        if assigned_to_id_raw == "":
            task.assigned_to_id = None
        else:
            try:
                staff_id = int(assigned_to_id_raw)
            except ValueError:
                staff_id = None
            if staff_id is not None:
                staff = User.query.filter_by(id=staff_id, stable_id=user.stable_id,
                                              role=User.ROLE_STAFF).first()
                if staff:
                    task.assigned_to_id = staff.id

    task.notes = request.form.get("notes", "").strip()[:2000]

    photo_path = save_photo(request.files.get("photo"), f"tasks/{task.horse_id}")
    if photo_path:
        task.photo_path = photo_path

    if new_status == DailyTask.STATUS_DONE:
        task.completed_by = user.id
        task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        task.completed_by = None
        task.completed_at = None

    db.session.commit()
    app.logger.info("task_updated id=%s status=%s by user_id=%s", task.id, task.status, user.id)

    redirect_date = request.form.get("redirect_date") or task.task_date.isoformat()
    return redirect(url_for("admin_horse_tasks", horse_id=task.horse_id, date=redirect_date))


@app.route("/admin/horse/new", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_horse_new():
    user = current_user()
    horse_owners = User.query.filter_by(stable_id=user.stable_id, role=User.ROLE_HORSE_OWNER).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("اسم الحصان مطلوب", "error")
            return render_template("admin_horse_new.html", horse_owners=horse_owners, genders=Horse.GENDERS)

        photo_path = save_photo(request.files.get("photo"), "horses/new")
        owner_id = request.form.get("owner_id") or None
        # تأكد أن المالك المُختار فعلًا تابع لنفس الإسطبل (منع تسريب بيانات بين المرابط)
        if owner_id:
            owner = User.query.filter_by(id=int(owner_id), stable_id=user.stable_id,
                                          role=User.ROLE_HORSE_OWNER).first()
            owner_id = owner.id if owner else None

        birth_year_raw = request.form.get("birth_year")
        birth_year = None
        if birth_year_raw and birth_year_raw.isdigit():
            birth_year = int(birth_year_raw)

        gender = request.form.get("gender", "").strip()
        if gender not in Horse.GENDERS:
            gender = None

        horse = Horse(
            stable_id=user.stable_id,
            owner_id=owner_id,
            name=name,
            breed=request.form.get("breed", "").strip()[:100],
            color=request.form.get("color", "").strip()[:50],
            gender=gender,
            sire_name=request.form.get("sire_name", "").strip()[:100] or None,
            dam_name=request.form.get("dam_name", "").strip()[:100] or None,
            birth_year=birth_year,
            service_type=request.form.get("service_type", "boarding"),
            notes=request.form.get("notes", "").strip()[:2000],
            photo_path=photo_path,
            chip_number=request.form.get("chip_number", "").strip()[:50] or None,
            stall_number=request.form.get("stall_number", "").strip()[:20] or None,
            health_notes=request.form.get("health_notes", "").strip()[:2000] or None,
            allergies=request.form.get("allergies", "").strip()[:1000] or None,
            feeding_plan=request.form.get("feeding_plan", "").strip()[:2000] or None,
            vet_name=request.form.get("vet_name", "").strip()[:100] or None,
            vet_contact=request.form.get("vet_contact", "").strip()[:100] or None,
            important_alert=request.form.get("important_alert", "").strip()[:1000] or None,
        )
        db.session.add(horse)
        db.session.commit()
        app.logger.info("horse_created id=%s by user_id=%s", horse.id, user.id)
        flash(f"تمت إضافة الحصان «{horse.name}» بنجاح", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_horse_new.html", horse_owners=horse_owners, genders=Horse.GENDERS)


@app.route("/admin/horse/<int:horse_id>/edit", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_horse_edit(horse_id):
    """تعديل بيانات حصان موجود — متاح للموظفين أيضًا (إدخال/تحديث بيانات الخيول والملف الصحي)."""
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()
    horse_owners = User.query.filter_by(stable_id=user.stable_id, role=User.ROLE_HORSE_OWNER).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("اسم الحصان مطلوب", "error")
            return render_template("admin_horse_edit.html", horse=horse, horse_owners=horse_owners,
                                    genders=Horse.GENDERS)

        photo_path = save_photo(request.files.get("photo"), f"horses/{horse.id}")
        if photo_path:
            horse.photo_path = photo_path

        owner_id = request.form.get("owner_id") or None
        if owner_id:
            owner = User.query.filter_by(id=int(owner_id), stable_id=user.stable_id,
                                          role=User.ROLE_HORSE_OWNER).first()
            owner_id = owner.id if owner else None
        horse.owner_id = owner_id

        birth_year_raw = request.form.get("birth_year")
        horse.birth_year = int(birth_year_raw) if birth_year_raw and birth_year_raw.isdigit() else None

        gender = request.form.get("gender", "").strip()
        horse.gender = gender if gender in Horse.GENDERS else None

        horse.name = name
        horse.breed = request.form.get("breed", "").strip()[:100] or None
        horse.color = request.form.get("color", "").strip()[:50] or None
        horse.sire_name = request.form.get("sire_name", "").strip()[:100] or None
        horse.dam_name = request.form.get("dam_name", "").strip()[:100] or None
        horse.service_type = request.form.get("service_type", "boarding")
        horse.notes = request.form.get("notes", "").strip()[:2000] or None
        horse.chip_number = request.form.get("chip_number", "").strip()[:50] or None
        horse.stall_number = request.form.get("stall_number", "").strip()[:20] or None
        horse.health_notes = request.form.get("health_notes", "").strip()[:2000] or None
        horse.allergies = request.form.get("allergies", "").strip()[:1000] or None
        horse.feeding_plan = request.form.get("feeding_plan", "").strip()[:2000] or None
        horse.vet_name = request.form.get("vet_name", "").strip()[:100] or None
        horse.vet_contact = request.form.get("vet_contact", "").strip()[:100] or None
        horse.important_alert = request.form.get("important_alert", "").strip()[:1000] or None

        db.session.commit()
        app.logger.info("horse_updated id=%s by user_id=%s", horse.id, user.id)
        flash(f"تم تحديث بيانات «{horse.name}» بنجاح", "success")
        return redirect(url_for("admin_horse_detail", horse_id=horse.id))

    return render_template("admin_horse_edit.html", horse=horse, horse_owners=horse_owners, genders=Horse.GENDERS)


@app.route("/admin/horse/<int:horse_id>/vaccinations/new", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_vaccination_new(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    name = request.form.get("name", "").strip()
    if not name:
        flash("اسم التطعيم مطلوب", "error")
        return redirect(url_for("admin_horse_detail", horse_id=horse.id))

    def parse_date(raw):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            return None

    vaccination = Vaccination(
        horse_id=horse.id,
        name=name[:100],
        given_date=parse_date(request.form.get("given_date", "")),
        next_due_date=parse_date(request.form.get("next_due_date", "")),
        notes=request.form.get("notes", "").strip()[:300] or None,
    )
    db.session.add(vaccination)
    db.session.commit()
    flash("تمت إضافة سجل التطعيم", "success")
    return redirect(url_for("admin_horse_detail", horse_id=horse.id))


@app.route("/admin/vaccination/<int:vaccination_id>/delete", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_vaccination_delete(vaccination_id):
    user = current_user()
    vaccination = Vaccination.query.join(Horse).filter(
        Vaccination.id == vaccination_id, Horse.stable_id == user.stable_id
    ).first_or_404()
    horse_id = vaccination.horse_id
    db.session.delete(vaccination)
    db.session.commit()
    flash("تم حذف سجل التطعيم", "success")
    return redirect(url_for("admin_horse_detail", horse_id=horse_id))


@app.route("/admin/horse/<int:horse_id>/medications/new", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_medication_new(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, stable_id=user.stable_id).first_or_404()

    name = request.form.get("name", "").strip()
    if not name:
        flash("اسم الدواء مطلوب", "error")
        return redirect(url_for("admin_horse_detail", horse_id=horse.id))

    def parse_date(raw):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            return None

    medication = Medication(
        horse_id=horse.id,
        name=name[:100],
        dosage=request.form.get("dosage", "").strip()[:100] or None,
        start_date=parse_date(request.form.get("start_date", "")),
        end_date=parse_date(request.form.get("end_date", "")),
        notes=request.form.get("notes", "").strip()[:300] or None,
    )
    db.session.add(medication)
    db.session.commit()
    flash("تمت إضافة الدواء", "success")
    return redirect(url_for("admin_horse_detail", horse_id=horse.id))


@app.route("/admin/medication/<int:medication_id>/delete", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF)
def admin_medication_delete(medication_id):
    user = current_user()
    medication = Medication.query.join(Horse).filter(
        Medication.id == medication_id, Horse.stable_id == user.stable_id
    ).first_or_404()
    horse_id = medication.horse_id
    db.session.delete(medication)
    db.session.commit()
    flash("تم حذف الدواء", "success")
    return redirect(url_for("admin_horse_detail", horse_id=horse_id))


MAX_GALLERY_PHOTOS = 30


@app.route("/admin/gallery", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_gallery():
    user = current_user()
    photos = GalleryPhoto.query.filter_by(stable_id=user.stable_id).order_by(
        GalleryPhoto.created_at.desc()).all()

    if request.method == "POST":
        if len(photos) >= MAX_GALLERY_PHOTOS:
            flash(f"وصلت الحد الأقصى لعدد الصور ({MAX_GALLERY_PHOTOS} صورة) — احذف صورة قديمة أولًا لإضافة جديدة", "error")
            return redirect(url_for("admin_gallery"))

        photo_path = save_photo(request.files.get("photo"), f"gallery/{user.stable_id}")
        if not photo_path:
            flash("الرجاء اختيار صورة صالحة", "error")
            return redirect(url_for("admin_gallery"))

        photo = GalleryPhoto(
            stable_id=user.stable_id,
            photo_path=photo_path,
            caption=request.form.get("caption", "").strip()[:200],
            uploaded_by=user.id,
        )
        db.session.add(photo)
        db.session.commit()
        app.logger.info("gallery_photo_added id=%s stable_id=%s by user_id=%s",
                         photo.id, user.stable_id, user.id)
        flash("تمت إضافة الصورة لمعرض المربط", "success")
        return redirect(url_for("admin_gallery"))

    return render_template("admin_gallery.html", photos=photos, max_photos=MAX_GALLERY_PHOTOS)


@app.route("/admin/gallery/<int:photo_id>/delete", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_gallery_delete(photo_id):
    user = current_user()
    # first_or_404 مقيّد بـ stable_id يمنع أي مربط من حذف صور مربط آخر
    photo = GalleryPhoto.query.filter_by(id=photo_id, stable_id=user.stable_id).first_or_404()
    db.session.delete(photo)
    db.session.commit()
    app.logger.info("gallery_photo_deleted id=%s by user_id=%s", photo_id, user.id)
    flash("تم حذف الصورة", "success")
    return redirect(url_for("admin_gallery"))


@app.route("/admin/owners")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_owners():
    user = current_user()
    owners = (User.query.filter_by(stable_id=user.stable_id, role=User.ROLE_HORSE_OWNER)
              .order_by(User.name).all())
    return render_template("admin_owners.html", owners=owners)


@app.route("/admin/owners/new", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_owner_new():
    user = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone, phone_warning = normalize_optional_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")

        if not all([name, email, password]):
            flash("الرجاء تعبئة الاسم والبريد وكلمة المرور", "error")
            return render_template("admin_owner_new.html")
        if len(password) < 8:
            flash("يجب ألا تقل كلمة المرور عن 8 أحرف", "error")
            return render_template("admin_owner_new.html")
        if "@" not in email or "." not in email.split("@")[-1]:
            flash("الرجاء إدخال بريد إلكتروني صحيح", "error")
            return render_template("admin_owner_new.html")
        if User.query.filter_by(email=email).first():
            flash("هذا البريد الإلكتروني مسجّل مسبقًا لحساب آخر", "error")
            return render_template("admin_owner_new.html")
        if phone and User.query.filter_by(phone=phone).first():
            flash("رقم الجوال هذا مسجّل مسبقًا لحساب آخر", "error")
            return render_template("admin_owner_new.html")

        owner = User(name=name, email=email, phone=phone,
                     stable_id=user.stable_id, role=User.ROLE_HORSE_OWNER)
        owner.set_password(password)
        db.session.add(owner)
        db.session.commit()
        app.logger.info("horse_owner_created id=%s by user_id=%s", owner.id, user.id)
        if phone_warning:
            flash(phone_warning, "error")
        flash(f"تم إنشاء حساب «{owner.name}» بنجاح — شارِكه ببريده وكلمة المرور ليسجّل دخوله", "success")
        return redirect(url_for("admin_owners"))

    return render_template("admin_owner_new.html")


@app.route("/admin/staff")
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_staff():
    user = current_user()
    staff = (User.query.filter_by(stable_id=user.stable_id, role=User.ROLE_STAFF)
             .order_by(User.name).all())
    return render_template("admin_staff.html", staff=staff)


@app.route("/admin/staff/new", methods=["GET", "POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_staff_new():
    user = current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone, phone_warning = normalize_optional_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")
        specialty = request.form.get("specialty", "").strip()

        if not all([name, email, password]):
            flash("الرجاء تعبئة الاسم والبريد وكلمة المرور", "error")
            return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)
        if len(password) < 8:
            flash("يجب ألا تقل كلمة المرور عن 8 أحرف", "error")
            return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)
        if "@" not in email or "." not in email.split("@")[-1]:
            flash("الرجاء إدخال بريد إلكتروني صحيح", "error")
            return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)
        if User.query.filter_by(email=email).first():
            flash("هذا البريد الإلكتروني مسجّل مسبقًا لحساب آخر", "error")
            return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)
        if phone and User.query.filter_by(phone=phone).first():
            flash("رقم الجوال هذا مسجّل مسبقًا لحساب آخر", "error")
            return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)
        if specialty and specialty not in User.SPECIALTIES:
            specialty = None

        staff = User(name=name, email=email, phone=phone, specialty=specialty,
                     stable_id=user.stable_id, role=User.ROLE_STAFF)
        staff.set_password(password)
        db.session.add(staff)
        db.session.commit()
        app.logger.info("staff_created id=%s by user_id=%s", staff.id, user.id)
        if phone_warning:
            flash(phone_warning, "error")
        flash(f"تم إنشاء حساب «{staff.name}» بنجاح — شارِكه ببريده وكلمة المرور ليسجّل دخوله", "success")
        return redirect(url_for("admin_staff"))

    return render_template("admin_staff_new.html", specialties=User.SPECIALTIES)


@app.route("/admin/bookings/<int:booking_id>/confirm", methods=["POST"])
@login_required(User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN)
def admin_confirm_booking(booking_id):
    user = current_user()
    booking = Booking.query.filter_by(id=booking_id, stable_id=user.stable_id).first_or_404()
    booking.status = Booking.STATUS_CONFIRMED
    db.session.commit()
    flash("تم تأكيد الحجز", "success")
    return redirect(url_for("admin_dashboard"))


# ---------------------------------------------------------------- لوحة مالك الخيل (الزبون المُودِع)
@app.route("/owner")
@login_required(User.ROLE_HORSE_OWNER)
def owner_dashboard():
    user = current_user()
    horses = Horse.query.filter_by(owner_id=user.id).all()
    return render_template("owner_dashboard.html", horses=horses)


@app.route("/owner/horse/<int:horse_id>")
@login_required(User.ROLE_HORSE_OWNER)
def owner_horse_detail(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, owner_id=user.id).first_or_404()
    return render_template("owner_horse_detail.html", horse=horse)


@app.route("/owner/horse/<int:horse_id>/achievements/new", methods=["GET", "POST"])
@login_required(User.ROLE_HORSE_OWNER)
def owner_achievement_new(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, owner_id=user.id).first_or_404()

    if request.method == "POST":
        achievement, error = submit_achievement(horse, user)
        if error:
            flash(error, "error")
            return render_template("achievement_new.html", horse=horse, back_url=url_for("owner_horse_detail", horse_id=horse.id))
        flash("تم إرسال الإنجاز — بانتظار موافقة مشرف المنصة قبل ظهوره في منصة الفخر", "success")
        return redirect(url_for("owner_horse_detail", horse_id=horse.id))

    return render_template("achievement_new.html", horse=horse, back_url=url_for("owner_horse_detail", horse_id=horse.id))


@app.route("/owner/horse/<int:horse_id>/tasks")
@login_required(User.ROLE_HORSE_OWNER)
def owner_horse_tasks(horse_id):
    user = current_user()
    horse = Horse.query.filter_by(id=horse_id, owner_id=user.id).first_or_404()

    date_raw = request.args.get("date")
    try:
        day = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else date.today()
    except ValueError:
        day = date.today()

    tasks = get_or_create_tasks_for_day(horse, day)
    done_count = sum(1 for t in tasks if t.status == DailyTask.STATUS_DONE)
    return render_template("owner_horse_tasks.html", horse=horse, tasks=tasks, day=day,
                            done_count=done_count, total_count=len(tasks),
                            prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
                            today=date.today())


# ---------------------------------------------------------------- الحجز العام (للزوار)
@app.route("/s/<slug>/book", methods=["GET", "POST"])
def stable_book(slug):
    stable = Stable.query.filter_by(slug=slug).first_or_404()
    _stable_publicly_visible_or_404(stable)
    packages = Package.query.filter_by(stable_id=stable.id, is_active=True).all()
    rideable_horses = Horse.query.filter(
        Horse.stable_id == stable.id, Horse.is_public == True,
        Horse.service_type.in_(["training", "riding"])
    ).all()

    if request.method == "POST":
        user = current_user()
        if not user:
            flash("الرجاء تسجيل الدخول أو إنشاء حساب أولًا لإتمام الحجز", "error")
            return redirect(url_for("stable_register", slug=stable.slug))
        # أمان أساسي في منصة متعددة المرابط: يمنع حجز زائر مسجّل في مربط آخر هنا
        if user.stable_id != stable.id:
            flash("هذا الحساب مسجّل في مربط مختلف — سجّل حسابًا جديدًا خاصًا بهذا المربط", "error")
            return redirect(url_for("stable_home", slug=stable.slug))

        package = Package.query.filter_by(id=request.form.get("package_id"),
                                           stable_id=stable.id, is_active=True).first()
        if not package:
            flash("الباقة المختارة غير متاحة", "error")
            return redirect(url_for("stable_book", slug=stable.slug))

        horse_id = request.form.get("horse_id") or None
        if horse_id:
            # تأكد أن الحصان فعلًا من نفس الإسطبل ومتاح للحجز
            valid_horse = Horse.query.filter(
                Horse.id == int(horse_id), Horse.stable_id == stable.id, Horse.is_public == True,
                Horse.service_type.in_(["training", "riding"])
            ).first()
            horse_id = valid_horse.id if valid_horse else None

        session_date_raw = request.form.get("session_date")
        session_date = None
        if session_date_raw:
            try:
                session_date = datetime.strptime(session_date_raw, "%Y-%m-%d").date()
            except ValueError:
                session_date = None

        booking = Booking(
            stable_id=stable.id,
            visitor_id=user.id,
            package_id=package.id,
            horse_id=horse_id,
            session_date=session_date,
            sessions_remaining=package.session_count,
            amount=package.price,
            status=Booking.STATUS_PENDING,
            payment_status="unpaid",
        )
        db.session.add(booking)
        db.session.commit()
        app.logger.info("booking_created id=%s visitor_id=%s package_id=%s", booking.id, user.id, package.id)
        return redirect(url_for("checkout", booking_id=booking.id))

    return render_template("book.html", stable=stable, packages=packages, horses=rideable_horses)


@app.route("/checkout/<int:booking_id>", methods=["GET", "POST"])
def checkout(booking_id):
    user = current_user()
    booking = db.get_or_404(Booking, booking_id)
    # لا يجوز لأي زائر آخر رؤية أو دفع حجز ليس له
    if not user or booking.visitor_id != user.id:
        abort(403)
    if booking.payment_status == "paid":
        return redirect(url_for("booking_success", booking_id=booking.id))

    if not app.config["SIMULATED_PAYMENTS_ENABLED"]:
        return render_template("payment_unavailable.html", booking=booking), 503

    if request.method == "POST":
        # محاكاة بوابة دفع (Mada / Apple Pay / Visa) — يُستبدل لاحقًا ببوابة حقيقية مثل Moyasar أو HyperPay
        booking.payment_status = "paid"
        booking.payment_ref = f"SIM-{uuid.uuid4().hex[:12]}"
        booking.status = Booking.STATUS_CONFIRMED
        db.session.commit()
        app.logger.info("booking_paid id=%s", booking.id)
        return redirect(url_for("booking_success", booking_id=booking.id))
    return render_template("checkout.html", booking=booking)


@app.route("/booking/<int:booking_id>/success")
def booking_success(booking_id):
    user = current_user()
    booking = db.get_or_404(Booking, booking_id)
    if not user or booking.visitor_id != user.id:
        abort(403)
    return render_template("booking_success.html", booking=booking)


@app.route("/booking/<int:booking_id>/invoice")
def booking_invoice(booking_id):
    user = current_user()
    booking = db.get_or_404(Booking, booking_id)
    if not user:
        abort(403)
    is_owner_visitor = booking.visitor_id == user.id
    is_stable_staff = (user.stable_id == booking.stable_id and
                        user.role in (User.ROLE_STABLE_OWNER, User.ROLE_SUPER_ADMIN, User.ROLE_STAFF))
    if not (is_owner_visitor or is_stable_staff):
        abort(403)

    stable = db.session.get(Stable, booking.stable_id)
    pdf_buf = build_invoice_pdf(stable, booking)
    filename = f"فاتورة-{booking.id}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/review/<int:booking_id>", methods=["GET", "POST"])
def leave_review(booking_id):
    user = current_user()
    booking = db.get_or_404(Booking, booking_id)
    if not user or booking.visitor_id != user.id:
        abort(403)
    if booking.payment_status != "paid":
        flash("لا يمكن تقييم حجز لم يُدفع بعد", "error")
        return redirect(url_for("home"))
    if Review.query.filter_by(booking_id=booking.id).first():
        flash("سبق أن أرسلت تقييمًا لهذا الحجز", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        def parse_rating(raw):
            try:
                val = int(raw)
            except (TypeError, ValueError):
                return None
            return val if 1 <= val <= 5 else None

        horse_rating = parse_rating(request.form.get("horse_rating"))
        experience_rating = parse_rating(request.form.get("experience_rating"))
        if horse_rating is None or experience_rating is None:
            flash("الرجاء اختيار تقييم صحيح (من 1 إلى 5 نجوم)", "error")
            return render_template("review.html", booking=booking)

        review = Review(
            booking_id=booking.id,
            horse_id=booking.horse_id,
            rating=horse_rating,
            experience_rating=experience_rating,
            comment=(request.form.get("comment") or "").strip()[:1000],
        )
        db.session.add(review)
        db.session.commit()
        flash("شكرًا لتقييمك، نتشرف بزيارتك القادمة", "success")
        return redirect(url_for("home"))
    return render_template("review.html", booking=booking)


@app.route("/s/<slug>/horses/<int:horse_id>")
def stable_horse_profile(slug, horse_id):
    stable = Stable.query.filter_by(slug=slug).first_or_404()
    _stable_publicly_visible_or_404(stable)
    horse = Horse.query.filter_by(id=horse_id, stable_id=stable.id).first_or_404()
    user = current_user()
    is_staff_view = user and user.stable_id == stable.id and user.role in (
        User.ROLE_STABLE_OWNER, User.ROLE_STAFF, User.ROLE_SUPER_ADMIN
    )
    if not horse.is_public and not is_staff_view:
        abort(404)
    return render_template("public_horse_profile.html", horse=horse)


@app.route("/s/<slug>/gallery")
def stable_gallery(slug):
    stable = Stable.query.filter_by(slug=slug).first_or_404()
    _stable_publicly_visible_or_404(stable)
    photos = GalleryPhoto.query.filter_by(stable_id=stable.id).order_by(
        GalleryPhoto.created_at.desc()).all()
    return render_template("stable_gallery.html", stable=stable, photos=photos)


# ---------------------------------------------------------------- لوحة مشرف المنصة (تعدد المرابط)
@app.route("/platform")
@login_required(User.ROLE_SUPER_ADMIN)
def platform_dashboard():
    stables = Stable.query.order_by(Stable.name_ar).all()
    pending_count = Stable.query.filter_by(status=Stable.STATUS_PENDING).count()
    return render_template("platform_dashboard.html", stables=stables, pending_count=pending_count)


@app.route("/platform/stables/pending")
@login_required(User.ROLE_SUPER_ADMIN)
def platform_stables_pending():
    stables = Stable.query.filter_by(status=Stable.STATUS_PENDING).order_by(Stable.created_at).all()
    return render_template("platform_stables_pending.html", stables=stables)


@app.route("/platform/stables/<int:stable_id>/approve", methods=["POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_stable_approve(stable_id):
    stable = db.get_or_404(Stable, stable_id)
    stable.status = Stable.STATUS_APPROVED
    db.session.commit()
    app.logger.info("stable_approved id=%s by super_admin_id=%s", stable.id, current_user().id)
    flash(f"تم اعتماد مربط «{stable.name_ar}» — أصبح ظاهرًا للزوار الآن", "success")
    return redirect(url_for("platform_stables_pending"))


@app.route("/platform/stables/<int:stable_id>/reject", methods=["POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_stable_reject(stable_id):
    stable = db.get_or_404(Stable, stable_id)
    stable.status = Stable.STATUS_REJECTED
    db.session.commit()
    app.logger.info("stable_rejected id=%s by super_admin_id=%s", stable.id, current_user().id)
    flash(f"تم رفض مربط «{stable.name_ar}»", "success")
    return redirect(url_for("platform_stables_pending"))


def parse_stable_location_fields(form):
    """يتحقق من حقول المدينة والموقع والإحداثيات ويرجعها منظّفة. كلها اختيارية."""
    city = form.get("city", "").strip()
    if city and city not in Stable.CITIES:
        city = None
    location = form.get("location", "").strip()[:200]

    lat_raw = form.get("latitude", "").strip()
    lng_raw = form.get("longitude", "").strip()
    latitude = longitude = None
    if lat_raw and lng_raw:
        try:
            lat_val = float(lat_raw)
            lng_val = float(lng_raw)
            if -90 <= lat_val <= 90 and -180 <= lng_val <= 180:
                latitude, longitude = lat_val, lng_val
        except ValueError:
            pass
    return city, location, latitude, longitude


@app.route("/platform/stables/new", methods=["GET", "POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_stable_new():
    if request.method == "POST":
        import re as _re

        stable_name = request.form.get("stable_name", "").strip()
        slug = request.form.get("slug", "").strip().lower()
        owner_name = request.form.get("owner_name", "").strip()
        owner_email = request.form.get("owner_email", "").strip().lower()
        owner_password = request.form.get("owner_password", "")

        if not all([stable_name, slug, owner_name, owner_email, owner_password]):
            flash("الرجاء تعبئة جميع الحقول", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)
        if len(owner_password) < 8:
            flash("يجب ألا تقل كلمة المرور عن 8 أحرف", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)
        if "@" not in owner_email or "." not in owner_email.split("@")[-1]:
            flash("الرجاء إدخال بريد إلكتروني صحيح لمالك المربط", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)
        if not _re.fullmatch(r"[a-z0-9-]+", slug):
            flash("الرابط المختصر يجب أن يحتوي أحرفًا إنجليزية صغيرة وأرقامًا وشرطات (-) فقط", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)
        if Stable.query.filter_by(slug=slug).first():
            flash("هذا الرابط المختصر مستخدم لمربط آخر بالفعل", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)
        if User.query.filter_by(email=owner_email).first():
            flash("هذا البريد الإلكتروني مسجّل مسبقًا لحساب آخر", "error")
            return render_template("platform_stable_new.html", cities=Stable.CITIES)

        city, location, latitude, longitude = parse_stable_location_fields(request.form)
        stable = Stable(name_ar=stable_name, slug=slug, city=city, location=location,
                         latitude=latitude, longitude=longitude, status=Stable.STATUS_APPROVED)
        db.session.add(stable)
        db.session.commit()

        owner = User(name=owner_name, email=owner_email, stable_id=stable.id,
                     role=User.ROLE_STABLE_OWNER)
        owner.set_password(owner_password)
        db.session.add(owner)
        db.session.commit()
        app.logger.info("stable_created id=%s slug=%s by super_admin_id=%s",
                         stable.id, stable.slug, current_user().id)
        flash(f"تم إنشاء مربط «{stable_name}» وحساب مالكه بنجاح", "success")
        return redirect(url_for("platform_dashboard"))

    return render_template("platform_stable_new.html", cities=Stable.CITIES)


@app.route("/platform/stables/<int:stable_id>/edit", methods=["GET", "POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_stable_edit(stable_id):
    stable = db.get_or_404(Stable, stable_id)
    if request.method == "POST":
        name = request.form.get("stable_name", "").strip()
        if not name:
            flash("اسم المربط مطلوب", "error")
            return render_template("platform_stable_edit.html", stable=stable, cities=Stable.CITIES)
        stable.name_ar = name
        city, location, latitude, longitude = parse_stable_location_fields(request.form)
        stable.city = city
        stable.location = location
        stable.latitude = latitude
        stable.longitude = longitude
        db.session.commit()
        flash("تم تحديث بيانات المربط", "success")
        return redirect(url_for("platform_dashboard"))
    return render_template("platform_stable_edit.html", stable=stable, cities=Stable.CITIES)


# ---------------------------------------------------------------- منصة الفخر (صفحة مشتركة لإنجازات الخيول)
@app.route("/lounge")
def lounge():
    achievements = (Achievement.query.filter_by(status=Achievement.STATUS_APPROVED)
                    .order_by(Achievement.achievement_date.desc().nullslast(),
                              Achievement.created_at.desc())
                    .limit(60).all())
    return render_template("lounge.html", achievements=achievements)


@app.route("/platform/lounge")
@login_required(User.ROLE_SUPER_ADMIN)
def platform_lounge():
    pending = (Achievement.query.filter_by(status=Achievement.STATUS_PENDING)
               .order_by(Achievement.created_at.asc()).all())
    return render_template("platform_lounge.html", achievements=pending)


@app.route("/platform/lounge/<int:achievement_id>/approve", methods=["POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_lounge_approve(achievement_id):
    user = current_user()
    achievement = db.get_or_404(Achievement, achievement_id)
    achievement.status = Achievement.STATUS_APPROVED
    achievement.reviewed_by = user.id
    achievement.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    app.logger.info("achievement_approved id=%s by user_id=%s", achievement.id, user.id)
    flash("تم اعتماد الإنجاز — سيظهر الآن في منصة الفخر", "success")
    return redirect(url_for("platform_lounge"))


@app.route("/platform/lounge/<int:achievement_id>/reject", methods=["POST"])
@login_required(User.ROLE_SUPER_ADMIN)
def platform_lounge_reject(achievement_id):
    user = current_user()
    achievement = db.get_or_404(Achievement, achievement_id)
    achievement.status = Achievement.STATUS_REJECTED
    achievement.reviewed_by = user.id
    achievement.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    app.logger.info("achievement_rejected id=%s by user_id=%s", achievement.id, user.id)
    flash("تم رفض الإنجاز", "success")
    return redirect(url_for("platform_lounge"))


@app.cli.command("seed-admin")
def seed_admin():
    """ينشئ الإسطبل الحقيقي وحساب مالك الإسطبل الأول — مرة واحدة فقط.
    آمن يتكرر تشغيله عند كل نشر (لا يفعل شيئًا إن كان الإسطبل موجودًا مسبقًا)،
    لذلك يصلح ليكون جزءًا من buildCommand في render.yaml حتى بدون الوصول لـ Shell."""
    import click

    if Stable.query.first():
        click.echo("يوجد إسطبل مسبقًا في قاعدة البيانات — تم تجاوز الإنشاء.")
        return

    stable_name = os.environ.get("STABLE_NAME", "الخيول السعودية")
    admin_name = os.environ.get("ADMIN_NAME")
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if not all([admin_name, admin_email, admin_password]):
        click.echo("تم التجاوز: لم يتم ضبط ADMIN_NAME / ADMIN_EMAIL / ADMIN_PASSWORD كمتغيرات بيئة.")
        return

    stable = Stable(name_ar=stable_name, slug="main")
    db.session.add(stable)
    db.session.commit()

    admin = User(name=admin_name, email=admin_email.strip().lower(),
                 stable_id=stable.id, role=User.ROLE_STABLE_OWNER)
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f"تم إنشاء الإسطبل «{stable_name}» وحساب المالك {admin_email} بنجاح.")


@app.cli.command("promote-super-admin")
def promote_super_admin():
    """يرقّي حساب صاحب المنصة (المحدد عبر ADMIN_EMAIL) إلى مشرف منصة (super_admin) — آمن يتكرر تشغيله.
    يبقي stable_id للحساب كما هو، فهو يستمر بإدارة مربطه الخاص عبر /admin كالمعتاد،
    ويكتسب إضافةً صلاحية إنشاء وإدارة مرابط أخرى عبر /platform."""
    import click

    admin_email = os.environ.get("ADMIN_EMAIL")
    if not admin_email:
        click.echo("تم التجاوز: لم يتم ضبط ADMIN_EMAIL.")
        return

    user = User.query.filter_by(email=admin_email.strip().lower()).first()
    if not user:
        click.echo(f"لم يتم العثور على مستخدم بالبريد {admin_email}.")
        return
    if user.role == User.ROLE_SUPER_ADMIN:
        click.echo("الحساب مُرقّى مسبقًا لمشرف منصة — تم تجاوز الترقية.")
        return

    user.role = User.ROLE_SUPER_ADMIN
    db.session.commit()
    click.echo(f"تم ترقية {admin_email} إلى مشرف منصة (super_admin) بنجاح.")


if __name__ == "__main__":
    # للتطوير المحلي فقط. في الإنتاج يُشغَّل التطبيق عبر Gunicorn (انظر Procfile)
    app.run(debug=app.config["DEBUG"], host="127.0.0.1", port=int(os.environ.get("PORT", 5050)))
