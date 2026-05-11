class RecoveryService:
    def request_recovery(self, email: str):
        raise NotImplementedError("La recuperacion de Contraseña se gestiona con Supabase Auth.")

    def confirm_recovery(self, token: str, new_password: str):
        raise NotImplementedError("La recuperacion de Contraseña se gestiona con Supabase Auth.")
