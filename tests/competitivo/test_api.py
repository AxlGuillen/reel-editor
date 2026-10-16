"""Tests de la sincronía (estimar_desfase) y de la validación del módulo competitivo (sin internet)."""
import unittest

from modules.competitivo import layout as L
from modules.competitivo.schema import CompetitivoParams, FinishParams

IDS = "115660588583171787 115660588583171788"
VOD = "https://www.youtube.com/watch?v=8ci8rGBi4lU"


class Desfase(unittest.TestCase):
    # Kills del feed (segundo con la fórmula, lado) y lo que vio el video.
    ESPERADOS = [(16.23, "der"), (18.23, "der"), (22.53, "der"), (-3.25, "der")]

    def test_alinea_el_patron_de_kills(self):
        # Medido en el cierre de la G1: el video iba ~7.5 s más tarde, y el
        # 8→9 (cambio chico) no se vio.
        vistos = [(4.3, "der"), (23.5, "der"), (30.3, "der")]
        d, n = L.estimar_desfase(vistos, self.ESPERADOS)
        self.assertEqual(n, 3)
        self.assertAlmostEqual(d, 7.5, delta=0.3)

    def test_el_lado_importa(self):
        vistos = [(23.5, "izq"), (30.3, "izq")]
        self.assertEqual(L.estimar_desfase(vistos, self.ESPERADOS), (0.0, 0))

    def test_respeta_el_rango(self):
        # Corridas 40 s: dentro de ±25 s solo hay coincidencias sueltas (por
        # casualidad); por eso la confianza alta pide 2 o más.
        vistos = [(t + 40, lado) for t, lado in self.ESPERADOS]
        self.assertLessEqual(L.estimar_desfase(vistos, self.ESPERADOS, rango=25)[1], 1)
        d, n = L.estimar_desfase(vistos, self.ESPERADOS, rango=45)
        self.assertEqual(n, 4)
        self.assertAlmostEqual(d, 40, delta=0.01)

    def test_sin_datos(self):
        self.assertEqual(L.estimar_desfase([], self.ESPERADOS), (0.0, 0))
        self.assertEqual(L.estimar_desfase([(1.0, "izq")], []), (0.0, 0))


class Validacion(unittest.TestCase):
    def test_defaults(self):
        p = CompetitivoParams.from_form({"game_ids": IDS, "vod_url": VOD})
        self.assertEqual(p.game_ids, IDS.split())
        self.assertEqual((p.modo, p.duracion_max_s, p.max_por_partida, p.min_kills), ("revisar", 60, 3, 2))
        self.assertEqual((p.zoom, p.color, p.watermark), (1.3, "vivo", ""))
        self.assertFalse(p.incluir_picks or p.incluir_primera_sangre)
        self.assertEqual(p.reglas.duracion_max_s, 60)

    def test_errores_legibles(self):
        casos = [
            {"vod_url": VOD},                                            # sin gameId
            {"game_ids": "123", "vod_url": VOD},                         # gameId corto
            {"game_ids": "115660588583171787 115660588583171787", "vod_url": VOD},   # repetido
            {"game_ids": IDS},                                           # sin VOD
            {"game_ids": IDS, "vod_url": VOD, "modo": "x"},
            {"game_ids": IDS, "vod_url": VOD, "zoom": "3"},
            {"game_ids": IDS, "vod_url": VOD, "duracion_max_s": "10"},
            {"game_ids": IDS, "vod_url": VOD, "color": "neon"},
            {"game_ids": IDS, "vod_url": VOD, "watermark": "no-existe.png"},
        ]
        for form in casos:
            with self.assertRaises(ValueError, msg=form):
                CompetitivoParams.from_form(form)

    def test_finish(self):
        base = CompetitivoParams.from_form({"game_ids": IDS, "vod_url": VOD, "zoom": "1.4"})
        fp = FinishParams.from_form({"anchors": '{"115660588583171787": "1:22:15"}',
                                     "clips": '["1-2", "2-5"]'}, base)
        self.assertEqual(fp.anclas, {"115660588583171787": 4935.0})
        self.assertEqual(fp.clips, ["1-2", "2-5"])
        self.assertEqual(fp.zoom, 1.4)                 # hereda el render de la fase 1
        self.assertIsNone(FinishParams.from_form({}, base).clips)
        for form in ({"anchors": '{"999999999999999999": 10}'}, {"anchors": "no-json"},
                     {"clips": "[]"}, {"clips": "[1, 2]"}):
            with self.assertRaises(ValueError, msg=form):
                FinishParams.from_form(form, base)


if __name__ == "__main__":
    unittest.main()
