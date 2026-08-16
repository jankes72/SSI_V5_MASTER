"""
SSI V5 - Startup Validator

Moduł sprawdzający gotowość środowiska SSI.

Odpowiada za:
- znalezienie głównego katalogu SSI
- sprawdzenie wymaganych katalogów
- sprawdzenie podstawowych plików
- zwrócenie statusu READY / FAILED

Nie uruchamia:
- Dyrektora
- Ollama
- agentów

Tylko sprawdza stan systemu.
"""


import os



# =====================================================
# GŁÓWNY KATALOG SSI
# =====================================================

SSI_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)



class StartupValidator:


    def __init__(self):

        self.status = "NOT_VALIDATED"

        self.results = {}



    # =====================================================
    # GŁÓWNA WALIDACJA
    # =====================================================

    def validate(self):

        print()

        print(
            "[VALIDATOR] Starting validation"
        )


        print()

        print(
            "[SSI ROOT]",
            SSI_ROOT
        )


        self.check_directories()


        self.check_files()



        if all(self.results.values()):

            self.status = "VALID"


        else:

            self.status = "FAILED"



        print()

        print(
            "[VALIDATOR STATUS]",
            self.status
        )



        return self.status == "VALID"



    # =====================================================
    # SPRAWDZENIE KATALOGÓW
    # =====================================================

    def check_directories(self):


        required_directories = [

            "DIRECTOR",

            "MEMORY",

            "CONTEXT",

            "SESSION",

            "CONFIG",

            "LOGS",

            "OLLAMA",

            "TESTS"

        ]



        for directory in required_directories:


            path = os.path.join(
                SSI_ROOT,
                directory
            )


            exists = os.path.isdir(path)



            self.results[
                f"DIR_{directory}"
            ] = exists



            print(

                "[DIR]",

                directory,

                "OK" if exists else "MISSING"

            )



    # =====================================================
    # SPRAWDZENIE PLIKÓW
    # =====================================================

    def check_files(self):


        required_files = [

            "start_ssi.py",

            "DIRECTOR/director_core.py"

        ]



        for file in required_files:


            path = os.path.join(
                SSI_ROOT,
                file
            )


            exists = os.path.isfile(path)



            self.results[
                f"FILE_{file}"
            ] = exists



            print(

                "[FILE]",

                file,

                "OK" if exists else "MISSING"

            )



    # =====================================================
    # POBRANIE RAPORTU
    # =====================================================

    def get_results(self):

        return self.results



    def get_status(self):

        return self.status




# =========================================================
# TEST MODUŁU
# =========================================================

if __name__ == "__main__":


    validator = StartupValidator()


    result = validator.validate()


    print()


    print(
        "VALIDATION RESULT:"
    )


    print(result)


    print()


    print(
        validator.get_results()
    )