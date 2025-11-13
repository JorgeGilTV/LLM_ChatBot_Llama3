import smtplib

try:
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    server.quit()
    print("Conexión exitosa al servidor SMTP de Gmail.")
except Exception as e:
    print("Error de conexión:", e)