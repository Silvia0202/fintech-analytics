# test_email.py
import os, smtplib, ssl
from email.message import EmailMessage
from dotenv import load_dotenv

# Carga .env y fuerza a sobrescribir variables antiguas del entorno
load_dotenv(override=True)

USER = os.getenv("SMTP_USER")
PASS = os.getenv("SMTP_PASS")
HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")

def send_test_email():
    msg = EmailMessage()
    msg["From"] = USER
    msg["To"] = USER  # te lo envías a ti misma
    msg["Subject"] = "✅ Prueba SMTP (STARTTLS 587)"
    msg.set_content("Hola! Si lees esto, Gmail con App Password funcionó desde Python.")

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(HOST, 587) as s:
            s.set_debuglevel(1)   # imprime diálogo SMTP para depurar
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(USER, PASS)
            s.send_message(msg)
        print("✅ Email enviado con éxito (STARTTLS 587)")
    except Exception as e:
        print("❌ Error enviando email:", e)

if __name__ == "__main__":
    print("USER:", USER)
    print("PASS length:", len(PASS) if PASS else 0)
    send_test_email()
