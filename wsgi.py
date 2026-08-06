# -*- coding: utf-8 -*-
"""نقطة الدخول التي يستخدمها Gunicorn في الإنتاج: gunicorn wsgi:app"""
from app import app

if __name__ == "__main__":
    app.run()
