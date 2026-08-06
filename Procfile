web: gunicorn wsgi:app --workers 3 --timeout 60 --log-file -
release: flask db upgrade
