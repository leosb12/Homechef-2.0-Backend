import os
import firebase_admin
from firebase_admin import credentials, messaging

class PushNotificationService:
    _initialized = False

    @classmethod
    def _initialize(cls):
        if cls._initialized:
            return

        if firebase_admin._apps:
            cls._initialized = True
            return

        private_key = os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
        
        if not private_key:
            print("Firebase is not configured correctly.")
            cls._initialized = True
            return

        cert = {
            "type": "service_account",
            "project_id": os.getenv("FIREBASE_PROJECT_ID", ""),
            "private_key_id": "",
            "private_key": private_key,
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL", ""),
            "client_id": "",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{os.getenv('FIREBASE_CLIENT_EMAIL', '').replace('@', '%40')}"
        }

        try:
            cred = credentials.Certificate(cert)
            firebase_admin.initialize_app(cred)
            cls._initialized = True
        except Exception as e:
            print(f"Error initializing Firebase: {e}")
            cls._initialized = True

    @classmethod
    def send_push_notification(cls, token, title, body, data=None):
        cls._initialize()
        if not firebase_admin._apps or not token:
            return False

        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=token,
        )

        try:
            response = messaging.send(message)
            print(f"Successfully sent message: {response}")
            return True
        except Exception as e:
            print(f"Error sending push notification: {e}")
            return False
