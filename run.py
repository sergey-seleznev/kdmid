import io
import logging
import os
import time
import sys
import re
from enum import Enum
from dataclasses import dataclass
import easyocr
import requests
import certifi
from PIL import Image
import ssl
import urllib.request
import urllib.parse
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from typing import Dict, Optional

log = logging.getLogger()
logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')

class Result(str, Enum):
    REQUEST_NOT_CONFIRMED = 'request not confirmed over email'
    CAPTCHA_NOT_SOLVED = 'captcha not solved'
    CAPTCHA_SOLVED_INCORRECTLY = 'captcha solved incorrectly'
    NO_FREE_SLOTS = 'no free slots'
    HAS_FREE_SLOTS = 'free slots might be available'
    REQUEST_BLOCKED = 'request blocked'
    TECHNICAL_ERROR = 'technical error'

EMBASSY_CITY = os.environ.get("EMBASSY_CITY")
APPOINTMENT_ALIAS = os.environ.get("APPOINTMENT_ALIAS")
APPOINTMENT_NUMBER = os.environ.get("APPOINTMENT_NUMBER")
SECURITY_CODE = os.environ.get("SECURITY_CODE")

if not EMBASSY_CITY or not APPOINTMENT_NUMBER or not SECURITY_CODE:
    log.critical("EMBASSY_CITY, APPOINTMENT_NUMBER or SECURITY_CODE not set")
    sys.exit()

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"

DELAY = {
    Result.REQUEST_NOT_CONFIRMED: int(os.environ.get('DELAY_REQUEST_NOT_CONFIRMED', '600')), # 10m
    Result.CAPTCHA_NOT_SOLVED: int(os.environ.get('DELAY_CAPTCHA_NOT_SOLVED', '10')), # 10s
    Result.CAPTCHA_SOLVED_INCORRECTLY: int(os.environ.get('DELAY_CAPTCHA_SOLVED_INCORRECTLY', '60')), # 1m
    Result.NO_FREE_SLOTS: int(os.environ.get('DELAY_NO_FREE_SLOTS', '4500')), # 1h 15m
    Result.HAS_FREE_SLOTS: int(os.environ.get('DELAY_HAS_FREE_SLOTS', '7200')), # 2h
    Result.REQUEST_BLOCKED: int(os.environ.get('DELAY_REQUEST_BLOCKED', '86400')), # 1d
    Result.TECHNICAL_ERROR: int(os.environ.get('DELAY_TECHNICAL_ERROR', '600')), # 10m
}

TELEGRAM_API_KEY = os.environ.get('TELEGRAM_API_KEY')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

if not TELEGRAM_API_KEY or not TELEGRAM_CHAT_ID:
   log.warning("TELEGRAM_API_KEY or TELEGRAM_CHAT_ID not set, Telegram notifications won't be sent") 


# configure kdmid.ru-compatible SSL cipher, cookies and user-agent
cookies = urllib.request.HTTPCookieProcessor()
ssl._create_default_https_context = ssl._create_unverified_context
ssl_ctx = ssl.create_default_context(cafile=certifi.where())
ssl_ctx.set_ciphers('AES128-SHA')
opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl_ctx),
    urllib.request.HTTPRedirectHandler(),
    cookies,
)
opener.addheaders = [('User-agent', USER_AGENT)]

# initialize EasyOCR and download the required model files (once)
easyocr_reader = easyocr.Reader(["en"], model_storage_directory="/model")

# precompile position extraction regex
position_regex = re.compile(r"позиция в очереди - (?P<pos>\d+)")


# send HTTP requests using the configured web client
def http_req(url: str, form_data: Optional[Dict[str, str]] = None) -> bytes:
    data = urllib.parse.urlencode(form_data).encode() if form_data else None
    req = urllib.request.Request(url, data=data)
    return opener.open(req).read()


# solve capture given its relative URL
def solve_captcha(src: str) -> str:
    # fetch original image data
    img_bytes = http_req(f"https://{EMBASSY_CITY}.kdmid.ru/queue/{src}")

    # crop middle 200x200 from original 600x200
    img = Image.open(io.BytesIO(img_bytes)).crop((200, 0, 400, 200))

    # convert the cropped image to byte array
    img_bytes_io = io.BytesIO()
    img.save(img_bytes_io, format="JPEG")
    img_bytes = img_bytes_io.getvalue()

    # find and merge numbers
    return "".join(easyocr_reader.readtext(
        img_bytes, allowlist="0123456789", min_size=30, detail=0,
        decoder='wordbeamsearch', beamWidth=15, paragraph=True,
    ))


# get dictionary from <form> inputs
def get_form_data(soup: BeautifulSoup) -> Dict[str, str]:
    form = soup.find("form")
    form_data: Dict[str, str] = {}
    for input in form.find_all("input"):
        name: str = input.get("name")
        value: str = input.get("value")
        if name:
            form_data[name] = value
    return form_data


def attempt():
    # fetch and parse order page
    url = f"https://{EMBASSY_CITY}.kdmid.ru/queue/OrderInfo.aspx?id={APPOINTMENT_NUMBER}&cd={SECURITY_CODE}"
    soup = BeautifulSoup(http_req(url), "html.parser")

    # solve captcha
    captcha_src = soup.find(id="ctl00_MainContent_imgSecNum").get("src")
    captcha_text = solve_captcha(captcha_src)
    if len(captcha_text) < 6:
        return Result.CAPTCHA_NOT_SOLVED, None

    # submit the first form (request details and captcha)
    form = get_form_data(soup)
    form["ctl00$MainContent$txtID"] = APPOINTMENT_NUMBER
    form["ctl00$MainContent$txtUniqueID"] = SECURITY_CODE
    form["ctl00$MainContent$txtCode"] = captcha_text
    form["ctl00$MainContent$FeedbackClientID"] = "0"
    form["ctl00$MainContent$FeedbackOrderID"] = "0"
    response = http_req(url, form)
    if "Символы с картинки введены неправильно".encode("utf-8") in response:
        return Result.CAPTCHA_SOLVED_INCORRECTLY, None
    soup = BeautifulSoup(response, "html.parser")

    # submit the second form (confirm request)
    form = get_form_data(soup)
    form["ctl00$MainContent$ButtonB.x"] = "0"
    form["ctl00$MainContent$ButtonB.y"] = "0"
    soup = BeautifulSoup(http_req(url, form), "html.parser")
    message = soup.find(id="center-panel").get_text().lower()
    
    if "для подтверждения заявки воспользуйтесь ссылкой" in message:
        return Result.REQUEST_NOT_CONFIRMED, None

    if "нет свободного времени" in message:
        match = position_regex.search(message)
        return Result.NO_FREE_SLOTS, f"{match.group(1)} in queue" if match else None

    if "ваша заявка заблокирована" in message:
        return Result.REQUEST_BLOCKED, message
    
    return Result.HAS_FREE_SLOTS, message


def main(*args, **kwargs):
    prevDetails = None
    while True:
        try:
            result, details = attempt()
        except Exception as e:
            result = Result.TECHNICAL_ERROR
            details = str(e)

        # send notifications upon errors and position update
        notify = result in [
            Result.REQUEST_NOT_CONFIRMED,
            Result.HAS_FREE_SLOTS,
            Result.REQUEST_BLOCKED,
            Result.TECHNICAL_ERROR
        ]
        if result == Result.NO_FREE_SLOTS and details != prevDetails:
            notify = True
            prevDetails = details

        message = f"[{APPOINTMENT_ALIAS if APPOINTMENT_ALIAS else APPOINTMENT_NUMBER}]: {result.value}"
        if details:
            message += ": " + details

        log.info(message)
        if notify and TELEGRAM_API_KEY and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_API_KEY}/sendMessage?chat_id={TELEGRAM_CHAT_ID}&text={message}"
            requests.get(url)

        time.sleep(DELAY[result])

if __name__ == '__main__':
    sys.exit(main())
