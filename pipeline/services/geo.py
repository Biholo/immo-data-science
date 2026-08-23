"""
French administrative geography: department → region mapping, arrondissement
consolidation. Region codes are INSEE numeric codes matching geo.api.gouv.fr
(e.g. "11" = Île-de-France).
"""

# Paris/Lyon/Marseille arrondissements → commune principale.
ARR_TO_COMMUNE: dict[str, str] = {
    **{f"751{str(i).zfill(2)}": "75056" for i in range(1, 21)},    # Paris 75101-75120
    **{f"693{str(80+i).zfill(2)}": "69123" for i in range(1, 10)},  # Lyon 69381-69389
    **{f"13{str(200+i).zfill(3)}": "13055" for i in range(1, 17)},  # Marseille 13201-13216
}

DEPT_TO_REGION: dict[str, str] = {
    # Auvergne-Rhône-Alpes (84)
    "01": "84", "03": "84", "07": "84", "15": "84", "26": "84",
    "38": "84", "42": "84", "43": "84", "63": "84", "69": "84",
    "73": "84", "74": "84",
    # Bourgogne-Franche-Comté (27)
    "21": "27", "25": "27", "39": "27", "58": "27",
    "70": "27", "71": "27", "89": "27", "90": "27",
    # Bretagne (53)
    "22": "53", "29": "53", "35": "53", "56": "53",
    # Centre-Val de Loire (24)
    "18": "24", "28": "24", "36": "24", "37": "24", "41": "24", "45": "24",
    # Corse (94)
    "2A": "94", "2B": "94",
    # Grand Est (44)
    "08": "44", "10": "44", "51": "44", "52": "44", "54": "44",
    "55": "44", "57": "44", "67": "44", "68": "44", "88": "44",
    # Hauts-de-France (32)
    "02": "32", "59": "32", "60": "32", "62": "32", "80": "32",
    # Île-de-France (11)
    "75": "11", "77": "11", "78": "11", "91": "11",
    "92": "11", "93": "11", "94": "11", "95": "11",
    # Normandie (28)
    "14": "28", "27": "28", "50": "28", "61": "28", "76": "28",
    # Nouvelle-Aquitaine (75)
    "16": "75", "17": "75", "19": "75", "23": "75", "24": "75",
    "33": "75", "40": "75", "47": "75", "64": "75", "79": "75",
    "86": "75", "87": "75",
    # Occitanie (76)
    "09": "76", "11": "76", "12": "76", "30": "76", "31": "76",
    "32": "76", "34": "76", "46": "76", "48": "76", "65": "76",
    "66": "76", "81": "76", "82": "76",
    # Pays de la Loire (52)
    "44": "52", "49": "52", "53": "52", "72": "52", "85": "52",
    # Provence-Alpes-Côte d'Azur (93)
    "04": "93", "05": "93", "06": "93", "13": "93", "83": "93", "84": "93",
    # Overseas
    "971": "01", "972": "02", "973": "03", "974": "04", "976": "06",
}

REGION_NAMES: dict[str, str] = {
    "84": "Auvergne-Rhône-Alpes",
    "27": "Bourgogne-Franche-Comté",
    "53": "Bretagne",
    "24": "Centre-Val de Loire",
    "94": "Corse",
    "44": "Grand Est",
    "32": "Hauts-de-France",
    "11": "Île-de-France",
    "28": "Normandie",
    "75": "Nouvelle-Aquitaine",
    "76": "Occitanie",
    "52": "Pays de la Loire",
    "93": "Provence-Alpes-Côte d'Azur",
    "01": "Guadeloupe",
    "02": "Martinique",
    "03": "Guyane",
    "04": "La Réunion",
    "06": "Mayotte",
}
