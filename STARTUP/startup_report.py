"""
SSI V5 - Startup Report

Tworzenie raportu uruchomienia systemu.

Odpowiada za:
- zebranie danych startowych
- zapis historii uruchomień
- przygotowanie informacji dla kolejnych modułów

Nie podejmuje decyzji.
Nie steruje Dyrektorem.
"""


import os
import json
from datetime import datetime



# =====================================================
# KATALOG GŁÓWNY SSI
# =====================================================

SSI_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


LOG_DIR = os.path.join(
    SSI_ROOT,
    "LOGS"
)



class StartupReport:


    def __init__(self):

        self.report = {}



    # =====================================================
    # TWORZENIE RAPORTU
    # =====================================================

    def create_report(
        self,
        sync_data,
        validation_status,
        validation_results
    ):


        now = datetime.now()


        self.report = {


            "system":
                "SSI V5",


            "timestamp":
                str(now),


            "session":
                sync_data.get(
                    "session",
                    {}
                ),


            "system_time":
                sync_data.get(
                    "system_time"
                ),


            "synchronization":
                "OK",


            "validation":
            {

                "status":
                    validation_status,

                "details":
                    validation_results

            },


            "status":
                "READY"
                if validation_status
                else
                "FAILED"


        }


        return self.report



    # =====================================================
    # ZAPIS RAPORTU
    # =====================================================

    def save_report(self):


        if not os.path.exists(LOG_DIR):

            os.makedirs(
                LOG_DIR
            )



        filename = (
            "startup_report_"
            +
            datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            +
            ".json"
        )


        path = os.path.join(
            LOG_DIR,
            filename
        )



        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:


            json.dump(

                self.report,

                file,

                indent=4,

                ensure_ascii=False

            )



        print()

        print(
            "[REPORT SAVED]",
            path
        )


        return path



    # =====================================================
    # ODCZYT RAPORTU
    # =====================================================

    def get_report(self):

        return self.report




# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":


    test_sync = {


        "system_time":
            str(datetime.now()),


        "session":
        {

            "id":
                "TEST_SESSION",

            "runtime_hours":
                5

        }

    }



    test_validation = True


    test_results = {


        "DIRECTOR":
            True,

        "MEMORY":
            True

    }



    report = StartupReport()



    data = report.create_report(

        test_sync,

        test_validation,

        test_results

    )



    print()

    print(
        "STARTUP REPORT:"
    )


    print(
        json.dumps(
            data,
            indent=4,
            ensure_ascii=False
        )
    )


    report.save_report()