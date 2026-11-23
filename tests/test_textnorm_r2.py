"""R2A Spanish numeric reading: fixed examples, negative cases and idempotence."""

import pytest

from clipoteca.studio.textnorm import DEFAULT_LEXICON, apply_lexicon, spell_numbers


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Tiene 21 años y 31 perros", "Tiene veintiún años y treinta y un perros"),
        ("Pesa 1 kilo", "Pesa un kilo"),
        ("Hay 101 especies", "Hay ciento una especies"),
        ("31 personas", "treinta y una personas"),
        ("21 mujeres", "veintiuna mujeres"),
        ("21 horas", "veintiuna horas"),
        ("21 veces", "veintiuna veces"),
        ("21 semanas", "veintiuna semanas"),
        ("21 toneladas", "veintiuna toneladas"),
        ("21 millas", "veintiuna millas"),
        ("21 hectáreas", "veintiuna hectáreas"),
        ("21 libras", "veintiuna libras"),
        ("21 calorías", "veintiuna calorías"),
        ("21 páginas", "veintiuna páginas"),
        ("21 ciudades", "veintiuna ciudades"),
        ("21 niñas", "veintiuna niñas"),
        ("21 casas", "veintiuna casas"),
        ("21 cosas", "veintiuna cosas"),
        ("21 partes", "veintiuna partes"),
        ("21 vidas", "veintiuna vidas"),
        ("21 islas", "veintiuna islas"),
        ("21 lenguas", "veintiuna lenguas"),
        ("21 estrellas", "veintiuna estrellas"),
        ("21 galaxias", "veintiuna galaxias"),
        ("21 noches", "veintiuna noches"),
        ("31 mujeres", "treinta y una mujeres"),
        ("101 casos", "ciento un casos"),
        ("1001 años", "mil un años"),
        ("11 años", "once años"),
        ("1", "uno"),
        ("21", "veintiuno"),
        ("21,", "veintiuno,"),
        ("21. Y 31.", "veintiuno. Y treinta y uno."),
        ("el 1 de mayo", "el uno de mayo"),
        ("1.000.000", "un millón"),
        ("3,14159", "tres coma uno cuatro uno cinco nueve"),
        ("1,5", "uno coma cinco"),
        ("2,50", "dos coma cincuenta"),
        ("0,05", "cero coma cero cinco"),
        ("12.34", "doce coma treinta y cuatro"),
        ("14:30", "catorce treinta"),
        ("14:00", "catorce en punto"),
        ("14:05", "catorce cero cinco"),
        ("00:01", "cero cero uno"),
        ("23:59", "veintitrés cincuenta y nueve"),
        ("12/02/1995", "doce de febrero de mil novecientos noventa y cinco"),
        ("29/02/2024", "veintinueve de febrero de dos mil veinticuatro"),
        ("1/2", "un medio"),
        ("1/3", "un tercio"),
        ("1/4", "un cuarto"),
        ("3/4", "tres cuartos"),
        ("2/3", "dos sobre tres"),
        ("7/8", "siete sobre ocho"),
        ("-5 grados", "menos cinco grados"),
        ("Baja -21 grados", "Baja menos veintiún grados"),
        ("$1.500", "mil quinientos pesos"),
        ("US$20", "veinte dólares"),
        ("USD 20", "veinte dólares"),
        ("€20", "veinte euros"),
        ("20 €", "veinte euros"),
        ("USD 21", "veintiún dólares"),
        ("1er lugar", "primer lugar"),
        ("1º lugar", "primero lugar"),
        ("1ª mujer", "primera mujer"),
        ("2º lugar", "segundo lugar"),
        ("3ª vez", "tercera vez"),
        ("10º lugar", "décimo lugar"),
        ("10ª página", "décima página"),
        ("clip-000023", "clip-000023"),
        ("H264", "H264"),
        ("v2.0.6", "v2.0.6"),
        ("v2.0.6 y clip-000023", "v2.0.6 y clip-000023"),
        ("RT2/3", "RT2/3"),
        ("24:60", "24:60"),
        ("31/02/1995", "31/02/1995"),
        ("12/13/2026", "12/13/2026"),
        ("1/0", "1/0"),
        ("-x", "-x"),
        ("US$beta", "US$beta"),
        ("tres", "tres"),
        ("21!", "veintiuno!"),
        ("ABC123", "ABC123"),
        ("2026-11-23", "2026-11-23"),
    ],
)
def test_spoken_es_cases_and_idempotence(raw, expected):
    assert spell_numbers(raw) == expected
    assert spell_numbers(spell_numbers(raw)) == expected


def test_lexicon_defaults_no_auto_brands_and_custom_override():
    assert set(DEFAULT_LEXICON) == {"4x4", "km/h"}
    assert apply_lexicon("RS3 V6 DTM TDI") == "RS3 V6 DTM TDI"
    assert apply_lexicon("4x4 km/h") == "cuatro por cuatro kilometros por hora"
    assert apply_lexicon("V6 RS3", {"V6": "ve seis", "RS3": "erre ese tres"}) == (
        "ve seis erre ese tres"
    )


def test_other_languages_unchanged():
    for lang in ("en", "ru", "zh", "fr"):
        assert spell_numbers("21 personas a las 14:30", lang) == "21 personas a las 14:30"
