# -*- coding: utf-8 -*-
"""
إرسال رسائل SMS — طبقة تجريدية فوق مزود خارجي.
==================================================

يدعم حاليًا:
  - "console"  → وضع تطوير: يطبع الكود بسجلات التطبيق بدل إرساله فعليًا (الافتراضي محليًا)
  - "msegat"   → مزود سعودي محلي (https://www.msegat.com)
  - "unifonic" → مزود سعودي/إقليمي (https://www.unifonic.com)

التبديل بين المزودين عبر متغير البيئة SMS_PROVIDER، بدون تغيير أي كود استدعاء.
كل الأسرار (مفاتيح API) تُقرأ من متغيرات البيئة فقط — لا تُكتب هنا مباشرة.
"""
import logging
import os

import requests

logger = logging.getLogger("sms")

SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "console").lower()
SMS_SENDER_NAME = os.environ.get("SMS_SENDER_NAME", "SaudiHorses")

MSEGAT_USERNAME = os.environ.get("MSEGAT_USERNAME")
MSEGAT_API_KEY = os.environ.get("MSEGAT_API_KEY")

UNIFONIC_APP_SID = os.environ.get("UNIFONIC_APP_SID")


class SmsSendError(Exception):
    """تُرفع عند فشل إرسال الرسالة فعليًا عبر المزود (شبكة، مفتاح خاطئ، رصيد منتهٍ...)."""
    pass


def send_otp_sms(phone: str, code: str) -> None:
    """يرسل كود التحقق لرقم الجوال المعطى (بصيغة +9665XXXXXXXX).
    يرفع SmsSendError عند الفشل — على المستدعي التعامل مع الاستثناء وعدم كشف تفاصيله للمستخدم."""
    message = f"كود التحقق الخاص بك في الخيول السعودية هو: {code}\nصالح لمدة 5 دقائق ولا تشاركه مع أحد."

    if SMS_PROVIDER == "console":
        # وضع التطوير فقط — لا يُستخدم أبدًا في الإنتاج (تحقق صريح أدناه في app.py)
        logger.info("[SMS-DEV] إلى %s: %s", phone, message)
        return

    if SMS_PROVIDER == "msegat":
        _send_via_msegat(phone, message)
    elif SMS_PROVIDER == "unifonic":
        _send_via_unifonic(phone, message)
    else:
        raise SmsSendError(f"مزود SMS غير مدعوم: {SMS_PROVIDER}")


def _send_via_msegat(phone: str, message: str) -> None:
    if not (MSEGAT_USERNAME and MSEGAT_API_KEY):
        raise SmsSendError("إعدادات Msegat غير مكتملة (MSEGAT_USERNAME / MSEGAT_API_KEY)")
    try:
        resp = requests.post(
            "https://www.msegat.com/gw/sendsms.php",
            json={
                "userName": MSEGAT_USERNAME,
                "apiKey": MSEGAT_API_KEY,
                "numbers": phone.lstrip("+"),
                "userSender": SMS_SENDER_NAME,
                "msg": message,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Msegat يرجّع code=1 عند النجاح
        if str(data.get("code")) != "1":
            raise SmsSendError(f"رفض Msegat الإرسال: {data}")
    except requests.RequestException as exc:
        raise SmsSendError(f"فشل الاتصال بـ Msegat: {exc}") from exc


def _send_via_unifonic(phone: str, message: str) -> None:
    if not UNIFONIC_APP_SID:
        raise SmsSendError("إعدادات Unifonic غير مكتملة (UNIFONIC_APP_SID)")
    try:
        resp = requests.post(
            "https://el.cloud.unifonic.com/rest/SMS/messages",
            data={
                "AppSid": UNIFONIC_APP_SID,
                "SenderID": SMS_SENDER_NAME,
                "Body": message,
                "Recipient": phone.lstrip("+"),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", False):
            raise SmsSendError(f"رفض Unifonic الإرسال: {data}")
    except requests.RequestException as exc:
        raise SmsSendError(f"فشل الاتصال بـ Unifonic: {exc}") from exc
