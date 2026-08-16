"""
SSI V5 - Startup Synchronizer

Odpowiada za przygotowanie informacji startowej systemu.

Funkcje:
- pobranie aktualnego czasu
- utworzenie informacji o sesji
- określenie stanu startowego
- przekazanie danych dalej

Nie wykonuje decyzji.
Nie uruchamia modelu.
Nie steruje Dyrektorem.
"""

from datetime import datetime


class StartupSynchronizer:


    def __init__(self):

        self.sync_data = {}

        self.status = "NOT_SYNCHRONIZED"



    # =====================================================
    # GŁÓWNA SYNCHRONIZACJA
    # =====================================================

    def synchronize(self):

        print()

        print(
            "[SYNC] Starting system synchronization"
        )


        self.get_system_time()


        self.create_session_data()


        self.status = "SYNCHRONIZED"


        print(
            "[SYNC STATUS]",
            self.status
        )


        return self.sync_data



    # =====================================================
    # CZAS SYSTEMOWY
    # =====================================================

    def get_system_time(self):


        now = datetime.now()


        self.sync_data["system_time"] = (
            str(now)
        )


        self.sync_data["date"] = (
            now.strftime("%Y-%m-%d")
        )


        self.sync_data["time"] = (
            now.strftime("%H:%M:%S")
        )


        self.sync_data["hour"] = (
            now.hour
        )


        print()

        print(
            "[SYSTEM TIME]",
            self.sync_data["system_time"]
        )



    # =====================================================
    # DANE SESJI
    # =====================================================

    def create_session_data(self):


        now = datetime.now()


        session_id = (
            now.strftime("%Y%m%d_%H%M%S")
        )


        self.sync_data["session"] = {

            "id": session_id,

            "start_time":
                self.sync_data["system_time"],

            "runtime_hours":
                5,

            "status":
                "starting"

        }


        print(
            "[SESSION]",
            session_id
        )



    # =====================================================
    # POBRANIE STANU
    # =====================================================

    def get_sync_data(self):

        return self.sync_data



# =========================================================
# TEST MODUŁU
# =========================================================

if __name__ == "__main__":


    synchronizer = StartupSynchronizer()


    data = synchronizer.synchronize()


    print()

    print(
        "SYNC DATA:"
    )


    print(data)