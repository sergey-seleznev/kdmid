FROM python:3-slim

ENV PYTHONUNBUFFERED=1

RUN pip install bs4 certifi easyocr requests
