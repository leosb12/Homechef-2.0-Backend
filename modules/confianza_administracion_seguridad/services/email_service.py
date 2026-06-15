import os
import json
import requests
import threading
import logging

logger = logging.getLogger(__name__)

def _send_email_async(to_email: str, subject: str, html_content: str):
    """
    Funcion interna que se ejecuta en el hilo secundario para evitar bloquear.
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        logger.warning("SENDGRID_API_KEY no configurada. No se enviará el correo.")
        return

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Payload basico para SendGrid
    payload = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "subject": subject
            }
        ],
        "from": {"email": os.environ.get("SENDGRID_FROM_EMAIL", "no-reply@homechef.app"), "name": "HomeChef"},
        "content": [
            {
                "type": "text/html",
                "value": html_content
            }
        ]
    }

    try:
        # Timeout corto para no dejar hilos colgados indefinidamente
        response = requests.post(url, headers=headers, json=payload, timeout=5.0)
        if response.status_code >= 400:
            logger.error(f"Error enviando correo SendGrid: {response.status_code} - {response.text}")
        else:
            logger.info(f"Correo enviado exitosamente a {to_email} (Status: {response.status_code})")
    except Exception as e:
        logger.error(f"Excepcion al enviar correo SendGrid a {to_email}: {str(e)}")


class EmailService:
    @staticmethod
    def send_low_stock_alert(chef_email: str, chef_name: str, item_name: str, current_stock: float, threshold: float):
        """
        Envía un correo de alerta de inventario bajo de forma asíncrona.
        """
        if not chef_email:
            return

        subject = f"⚠️ Alerta de Inventario: {item_name} está por agotarse"
        
        # HTML Content profesional alineado con la marca (Brand: #7c3aed, Bg: #f4f6fb)
        html_content = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Alerta de Inventario - HomeChef</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #f4f6fb; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #0f172a;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f6fb; padding: 40px 20px;">
                <tr>
                    <td align="center">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.05); max-width: 600px;">
                            <!-- Header -->
                            <tr>
                                <td style="background-color: #7c3aed; padding: 30px; text-align: center;">
                                    <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">HomeChef</h1>
                                </td>
                            </tr>
                            
                            <!-- Body -->
                            <tr>
                                <td style="padding: 40px 30px;">
                                    <h2 style="margin-top: 0; color: #1e293b; font-size: 22px;">Alerta de Inventario Bajo ⚠️</h2>
                                    <p style="color: #475569; font-size: 16px; line-height: 1.6; margin-bottom: 24px;">
                                        Hola <strong style="color: #0f172a;">{chef_name}</strong>,
                                    </p>
                                    <p style="color: #475569; font-size: 16px; line-height: 1.6; margin-bottom: 30px;">
                                        Te informamos que tu insumo <strong style="color: #7c3aed;">{item_name}</strong> ha bajado de su nivel mínimo permitido. Para evitar interrumpir la preparación de tus pedidos, te sugerimos reabastecerte pronto.
                                    </p>
                                    
                                    <!-- Stats Box -->
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f8fafc; border-radius: 12px; border: 1px solid #e2e8f0; margin-bottom: 30px;">
                                        <tr>
                                            <td width="50%" align="center" style="padding: 20px; border-right: 1px solid #e2e8f0;">
                                                <p style="margin: 0; font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Stock Actual</p>
                                                <p style="margin: 8px 0 0 0; font-size: 28px; color: #ef4444; font-weight: 700;">{current_stock}</p>
                                            </td>
                                            <td width="50%" align="center" style="padding: 20px;">
                                                <p style="margin: 0; font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Límite Configurado</p>
                                                <p style="margin: 8px 0 0 0; font-size: 28px; color: #0f172a; font-weight: 700;">{threshold}</p>
                                            </td>
                                        </tr>
                                    </table>
                                    
                                    <p style="color: #475569; font-size: 15px; line-height: 1.6; text-align: center; margin-bottom: 0;">
                                        Accede a tu panel de HomeChef para actualizar tu inventario una vez hayas adquirido más insumos.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td style="background-color: #f1f5f9; padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                                    <p style="color: #64748b; font-size: 13px; margin: 0;">
                                        © 2026 HomeChef. Todos los derechos reservados.<br>
                                        <span style="font-size: 11px;">Este es un correo automático, por favor no respondas a este mensaje.</span>
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        # Lanzar en un hilo para no bloquear la ejecución principal
        thread = threading.Thread(
            target=_send_email_async, 
            args=(chef_email, subject, html_content)
        )
        thread.start()

    @staticmethod
    def send_expiration_alert(chef_email: str, chef_name: str, item_name: str, expiration_date: str, days_left: int):
        """
        Envía un correo asíncrono avisando que un insumo va a caducar o ya caducó.
        days_left: 0 si caduca hoy, negativo si ya caducó, positivo si faltan dias.
        """
        if not chef_email:
            return

        if days_left < 0:
            status_msg = "ha caducado"
            subject = f"🚨 Alerta Crítica: {item_name} ha caducado"
            color = "#ef4444" # Rojo
        elif days_left == 0:
            status_msg = "caduca HOY"
            subject = f"⚠️ Alerta: {item_name} caduca HOY"
            color = "#f97316" # Naranja
        else:
            status_msg = f"caduca en {days_left} días"
            subject = f"Aviso de Vencimiento: {item_name} caducará pronto"
            color = "#f59e0b" # Amarillo oscuro

        html_content = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Alerta de Vencimiento - HomeChef</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #f4f6fb; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #0f172a;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f4f6fb; padding: 40px 20px;">
                <tr>
                    <td align="center">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.05); max-width: 600px;">
                            <tr>
                                <td style="background-color: {color}; padding: 30px; text-align: center;">
                                    <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px;">HomeChef</h1>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding: 40px 30px;">
                                    <h2 style="margin-top: 0; color: #1e293b; font-size: 22px;">Aviso de Vencimiento 📅</h2>
                                    <p style="color: #475569; font-size: 16px; line-height: 1.6; margin-bottom: 24px;">
                                        Hola <strong style="color: #0f172a;">{chef_name}</strong>,
                                    </p>
                                    <p style="color: #475569; font-size: 16px; line-height: 1.6; margin-bottom: 30px;">
                                        Te informamos que tu insumo <strong style="color: {color};">{item_name}</strong> <strong>{status_msg}</strong>.
                                    </p>
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f8fafc; border-radius: 12px; border: 1px solid #e2e8f0; margin-bottom: 30px;">
                                        <tr>
                                            <td align="center" style="padding: 20px;">
                                                <p style="margin: 0; font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;">Fecha de Vencimiento Registrada</p>
                                                <p style="margin: 8px 0 0 0; font-size: 28px; color: {color}; font-weight: 700;">{expiration_date}</p>
                                            </td>
                                        </tr>
                                    </table>
                                    <p style="color: #475569; font-size: 15px; line-height: 1.6; text-align: center; margin-bottom: 0;">
                                        Por favor, revisa tu inventario para descartar el insumo o renovarlo según sea necesario.
                                    </p>
                                </td>
                            </tr>
                            <tr>
                                <td style="background-color: #f1f5f9; padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                                    <p style="color: #64748b; font-size: 13px; margin: 0;">
                                        © 2026 HomeChef. Todos los derechos reservados.<br>
                                        <span style="font-size: 11px;">Este es un correo automático, por favor no respondas a este mensaje.</span>
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        thread = threading.Thread(
            target=_send_email_async, 
            args=(chef_email, subject, html_content)
        )
        thread.start()
