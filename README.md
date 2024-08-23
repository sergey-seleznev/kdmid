# kdmid

This tool monitors the Russian Federation Embassy appointment request queue status and notifies about its updates.

It uses EasyOCR for on-device captcha solving. In general, its catcha solve success rate is around 10-20%. However, only fully recognized 6-digit numbers are submitted, improving the success rate to approximately 40-60%. Using the default retry delay values, it is well enough to stay on the queue without being banned.  

## Quick start

Configure your embassy city, appointment number and security code in `.env`
```
EMBASSY_CITY="tallinn"
APPOINTMENT_NUMBER="00000"
SECURITY_CODE="1234ABCD"
```

Run the application in Docker
```
docker compose up -d
```

## Additional options

Configure [Telegram Bot API key and chat id](https://www.cytron.io/tutorial/how-to-create-a-telegram-bot-get-the-api-key-and-chat-id) to receive notifications to your Telegram immediately
```
TELEGRAM_API_KEY="***"
TELEGRAM_CHAT_ID="***"
```

Configure appointment alias to simplify tracking multiple appointments
```
APPOINTMENT_ALIAS="Ivan"
```

Adjust retry delays (in seconds)
```
DELAY_CAPTCHA_NOT_SOLVED=15
```
