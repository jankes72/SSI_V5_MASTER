"""
SSI V5 - Startup Manager

Główny koordynator procesu uruchomienia SSI.

Odpowiada za:

1. rozpoczęcie sesji startowej
2. synchronizację systemu
3. walidację środowiska
4. wygenerowanie raportu
5. zapis historii startu
6. przekazanie statusu READY

Nie wykonuje logiki Dyrektora.
Jest tylko warstwą startową.
"""


from datetime import datetime


from STARTUP.startup_synchronizer import StartupSynchronizer

from STARTUP.startup_validator import StartupValidator

from STARTUP.startup_report import StartupReport




class StartupManager:


    def __init__(self):


        self.status = "OFFLINE"

        self.start_time = None

        self.system_ready = False

        self.sync_data = {}

        self.validation_results = {}

        self.validation_status = False

        self.report_path = None




    # =====================================================
    # START PROCEDURY
    # =====================================================

    def start(self):


        print()

        print("=" * 60)
        print("SSI V5 STARTUP MANAGER")
        print("=" * 60)


        self.status = "STARTING"


        self.start_time = datetime.now()


        print(
            "[START TIME]",
            self.start_time
        )


        self.run_startup_sequence()




    # =====================================================
    # PEŁNA KOLEJNOŚĆ STARTU
    # =====================================================

    def run_startup_sequence(self):


        # -------------------------------------------------
        # 1. SYNCHRONIZACJA
        # -------------------------------------------------

        print()

        print(
            "[1] SYSTEM SYNCHRONIZATION"
        )


        synchronizer = StartupSynchronizer()


        self.sync_data = (
            synchronizer.synchronize()
        )




        # -------------------------------------------------
        # 2. WALIDACJA
        # -------------------------------------------------

        print()

        print(
            "[2] SYSTEM VALIDATION"
        )


        validator = StartupValidator()


        self.validation_status = (
            validator.validate()
        )


        self.validation_results = (
            validator.get_results()
        )




        # -------------------------------------------------
        # 3. RAPORT STARTOWY
        # -------------------------------------------------

        print()

        print(
            "[3] STARTUP REPORT"
        )


        report = StartupReport()


        report.create_report(

            self.sync_data,

            self.validation_status,

            self.validation_results

        )


        self.report_path = (
            report.save_report()
        )




        # -------------------------------------------------
        # 4. DECYZJA SYSTEMU
        # -------------------------------------------------

        print()

        print(
            "[4] STARTUP DECISION"
        )



        if self.validation_status:


            self.system_ready = True

            self.status = "READY"


            print(
                "[SYSTEM STATUS]",
                self.status
            )


        else:


            self.system_ready = False

            self.status = "FAILED"


            print(
                "[SYSTEM STATUS]",
                self.status
            )






    # =====================================================
    # CZY SYSTEM GOTOWY
    # =====================================================

    def is_ready(self):


        return self.system_ready




    # =====================================================
    # STATUS SYSTEMU
    # =====================================================

    def get_status(self):


        return {


            "status":

                self.status,


            "start_time":

                str(self.start_time),


            "ready":

                self.system_ready,


            "session":

                self.sync_data.get(
                    "session",
                    {}
                ),


            "report":

                self.report_path

        }





# =========================================================
# TEST MODUŁU
# =========================================================

if __name__ == "__main__":


    manager = StartupManager()


    manager.start()


    print()


    print(
        "STARTUP STATUS:"
    )


    print(
        manager.get_status()
    )