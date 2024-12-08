"""
Copyright (c) 2024 Baltic Marine Environment Protection Commission
Copyright (c) 2022 Antti-Jussi Kieloaho (Natural Resources Institute Finland)

LICENSE available under 
local: 'SOM/protect_baltic/LICENSE'
url: 'https://github.com/helcomsecretariat/SOM/blob/main/protect_baltic/LICENCE'
"""

# main package script

import som.som_app as som_app
from utilities import Timer, exception_traceback

def run():

    try:
        # Process survey data and read general input
        data = som_app.process_input_data()

        # Create links between core components
        links = som_app.build_links(data)

        # Create cases
        data['cases'] = som_app.build_cases(data['cases'], links)

        state_ges = som_app.simulate(data, links)

        print(state_ges['PR'])
    except Exception as e:
        exception_traceback(e)
    
    return

if __name__ == "__main__":
    run()

