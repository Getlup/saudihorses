# -*- coding: utf-8 -*-
"""
نماذج قاعدة البيانات لمنصة "المِرباط" — نظام إدارة الإسطبلات
==============================================================

مخطط العلاقات (Schema Overview):

Stable (اسطبل)
  └── 1‑to‑many → User (مستخدمون: مالك اسطبل / مالك خيل / زائر)
  └── 1‑to‑many → Horse (خيول)
  └── 1‑to‑many → Package (باقات تدريب/ركوب)
  └── 1‑to‑many → Booking (حجوزات)

User (مستخدم)
  role = super_admin | stable_owner | horse_owner | visitor
  └── 1‑to‑many → Horse (إن كان مالك خيل، يملك عدة خيول)
  └── 1‑to‑many → Booking (إن كان زائرًا يحجز حصصًا)

Horse (حصان)
  ينتمي لإسطبل واحد، ومملوك إما لمالك خيل خارجي (owner_id) أو للإسطبل نفسه (owner_id = NULL)
  └── 1‑to‑many → DailyLog (سجل يومي: تغذية / رعاية / تدريب / أدوية + صورة)
  └── 1‑to‑many → Review (تقييمات الزوار للحصان بعد الحجز)

DailyLog (سجل يومي)
  يُدخله مالك/مشرف الإسطبل يوميًا لكل حصان على حدة

Package (باقة)
  باقات حصص فردية أو مجمّعة بأسعار مدروسة على السوق السعودي

Booking (حجز)
  حجز زائر لحصة فردية أو باقة، مرتبط اختياريًا بحصان معيّن للركوب/الإيجار

Review (تقييم)
  تقييم الزائر للحصان وتجربة الحصة بعد انتهاء الحجز

Achievement (إنجاز — لصفحة Lounge المشتركة بين كل المرابط)
  يقدّمه مالك الإسطبل أو مالك الحصان لحصان معيّن (مسابقة/جائزة + صورة احترافية)
  يحتاج موافقة مشرف المنصة (super_admin) قبل ظهوره في صفحة Lounge العامة
"""

from datetime import datetime, date, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Stable(db.Model):
    __tablename__ = "stables"

    # قائمة مدن ثابتة تُستخدم للفلترة الموثوقة في البحث (بدل نص حر قد يُكتب بأشكال مختلفة)
    CITIES = [
        "الرياض", "جدة", "مكة المكرمة", "المدينة المنورة", "الدمام", "الخبر", "الظهران",
        "الطائف", "تبوك", "بريدة", "خميس مشيط", "أبها", "نجران", "جازان", "حائل",
        "الدوحة", "أبوظبي", "دبي", "الشارقة", "الكويت", "المنامة", "مسقط",
    ]

    id = db.Column(db.Integer, primary_key=True)
    name_ar = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), unique=True, nullable=False)
    location = db.Column(db.String(200))   # وصف نصي حر إضافي (حي/شارع)، اختياري
    city = db.Column(db.String(50), index=True)   # مدينة موحّدة من CITIES — تُستخدم للبحث والفلترة
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    description = db.Column(db.Text)
    logo_path = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    users = db.relationship("User", backref="stable", lazy=True)
    horses = db.relationship("Horse", backref="stable", lazy=True)
    packages = db.relationship("Package", backref="stable", lazy=True)
    bookings = db.relationship("Booking", backref="stable", lazy=True)
    gallery_photos = db.relationship("GalleryPhoto", backref="stable", lazy=True,
                                      order_by="desc(GalleryPhoto.created_at)")


class User(db.Model):
    __tablename__ = "users"

    ROLE_SUPER_ADMIN = "super_admin"
    ROLE_STABLE_OWNER = "stable_owner"
    ROLE_STAFF = "staff"          # موظف/مدرب/طبيب بيطري/حداد — ينفّذ المهام اليومية
    ROLE_HORSE_OWNER = "horse_owner"
    ROLE_VISITOR = "visitor"

    # التخصصات المقترحة لحساب الموظف — نص حر مقيّد بقائمة لتوحيد العرض في القوائم المنسدلة
    SPECIALTIES = ["مدير الإسطبل", "موظف عام", "مدرب", "طبيب بيطري", "حداد"]

    id = db.Column(db.Integer, primary_key=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=True, index=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_VISITOR)
    specialty = db.Column(db.String(50))   # يُستخدم فقط لحسابات role=staff
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    horses = db.relationship("Horse", backref="owner", lazy=True,
                              foreign_keys="Horse.owner_id")
    bookings = db.relationship("Booking", backref="visitor", lazy=True,
                                foreign_keys="Booking.visitor_id")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Horse(db.Model):
    __tablename__ = "horses"

    id = db.Column(db.Integer, primary_key=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)  # NULL = يملكه الإسطبل نفسه
    name = db.Column(db.String(100), nullable=False)
    breed = db.Column(db.String(100))          # السلالة
    color = db.Column(db.String(50))           # اللون
    birth_year = db.Column(db.Integer)
    photo_path = db.Column(db.String(300))
    service_type = db.Column(db.String(30), default="boarding", index=True)  # boarding | training | rental
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    logs = db.relationship("DailyLog", backref="horse", lazy=True,
                            order_by="desc(DailyLog.log_date)")
    tasks = db.relationship("DailyTask", backref="horse", lazy=True,
                             order_by="desc(DailyTask.task_date)")
    reviews = db.relationship("Review", backref="horse", lazy=True)
    achievements = db.relationship("Achievement", backref="horse", lazy=True,
                                    order_by="desc(Achievement.achievement_date)")

    @property
    def average_rating(self):
        if not self.reviews:
            return None
        return round(sum(r.rating for r in self.reviews) / len(self.reviews), 1)


class DailyLog(db.Model):
    """سجل قديم بنص حر (قبل نظام المهام اليومية DailyTask) — يُبقى للسجلات التاريخية فقط،
    لا يُستخدم لإدخالات جديدة."""
    __tablename__ = "daily_logs"

    id = db.Column(db.Integer, primary_key=True)
    horse_id = db.Column(db.Integer, db.ForeignKey("horses.id"), nullable=False, index=True)
    log_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    feeding = db.Column(db.Text)       # التغذية / العلف
    care = db.Column(db.Text)          # الرعاية / النظافة
    training = db.Column(db.Text)      # التدريب
    medication = db.Column(db.Text)    # الأدوية / العلاج البيطري
    photo_path = db.Column(db.String(300))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class DailyTask(db.Model):
    """مهمة يومية واحدة لحصان محدد — نظام المهام الذي يستبدل السجل النصي الحر.
    الموظف/مالك الإسطبل ينفّذ المهمة (يعلّمها منجزة) بدل ما يكتب تقريرًا حرًا؛
    التقارير والمؤشرات تُشتق لاحقًا من حالة المهام المنجزة."""
    __tablename__ = "daily_tasks"

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_SKIPPED = "skipped"

    # ترتيب وتسميات أنواع المهام الاثنتي عشرة لليوم الواحد لكل حصان
    TASK_TYPES = [
        ("morning_check", "الفحص الصباحي"),
        ("morning_feeding", "التغذية الصباحية"),
        ("water_check", "فحص المياه"),
        ("stall_cleaning", "تنظيف الحظيرة"),
        ("training", "التمارين / التدريب"),
        ("grooming", "العناية والتنظيف"),
        ("medication", "إعطاء الأدوية"),
        ("vet_appointment", "الموعد البيطري"),
        ("owner_request", "طلبات المالك"),
        ("evening_feeding", "التغذية المسائية"),
        ("night_check", "الفحص الليلي"),
        ("day_closing", "إغلاق اليوم"),
    ]
    TASK_TYPE_LABELS = dict(TASK_TYPES)

    id = db.Column(db.Integer, primary_key=True)
    horse_id = db.Column(db.Integer, db.ForeignKey("horses.id"), nullable=False, index=True)
    task_date = db.Column(db.Date, default=date.today, nullable=False, index=True)
    task_type = db.Column(db.String(30), nullable=False, index=True)
    status = db.Column(db.String(20), default=STATUS_PENDING, index=True)

    assigned_to = db.Column(db.String(150))   # نص حر قديم (قبل حسابات الموظفين) — يُبقى لعرض السجلات التاريخية فقط
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # حساب الموظف المكلَّف فعليًا
    notes = db.Column(db.Text)
    photo_path = db.Column(db.String(300))

    completed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    assigned_staff = db.relationship("User", foreign_keys=[assigned_to_id])

    __table_args__ = (
        db.UniqueConstraint("horse_id", "task_date", "task_type", name="uq_daily_task_horse_date_type"),
    )

    @property
    def assignee_name(self):
        """اسم المكلَّف بالمهمة — يفضّل حساب الموظف الفعلي، ويرجع للنص القديم إن وُجد."""
        if self.assigned_staff:
            return self.assigned_staff.name
        return self.assigned_to

    @property
    def type_label(self):
        return self.TASK_TYPE_LABELS.get(self.task_type, self.task_type)


class Package(db.Model):
    __tablename__ = "packages"

    id = db.Column(db.Integer, primary_key=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=False)
    name_ar = db.Column(db.String(150), nullable=False)
    kind = db.Column(db.String(30), default="riding")  # riding | training | jumping | annual
    session_count = db.Column(db.Integer, default=1)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    duration_label = db.Column(db.String(50))   # مثال: "صالحة لمدة شهر"
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)

    bookings = db.relationship("Booking", backref="package", lazy=True)


class Booking(db.Model):
    __tablename__ = "bookings"

    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=False, index=True)
    visitor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id"), nullable=True)
    horse_id = db.Column(db.Integer, db.ForeignKey("horses.id"), nullable=True)
    session_date = db.Column(db.Date)
    sessions_remaining = db.Column(db.Integer, default=1)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default=STATUS_PENDING, index=True)
    payment_status = db.Column(db.String(20), default="unpaid")  # unpaid | paid
    payment_ref = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    reviews = db.relationship("Review", backref="booking", lazy=True)


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, unique=True)
    horse_id = db.Column(db.Integer, db.ForeignKey("horses.id"), nullable=True)
    rating = db.Column(db.Integer, nullable=False)          # تقييم الحصان 1-5
    experience_rating = db.Column(db.Integer, nullable=False)  # تقييم التجربة 1-5
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Achievement(db.Model):
    """إنجاز حصان (مسابقة/جائزة + صورة احترافية) — يُقدَّم من مالك الإسطبل أو مالك الحصان،
    ولا يظهر في صفحة Lounge العامة إلا بعد موافقة مشرف المنصة (super_admin)."""
    __tablename__ = "achievements"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    id = db.Column(db.Integer, primary_key=True)
    horse_id = db.Column(db.Integer, db.ForeignKey("horses.id"), nullable=False, index=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=False, index=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)          # مثال: "المركز الأول - بطولة القدرة والتحمل"
    competition_name = db.Column(db.String(200))
    achievement_date = db.Column(db.Date)
    description = db.Column(db.Text)
    photo_path = db.Column(db.String(300))

    status = db.Column(db.String(20), default=STATUS_PENDING, index=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    stable = db.relationship("Stable", foreign_keys=[stable_id])
    submitter = db.relationship("User", foreign_keys=[submitted_by])


class GalleryPhoto(db.Model):
    """صورة في معرض الصور العام الخاص بالمربط (مرافق، ساحات، فعاليات — وليست صور خيول فردية)."""
    __tablename__ = "gallery_photos"

    id = db.Column(db.Integer, primary_key=True)
    stable_id = db.Column(db.Integer, db.ForeignKey("stables.id"), nullable=False, index=True)
    photo_path = db.Column(db.String(300), nullable=False)
    caption = db.Column(db.String(200))
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
