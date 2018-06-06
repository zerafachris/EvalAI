#!/bin/sh
cd /code && \
python manage.py migrate --noinput && \
python manage.py seed && \
python -u manage.py runserver 0.0.0.0:8000
