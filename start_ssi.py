"""
SSI V5 - SYSTEM START

Główny punkt wejścia systemu.

Odpowiada za:

- rozpoczęcie SSI
- uruchomienie Startup Manager
- przekazanie sterowania do Director Core

Nie wykonuje logiki systemu.
"""


import os
import sys




# =====================================================
# KATALOG GŁÓWNY SSI
# =====================================================


SSI_ROOT = os.path.dirname(

    os.path.abspath(__file__)

)


sys.path.append(

    SSI_ROOT

)




# =====================================================
# MODUŁ STARTUP
# =====================================================


from STARTUP.startup_manager import StartupManager
from ssi_v5.director.startup_bridge import DirectorStartupBridge





class SSI_System:



    def __init__(self):


        self.status = "OFFLINE"


        self.startup = None


        self.director = None






    # =====================================================
    # START SSI
    # =====================================================

    def start(self):


        print()


        print("=" * 60)
        print("SSI V5 SYSTEM START")
        print("=" * 60)



        self.status = "STARTING"



        print()

        print(

            "[SYSTEM STATUS]",

            self.status

        )





        # -----------------------------------------------
        # STARTUP MANAGER
        # -----------------------------------------------


        self.startup = StartupManager()


        self.startup.start()





        # -----------------------------------------------
        # SPRAWDZENIE GOTOWOŚCI
        # -----------------------------------------------


        if self.startup.is_ready():



            self.status = "READY"



            print()


            print(

                "[SSI STATUS]",

                self.status

            )



            self.launch_director()





        else:



            self.status = "FAILED"



            print()


            print(

                "[SSI STATUS]",

                self.status

            )







    # =====================================================
    # PRZEKAZANIE DO DYREKTORA
    # =====================================================

    def launch_director(self):


        print()


        print(

            "[NEXT] DIRECTOR CORE"

        )



        try:



            from DIRECTOR.director_core import DirectorCore



            self.director = DirectorCore()



            self.director.start()



            self.status = "RUNNING"



            print()


            print(

                "[SSI STATUS]",

                self.status

            )




        except Exception as error:



            self.status = "DIRECTOR FAILED"



            print()


            print(

                "[DIRECTOR ERROR]",

                error

            )








# =====================================================
# START PROGRAMU
# =====================================================



def build_ssi_v5_director_bridge(root=None):
    """Internal SSI V5 Director bridge. No public Director runtime."""
    from pathlib import Path
    return DirectorStartupBridge(Path(root or "dane/ssi_canonical"))


if __name__ == "__main__":



    system = SSI_System()



    system.start()