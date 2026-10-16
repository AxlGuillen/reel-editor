"""Tests de modules/competitivo/kills.py (sin internet).

    venv\\Scripts\\python -m unittest discover -s tests -t .

El fixture es el timeline real de KC vs C9 G1 (15/10), compactado a un frame
cada ~9.5 s. Las peleas sintéticas cubren lo que el fixture no trae
(kills por jugador, remontadas, intercambios, fusión de ventanas).
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

from modules.competitivo import engine
from modules.competitivo import kills as K

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")


class FixtureKCvsC9(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.partida = K.partida_de_fixture(FIXTURE)
        cls.peleas = K.detectar_peleas(cls.partida)
        cls.clips = K.seleccionar([cls.peleas])

    def test_dos_clips_con_los_defaults(self):
        # Orden cronológico, no de puntaje.
        self.assertEqual([c.titulo for c in self.clips],
                         ["C9 · pelea 2-0 → barón · 25:40", "C9 · pelea 3-0 → nexo · 34:50"])
        total = sum(c.duracion for c in self.clips)
        self.assertLessEqual(total, 60)
        # La pelea final tiene kills en DOS frames (34:40 y 34:50), así que con
        # la ventana de 10 s + 5 s su clip dura ~35 s: 25 + 35 ≈ 60.
        self.assertAlmostEqual(total, 60, delta=1.5)

    def test_puntajes_del_brief(self):
        baron, cierre = self.clips
        # 60 + 15 + 30 + 14 (+ oro) → tope 100
        d = cierre.desglose
        self.assertEqual((d["kills"], d["ganador_3"], d["cierre"], d["minuto"]), (60, 15, 30, 14))
        self.assertEqual(cierre.puntaje, 100)
        self.assertTrue(cierre.cierra and cierre.forzada)
        # 40 + 5 + 10 = 55, más el bonus de oro (pedido aparte del brief)
        d = baron.desglose
        self.assertEqual((d["kills"], d["minuto"], d["objetivo"]), (40, 5, 10))
        self.assertEqual(baron.puntaje - d["oro"], 55)
        self.assertGreater(d["oro"], 0)

    def test_ventana_del_clip(self):
        baron, cierre = self.clips
        # La kill pasó entre el frame anterior y el que sube el contador:
        # inicio = frame anterior − pre_s (10); fin = última kill + post_s (5).
        self.assertEqual((K.Reglas().pre_s, K.Reglas().post_s), (10, 5))
        for c in self.clips:
            self.assertAlmostEqual((c.antes - c.clip_inicio).total_seconds(), 10, places=3)
            self.assertAlmostEqual((c.clip_fin - c.ultima).total_seconds(), 5, places=3)
            self.assertAlmostEqual((c.primera - c.antes).total_seconds(), 9.75, delta=0.75)
        self.assertAlmostEqual(baron.duracion, 25, delta=1)
        self.assertAlmostEqual(cierre.duracion, 35, delta=1)
        # con otra ventana, el clip cambia lo mismo
        reglas = K.Reglas(pre_s=8, post_s=3)
        otro = [p for p in K.detectar_peleas(self.partida, reglas) if p.reloj == "25:40"][0]
        self.assertAlmostEqual(otro.duracion, baron.duracion - 4, places=3)

    def test_cuenta_de_kills_igual_al_motor(self):
        # 15 eventos de kills: la primera sangre, 9 picks y 5 peleas de 2+.
        del_motor = [e for e in self.partida.eventos if e.tipo in ("kill", "pelea", "primera_sangre")]
        self.assertEqual(len(self.peleas), len(del_motor), 15)
        self.assertEqual(sum(1 for p in self.peleas if p.total == 1), 10)
        self.assertEqual(sum(1 for p in self.peleas if p.total >= 2), 5)
        self.assertEqual(sum(p.total for p in self.peleas), 21)   # 1 + 20

    def test_objetivos_nunca_generan_clip(self):
        # Todo candidato nace de kills; los objetivos sueltos (dragones de
        # 8:00 y 13:10, barón de 33:20) no caen dentro de ningún clip elegible.
        t0 = self.partida.t0
        for p in self.peleas:
            self.assertGreaterEqual(p.total, 1)
        for c in self.clips:
            self.assertGreaterEqual(c.total, 2)
        sueltos = [e for e in self.partida.eventos
                   if e.tipo in ("dragon", "baron") and e.reloj in ("8:00", "13:10", "33:20")]
        self.assertEqual(len(sueltos), 3)
        for e in sueltos:
            for p in self.peleas:
                if p.elegible:
                    self.assertFalse(p.clip_inicio <= e.ts <= p.clip_fin,
                                     f"{e.reloj} {e.tipo} dentro de {p.titulo} ({t0})")

    def test_un_tercero_no_cabe(self):
        reglas = K.Reglas(max_por_partida=5)
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual(len(clips), 2)   # el siguiente (24:30, ~21 s) pasaría de 60

    def test_picks_y_presupuesto(self):
        reglas = K.Reglas(incluir_picks=True, max_por_partida=5, duracion_max_s=180, pre_s=8, post_s=3)
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual(len(clips), 5)
        self.assertTrue(any(c.total == 1 for c in clips))
        self.assertLessEqual(sum(c.duracion for c in clips), 180)
        self.assertEqual(clips, sorted(clips, key=lambda c: c.clip_inicio))

    def test_primera_sangre_fuera_por_defecto(self):
        fb = self.peleas[0]
        self.assertTrue(fb.primera_sangre)
        self.assertFalse(fb.elegible)
        reglas = K.Reglas(incluir_primera_sangre=True)
        self.assertTrue(K.detectar_peleas(self.partida, reglas)[0].elegible)

    def test_el_cierre_de_la_serie_entra_siempre(self):
        reglas = K.Reglas(min_kills=5)   # ninguna pelea llega a 5 kills
        clips = K.seleccionar([K.detectar_peleas(self.partida, reglas)], reglas)
        self.assertEqual([c.reloj for c in clips], ["34:50"])

    def test_solo_la_ultima_pelea_cierra(self):
        self.assertEqual([p.reloj for p in self.peleas if p.cierra], ["34:50"])


# --- Partidas sintéticas -------------------------------------------------------
T0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
AZUL, ROJO = "KC", "C9"


def _partida(kills, oro_azul=0, minutos=30, fin=True):
    """Frames cada 1 s. `kills`: lista de (segundo, equipo, participantId).
    `oro_azul`: ventaja fija de oro del azul (cada kill suma 300 a su equipo)."""
    frames, por_jugador = [], []
    kb = kr = 0
    pk: dict[int, int] = {pid: 0 for pid in range(1, 11)}
    pendientes = sorted(kills)
    for s in range(minutos * 60 + 1):
        while pendientes and pendientes[0][0] <= s:
            _, equipo, pid = pendientes.pop(0)
            if equipo == AZUL:
                kb += 1
            else:
                kr += 1
            pk[pid] += 1
        oro = 2500 + 20 * s
        estado = "finished" if fin and s == minutos * 60 else "in_game"
        frames.append(engine.Frame(
            T0 + timedelta(seconds=s), estado,
            engine.TeamState(oro + oro_azul + 300 * kb, kb, 0, 0, 0, []),
            engine.TeamState(oro + 300 * kr, kr, 0, 0, 0, [])))
        por_jugador.append(dict(pk))
    nombres = {pid: f"{AZUL if pid <= 5 else ROJO} Jugador{pid}" for pid in range(1, 11)}
    return K.Partida("sintetica", AZUL, ROJO, frames, por_jugador, nombres)


class Sinteticas(unittest.TestCase):
    def test_triple_remontada_e_intercambio(self):
        # min 25: el azul va +3000; C9 (rojo) gana 3-1 con un triple de Jugador8.
        p = _partida([(1500, ROJO, 8), (1503, ROJO, 8), (1504, AZUL, 2), (1506, ROJO, 8)],
                     oro_azul=3000)
        pe = K.detectar_peleas(p)[0]
        self.assertEqual(pe.detalle, "pelea 3-1")
        self.assertEqual(pe.ganador, ROJO)
        self.assertEqual(pe.multikill, "triple de Jugador8")
        d = pe.desglose
        self.assertEqual((d["kills"], d["intercambio"], d["ganador_3"], d["multikill"], d["remontada"], d["minuto"]),
                         (80, 10, 15, 25, 15, 5))
        self.assertEqual(pe.puntaje, 100)
        self.assertIn("triple de Jugador8", pe.titulo)

    def test_sin_multikill_si_reparten_las_kills(self):
        p = _partida([(900, ROJO, 6), (903, ROJO, 7), (905, ROJO, 8)])
        pe = K.detectar_peleas(p)[0]
        self.assertIsNone(pe.multikill)
        self.assertEqual(pe.desglose["multikill"], 0)

    def test_peleas_cercanas_se_fusionan(self):
        # Kills a 16 s (> 15): dos peleas; pero sus clips quedan a < 6 s → una.
        p = _partida([(600, ROJO, 6), (602, ROJO, 7), (618, AZUL, 1), (619, AZUL, 2)])
        peleas = K.detectar_peleas(p)
        self.assertEqual(len(peleas), 1)
        self.assertEqual(peleas[0].detalle, "pelea 2-2")
        self.assertEqual(peleas[0].desglose["intercambio"], 10)

    def test_peleas_separadas_no_se_fusionan(self):
        p = _partida([(600, ROJO, 6), (602, ROJO, 7), (640, AZUL, 1), (641, AZUL, 2)])
        self.assertEqual(len(K.detectar_peleas(p)), 2)

    def test_objetivo_despues_de_la_pelea(self):
        p = _partida([(1500, ROJO, 6), (1502, ROJO, 7)])
        # Barón del rojo 20 s después de la última kill.
        for f in p.frames[1522:]:
            f.red.barons = 1
        p = K.Partida(p.game_id, p.azul, p.rojo, p.frames, p.kills_jugador, p.nombres)
        pe = K.detectar_peleas(p)[0]
        self.assertEqual(pe.objetivo, "barón")
        self.assertEqual(pe.desglose["objetivo"], 10)
        self.assertTrue(pe.titulo.startswith("C9 · pelea 2-0 → barón"))


if __name__ == "__main__":
    unittest.main()
