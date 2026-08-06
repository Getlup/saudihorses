# -*- coding: utf-8 -*-
"""
قاموس ترجمة بسيط (عربي/إنجليزي) لنصوص الواجهة الأساسية.
هذه المرحلة الأولى: تغطي شريط التنقل، تسجيل الدخول/التسجيل، الصفحة الرئيسية، والتذييل.
باقي الصفحات (لوحات التحكم الداخلية) لا تزال بالعربي فقط حاليًا.
"""

TRANSLATIONS = {
    # شريط التنقل
    "nav.home": {"ar": "الرئيسية", "en": "Home"},
    "nav.platform_dashboard": {"ar": "لوحة المنصة", "en": "Platform Dashboard"},
    "nav.stable_dashboard": {"ar": "لوحة الإسطبل", "en": "Stable Dashboard"},
    "nav.my_horses": {"ar": "خيولي", "en": "My Horses"},
    "nav.book_session": {"ar": "حجز الحصص", "en": "Book a Session"},
    "nav.lounge": {"ar": "منصة الفخر", "en": "Pride Hub"},
    "nav.lounge_dashboard": {"ar": "لوحة منصة الفخر", "en": "Pride Hub Dashboard"},
    "nav.lounge_reviews": {"ar": "مراجعات منصة الفخر", "en": "Pride Hub Reviews"},
    "nav.login": {"ar": "تسجيل الدخول", "en": "Log In"},
    "nav.create_account": {"ar": "إنشاء حساب", "en": "Create Account"},
    "nav.profile": {"ar": "الملف الشخصي", "en": "Profile"},
    "nav.account_settings": {"ar": "إعدادات الحساب", "en": "Account Settings"},
    "nav.logout": {"ar": "تسجيل الخروج", "en": "Log Out"},

    # تسجيل الدخول
    "login.title": {"ar": "تسجيل الدخول", "en": "Log In"},
    "login.subtitle": {"ar": "أهلًا بعودتك إلى الخيول السعودية", "en": "Welcome back to Saudi Horses"},
    "login.username": {"ar": "اسم المستخدم", "en": "Username"},
    "login.password": {"ar": "كلمة المرور", "en": "Password"},
    "login.submit": {"ar": "دخول", "en": "Log In"},
    "login.no_account": {"ar": "ليس لديك حساب؟", "en": "Don't have an account?"},
    "login.explore": {"ar": "استكشف المرابط وأنشئ حسابك", "en": "Explore stables and sign up"},

    # التسجيل
    "register.title": {"ar": "إنشاء حساب زائر", "en": "Create a Visitor Account"},
    "register.name": {"ar": "الاسم الكامل", "en": "Full Name"},
    "register.email": {"ar": "البريد الإلكتروني", "en": "Email"},
    "register.phone": {"ar": "رقم الجوال", "en": "Phone Number"},
    "register.password": {"ar": "كلمة المرور", "en": "Password"},
    "register.terms_prefix": {"ar": "أوافق على", "en": "I agree to the"},
    "register.terms_link": {"ar": "الشروط والأحكام", "en": "Terms & Conditions"},
    "register.submit": {"ar": "إنشاء الحساب", "en": "Create Account"},
    "register.have_account": {"ar": "لديك حساب بالفعل؟", "en": "Already have an account?"},

    # الرئيسية
    "home.tagline": {"ar": "منصّة فروسية سعودية", "en": "A Saudi Equestrian Platform"},
    "home.explore_cta": {"ar": "اختر مربطًا لتتصفح خيوله وتحجز حصة ركوب أو تدريب.",
                          "en": "Choose a stable to browse its horses and book a riding or training session."},
    "home.book_now": {"ar": "احجز حصتك الآن", "en": "Book Your Session Now"},
    "home.view_packages": {"ar": "استعرض الباقات", "en": "View Packages"},

    # التذييل
    "footer.tagline": {"ar": "منصّة الخيول السعودية — لإدارة الإسطبلات وتنظيم خدمات الفروسية",
                        "en": "Saudi Horses Platform — Stable Management & Equestrian Services"},
    "footer.rights": {"ar": "جميع الحقوق محفوظة", "en": "All rights reserved"},
}


def translate(key, lang):
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    return entry.get(lang, entry.get("ar", key))
